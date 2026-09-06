from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
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
            Transition(
                "Read/local-read", "Bash/local-read", 2, 8, -6, Interval(-9, -3), True, False
            ),
        ],
        errors=1,
    )


def test_render_text() -> None:
    out = render(stats_fixture(), {"from": "opus-5", "to": "fable-5.1"})
    assert "model-drift report  opus-5 -> fable-5.1" in out
    assert "sessions replayed: 30   turns sampled: 60   errors: 1" in out
    assert "next-action agreement: 81%  (noise band 76%-88%)" in out
    assert "old-vs-record agreement: 83%" in out
    assert "noise column = one old-model draw per turn (first successful attempt)" in out
    assert "self-agreement" not in out  # not measured with --self-replays 1
    assert "verdict: no detectable drift (candidate agreement inside noise band)" in out
    assert "agreement by level: tool 0% / target 81% / full 0%" in out
    assert (
        "REAL changes (outside noise band, Bonferroni-corrected over 4 transitions, "
        "alpha=0.05/4)" in out
    )
    assert "1. Bash/local-read -> Read/local-read" in out and "+21 turns" in out
    assert "2. Write/local-write -> Read/local-read" in out and "+7 turns" in out and "!!" in out
    assert "NOISE (within band, ignore)" in out
    assert "- Bash/local-read -> Grep/local-read" in out and "+1 turns" in out
    assert out.index("REAL") < out.index("NOISE")
    # a negative delta keeps its sign and is still a REAL change
    assert "3. Read/local-read -> Bash/local-read" in out and "-6 turns" in out
    assert out.index("REAL") < out.index("-6 turns") < out.index("NOISE")


def test_render_md_has_table_and_ci() -> None:
    out = render(stats_fixture(), {"from": "a", "to": "b"}, fmt="md")
    assert out.startswith("# model-drift report")
    assert (
        "| # | transition | candidate turns | noise turns | delta CI (alpha=0.05/k) | flag |" in out
    )
    assert "| 1 | Bash/local-read -> Read/local-read | 23 | 2 | 21 [15, 27] |  |" in out
    assert "| 2 | Write/local-write -> Read/local-read | 7 | 0 | 7 [3, 11] | !! |" in out


def test_render_self_agreement_and_interpretation() -> None:
    base = stats_fixture()
    # the old model agrees with the record only 19% of the time but reproduces its own
    # replay 62% of the time: most of that gap is replay-vs-interactive, not sampling
    high = replace(
        base,
        noise_agreement=0.19,
        self_replays=3,
        self_agreement=0.62,
        self_band=Interval(0.55, 0.70),
    )
    out = render(high, {"from": "a", "to": "b"})
    assert "old-vs-old self-agreement (k=3 measured): 62%  (band 55%-70%)" in out
    assert (
        "interpretation: the old model mostly reproduces itself; the gap to the record "
        "is replay-vs-interactive mismatch, not model instability" in out
    )
    assert "old-vs-old self-agreement (k=3 measured): 62%" in render(high, {}, fmt="md")

    # self-agreement is close to agreement with the record, and both are low
    flat = replace(
        base,
        noise_agreement=0.19,
        self_replays=3,
        self_agreement=0.25,
        self_band=Interval(0.2, 0.3),
    )
    assert (
        "interpretation: the old model does not reproduce itself either; the turns "
        "themselves are unstable" in render(flat, {"from": "a", "to": "b"})
    )

    # self-agreement is high in absolute terms and close to agreement with the record
    comparable = replace(
        base,
        noise_agreement=0.83,
        self_replays=3,
        self_agreement=0.85,
        self_band=Interval(0.8, 0.9),
    )
    assert (
        "interpretation: self-agreement and agreement with the record are comparable "
        "and both high; the model is stable on these turns"
        in render(comparable, {"from": "a", "to": "b"})
    )

    # self-agreement is not low, but it is well below agreement with the record
    low = replace(
        base,
        noise_agreement=0.90,
        self_replays=3,
        self_agreement=0.55,
        self_band=Interval(0.45, 0.65),
    )
    assert (
        "interpretation: self-agreement is lower than agreement with the record; "
        "inspect the run" in render(low, {"from": "a", "to": "b"})
    )


def test_verdict_above_the_band_names_the_record() -> None:
    s = replace(stats_fixture(), candidate_agreement=0.95)
    out = render(s, {"from": "a", "to": "b"})
    assert (
        "verdict: candidate agrees with the record MORE than the old model does "
        "(above noise band)" in out
    )


def test_render_without_noise_and_empty() -> None:
    s = DriftStats(5, 2, 1.0, None, None, [], 0)
    out = render(s, {"from": "a", "to": "b"})
    assert "(noise band not measured)" in out
    assert "old-vs-record agreement: not measured" in out
    assert "verdict: not measured (run without --no-noise to get a verdict)" in out
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
