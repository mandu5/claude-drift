from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

from claude_drift.cli import main, run_replay
from claude_drift.report import build_report, render
from claude_drift.stats import DriftStats, Interval, Transition


def stats_fixture() -> DriftStats:
    return DriftStats(
        cuts=60,
        sessions=30,
        candidate_agreement=0.81,
        noise_agreement=0.83,
        noise_band=Interval(0.76, 0.88),
        transitions=[
            Transition(
                "Bash/local-read", "Read/local-read", 23, 2, 21, Interval(15, 27), True, False
            ),
            Transition(
                "Write/local-write", "Read/local-read", 7, 0, 7, Interval(3, 11), True, True
            ),
            Transition(
                "Bash/local-read", "Grep/local-read", 3, 2, 1, Interval(-2, 4), False, False
            ),
        ],
        errors=1,
    )


def test_render_text() -> None:
    out = render(stats_fixture(), {"from": "opus-5", "to": "fable-5.1"})
    assert "model-drift report  opus-5 -> fable-5.1" in out
    assert "sessions replayed: 30   turns sampled: 60   errors: 1" in out
    assert "next-action agreement: 81%  (noise band 76%-88%)" in out
    assert "REAL changes (outside noise band, Bonferroni-corrected over 3 transitions)" in out
    assert "1. Bash/local-read -> Read/local-read" in out and "+23 turns" in out
    assert "2. Write/local-write -> Read/local-read" in out and "!!" in out
    assert "NOISE (within band, ignore)" in out
    assert "- Bash/local-read -> Grep/local-read" in out
    assert out.index("REAL") < out.index("NOISE")


def test_render_md_has_table_and_ci() -> None:
    out = render(stats_fixture(), {"from": "a", "to": "b"}, fmt="md")
    assert out.startswith("# model-drift report")
    assert "| # | transition | candidate turns | noise turns | delta 95% CI | flag |" in out
    assert "| 1 | Bash/local-read -> Read/local-read | 23 | 2 | 21 [15, 27] |  |" in out
    assert "| 2 | Write/local-write -> Read/local-read | 7 | 0 | 7 [3, 11] | !! |" in out


def test_render_without_noise_and_empty() -> None:
    s = DriftStats(5, 2, 1.0, None, None, [], 0)
    out = render(s, {"from": "a", "to": "b"})
    assert "(noise band not measured)" in out
    assert "(none)" in out


def assistant_tool(name: str, inp: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "model": "x",
                "content": [{"type": "tool_use", "name": name, "input": inp}],
                "usage": {},
            },
        }
    )


class InjectableRunner:
    """Construct-validity double: the answer for the candidate model is injectable."""

    def __init__(self, candidate_answer: str) -> None:
        self.candidate_answer = candidate_answer

    @contextmanager
    def stream(self, argv: list[str], cwd: str, timeout: float) -> Iterator[Iterator[str]]:
        model = argv[argv.index("--model") + 1]
        if model == "new":
            line = self.candidate_answer
        else:
            line = assistant_tool("Bash", {"command": "ls -la"})
        yield iter([line, json.dumps({"type": "result"})])


def test_construct_validity_report_changes_when_candidate_answer_changes(
    projects_dir: Path, drift_home: Path
) -> None:
    """RGI-ARC lesson: the report must respond to what the model actually answered."""
    run_a = run_replay(
        from_model="opus-5",
        to_model="new",
        turns=10,
        workers=1,
        noise=True,
        project=None,
        seed=0,
        timeout=1.0,
        runner=InjectableRunner(assistant_tool("Bash", {"command": "ls -la"})),
        echo=lambda s: None,
    )
    run_b = run_replay(
        from_model="opus-5",
        to_model="new",
        turns=10,
        workers=1,
        noise=True,
        project=None,
        seed=0,
        timeout=1.0,
        runner=InjectableRunner(assistant_tool("WebFetch", {"url": "https://x"})),
        echo=lambda s: None,
    )
    a = build_report(run_a)
    b = build_report(run_b)
    assert a != b
    assert "WebFetch/remote" in b and "WebFetch/remote" not in a
    assert run_b.report_path.exists()


def test_cli_report_latest_and_named(projects_dir: Path, drift_home: Path) -> None:
    run = run_replay(
        from_model="opus-5",
        to_model="new",
        turns=10,
        workers=1,
        noise=False,
        project=None,
        seed=0,
        timeout=1.0,
        runner=InjectableRunner(assistant_tool("Read", {"file_path": "/r/a"})),
        echo=lambda s: None,
    )
    r1 = CliRunner().invoke(main, ["report"])
    assert r1.exit_code == 0, r1.output
    assert "model-drift report" in r1.output
    r2 = CliRunner().invoke(main, ["report", "--run", run.path.name, "--format", "md"])
    assert r2.exit_code == 0 and r2.output.startswith("# model-drift report")
    r3 = CliRunner().invoke(main, ["report", "--run", "nope"])
    assert r3.exit_code != 0
