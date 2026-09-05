from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

from claude_drift.cli import main, run_replay
from claude_drift.store import latest_run


def assistant_tool(name: str, inp: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "model": "x",
                "content": [{"type": "tool_use", "name": name, "input": inp}],
                "usage": {"output_tokens": 1},
            },
        }
    )


class ScriptedRunner:
    """Answers by model: returns Read for the new model, Bash for the old model."""

    def __init__(self, fail_models: set[str] | None = None) -> None:
        self.argvs: list[list[str]] = []
        self.fail_models = fail_models or set()

    @contextmanager
    def stream(self, argv: list[str], cwd: str, timeout: float) -> Iterator[Iterator[str]]:
        self.argvs.append(argv)
        model = argv[argv.index("--model") + 1]
        if model in self.fail_models:
            yield iter([json.dumps({"type": "result", "is_error": True, "subtype": "error"})])
            return
        line = (
            assistant_tool("Read", {"file_path": "/r/a"})
            if model == "new"
            else assistant_tool("Bash", {"command": "ls"})
        )
        yield iter([line, json.dumps({"type": "result"})])


def test_run_replay_writes_run_with_noise(projects_dir: Path, drift_home: Path) -> None:
    runner = ScriptedRunner()
    run = run_replay(
        from_model="opus-5",
        to_model="new",
        turns=10,
        workers=2,
        noise=True,
        project=None,
        seed=0,
        timeout=1.0,
        runner=runner,
        echo=lambda s: None,
    )
    assert run.read_manifest()["status"] == "done"
    cuts = run.read_cuts()
    assert len(cuts) == 3
    replays = run.read_replays()
    assert len(replays) == 6
    assert {r.role for r in replays} == {"candidate", "noise"}
    assert all(r.signature is not None for r in replays)
    models = {argv[argv.index("--model") + 1] for argv in runner.argvs}
    assert models == {"new", "claude-opus-5"}  # --from resolved to the full recorded name


def test_run_replay_no_noise(projects_dir: Path, drift_home: Path) -> None:
    run = run_replay(
        from_model="claude-opus-5",
        to_model="new",
        turns=10,
        workers=1,
        noise=False,
        project=None,
        seed=0,
        timeout=1.0,
        runner=ScriptedRunner(),
        echo=lambda s: None,
    )
    assert {r.role for r in run.read_replays()} == {"candidate"}
    assert run.read_manifest()["noise"] is False


def test_run_replay_aborts_on_error_rate(projects_dir: Path, drift_home: Path) -> None:
    # every replay fails -> after >=5 completed with 100% errors the run is aborted
    run = run_replay(
        from_model="opus-5",
        to_model="new",
        turns=10,
        workers=1,
        noise=True,
        project=None,
        seed=0,
        timeout=1.0,
        runner=ScriptedRunner(fail_models={"new", "claude-opus-5"}),
        echo=lambda s: None,
    )
    m = run.read_manifest()
    assert m["status"] == "aborted"
    assert m["errors"] >= 5


def test_cli_replay_dry_estimate_and_yes(projects_dir: Path, drift_home: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claude_drift.cli as cli

    monkeypatch.setattr(cli, "SubprocessRunner", ScriptedRunner)
    result = CliRunner().invoke(
        main, ["replay", "--from", "opus-5", "--to", "new", "--turns", "2", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "estimated input tokens" in result.output
    assert "run id:" in result.output
    lr = latest_run()
    assert lr is not None and len(lr.read_cuts()) == 2


def test_cli_replay_ambiguous_from(projects_dir: Path, drift_home: Path) -> None:
    slug = projects_dir / "-fake-project"
    text = (
        (slug / "alpha.jsonl")
        .read_text()
        .replace("claude-opus-5", "claude-opus-4-9")
        .replace('"alpha"', '"delta"')
    )
    (slug / "delta.jsonl").write_text(text)
    result = CliRunner().invoke(main, ["replay", "--from", "opus", "--to", "new", "--yes"])
    assert result.exit_code != 0
    assert "matches more than one" in result.output
