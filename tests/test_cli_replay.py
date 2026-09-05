from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
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


def pretend_claude_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `replay` command refuses to start without a claude binary; tests never run one."""
    import claude_drift.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/claude")


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
    manifest = run.read_manifest()
    assert manifest["status"] == "done"
    assert isinstance(manifest["claude_version"], str) and manifest["claude_version"]
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

    pretend_claude_installed(monkeypatch)
    monkeypatch.setattr(cli, "SubprocessRunner", ScriptedRunner)
    result = CliRunner().invoke(
        main, ["replay", "--from", "opus-5", "--to", "new", "--turns", "2", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "estimated input tokens" in result.output
    assert "run id:" in result.output
    # the run-level count of failed replays, distinct from the report's `errors:` line
    assert "failed replays: 0" in result.output
    lr = latest_run()
    assert lr is not None and len(lr.read_cuts()) == 2


def test_cli_replay_requires_the_claude_binary(
    projects_dir: Path, drift_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import claude_drift.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    result = CliRunner().invoke(main, ["replay", "--from", "opus-5", "--to", "new", "--yes"])
    assert result.exit_code != 0
    assert "claude binary not found on PATH" in result.output
    assert latest_run() is None  # refused before any run directory was created


def test_run_replay_marks_failed_when_worker_raises(
    projects_dir: Path, drift_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import claude_drift.cli as cli

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(cli, "replay_cut", boom)
    with pytest.raises(RuntimeError):
        run_replay(
            from_model="opus-5",
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
    run = latest_run()
    assert run is not None
    m = run.read_manifest()
    assert m["status"] == "failed"
    assert "disk full" in m["failure"]


def test_run_replay_marks_interrupted_on_keyboard_interrupt(
    projects_dir: Path, drift_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import claude_drift.cli as cli

    def boom(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "replay_cut", boom)
    with pytest.raises(KeyboardInterrupt):
        run_replay(
            from_model="opus-5",
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
    run = latest_run()
    assert run is not None
    m = run.read_manifest()
    assert m["status"] == "interrupted"


def test_raise_sigterm_raises_system_exit_143() -> None:
    import claude_drift.cli as cli

    with pytest.raises(SystemExit) as exc_info:
        cli._raise_sigterm(15, None)
    assert exc_info.value.code == 143


def test_cli_replay_estimate_matches_sampled_cuts(
    projects_dir: Path, drift_home: Path, repo_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import claude_drift.cli as cli

    pretend_claude_installed(monkeypatch)
    monkeypatch.setattr(cli, "SubprocessRunner", ScriptedRunner)
    slug = projects_dir / "-fake-project"
    alpha = slug / "alpha.jsonl"
    extra_lines = []
    for i in range(3):
        extra_lines.append(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "alpha",
                    "cwd": str(repo_dir),
                    "version": "2.1.261",
                    "message": {"role": "user", "content": f"extra prompt {i}"},
                }
            )
        )
        extra_lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": "alpha",
                    "cwd": str(repo_dir),
                    "version": "2.1.261",
                    "message": {
                        "model": "claude-opus-5",
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "Bash", "input": {"command": f"echo {i}"}}
                        ],
                    },
                }
            )
        )
    alpha.write_text(alpha.read_text().rstrip("\n") + "\n" + "\n".join(extra_lines) + "\n")

    result = CliRunner().invoke(
        main, ["replay", "--from", "opus-5", "--to", "new", "--turns", "60", "--yes"]
    )
    assert result.exit_code == 0, result.output
    printed_n = int(result.output.split("cuts: ")[1].split()[0])
    lr = latest_run()
    assert lr is not None
    assert printed_n == len(lr.read_cuts())


def test_cli_replay_per_session_option(
    projects_dir: Path, drift_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import claude_drift.cli as cli

    pretend_claude_installed(monkeypatch)
    monkeypatch.setattr(cli, "SubprocessRunner", ScriptedRunner)
    result = CliRunner().invoke(
        main,
        [
            "replay",
            "--from",
            "opus-5",
            "--to",
            "new",
            "--turns",
            "60",
            "--per-session",
            "1",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    lr = latest_run()
    assert lr is not None
    cuts = lr.read_cuts()
    # fixtures alpha and beta each have replayable claude-opus-5 cuts; capped at 1 per session
    assert len({c.session_id for c in cuts}) == 2
    assert len(cuts) == 2
    assert lr.read_manifest()["per_session"] == 1


def test_cli_replay_ambiguous_from(
    projects_dir: Path, drift_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pretend_claude_installed(monkeypatch)
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
