from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner

from claude_drift.cli import main, run_replay
from claude_drift.ingest import ingest, projects_root
from claude_drift.models import ActionSignature, CutPoint, RecordedAction, ReplayResult
from claude_drift.sample import sample_cuts
from claude_drift.stats import compute
from claude_drift.store import latest_run, new_run


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
    assert "min" in result.output  # wall-clock estimate alongside the token estimate
    assert "run id:" in result.output
    # the run-level count of failed replays, distinct from the report's `errors:` line
    assert "failed replays: 0" in result.output
    lr = latest_run()
    assert lr is not None and len(lr.read_cuts()) == 2


def test_cli_replay_turns_default_is_30() -> None:
    from claude_drift.cli import replay

    turns_param = next(p for p in replay.params if p.name == "turns")
    assert turns_param.default == 30


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


def test_resume_reruns_missing_and_errored_replays(
    projects_dir: Path, drift_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose noise replays all errored out (as would happen after hitting a session
    limit mid-run) should have `resume` re-run only the noise role, leaving the already
    -successful candidate rows untouched, and every cut should end up fully successful."""
    import claude_drift.cli as cli

    pretend_claude_installed(monkeypatch)
    monkeypatch.setattr(cli, "SubprocessRunner", ScriptedRunner)

    resolved_from = "claude-opus-5"
    all_cuts = ingest(projects_root(), project=None)
    cuts = sample_cuts(
        [c for c in all_cuts if c.model == resolved_from], turns=10, seed=0, per_session=3
    )
    assert cuts  # fixtures provide replayable claude-opus-5 cuts

    run = new_run()
    run.write_cuts(cuts)
    candidate_sig = ActionSignature("Read", "local-read", None, False)
    manifest = {
        "from": resolved_from,
        "to": "new",
        "turns": len(cuts),
        "seed": 0,
        "workers": 1,
        "noise": True,
        "timeout": 1.0,
        "per_session": 3,
        "claude_version": "test",
        "started": "2026-01-01T00:00:00",
        "finished": "2026-01-01T00:00:01",
        "status": "aborted",
        "completed": len(cuts) * 2,
        "errors": len(cuts),
    }
    run.write_manifest(manifest)
    for c in cuts:
        run.append_replay(
            ReplayResult(c.cut_id, "new", "candidate", candidate_sig, None, {}, None, 1)
        )
        run.append_replay(
            ReplayResult(c.cut_id, resolved_from, "noise", None, None, {}, "session limit", 1)
        )

    result = CliRunner().invoke(main, ["resume", "--run", run.path.name, "--yes"])
    assert result.exit_code == 0, result.output
    assert "pending replays" in result.output

    m = run.read_manifest()
    assert m["status"] == "done"
    assert len(m["resumes"]) == 1
    assert m["resumes"][0]["pending"] == len(cuts)

    replays = run.read_replays()
    assert all(
        r.error is None for r in replays if r.role == "candidate"
    )  # untouched, never re-run
    stats = compute(cuts, replays)
    assert stats.cuts == len(cuts)
    assert stats.errors == 0


def test_resume_nothing_pending(
    projects_dir: Path, drift_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import claude_drift.cli as cli

    pretend_claude_installed(monkeypatch)
    monkeypatch.setattr(cli, "SubprocessRunner", ScriptedRunner)
    run = run_replay(
        from_model="opus-5",
        to_model="new",
        turns=10,
        workers=2,
        noise=True,
        project=None,
        seed=0,
        timeout=1.0,
        runner=ScriptedRunner(),
        echo=lambda s: None,
    )
    result = CliRunner().invoke(main, ["resume", "--run", run.path.name, "--yes"])
    assert result.exit_code == 0, result.output
    assert "nothing to resume: all replays succeeded" in result.output
    assert "resumes" not in run.read_manifest()


def make_cut(session: str, line_index: int) -> CutPoint:
    return CutPoint(
        f"{session}:{line_index}",
        f"/p/{session}.jsonl",
        session,
        line_index,
        "p",
        RecordedAction("Bash", {"command": "ls"}),
        "old",
        "/c",
        "v",
    )


def test_batch_runs_each_candidate_next_to_its_own_noise_attempts() -> None:
    """The K+1 calls for one cut share an identical prefix, so running them back to
    back is what makes prompt caching hit."""
    from claude_drift.cli import _batch_items_by_session

    cuts = [make_cut("s1", 1), make_cut("s1", 2)]
    items = [(c, "new", "candidate", 0) for c in cuts]
    items += [(c, "old", "noise", a) for c in cuts for a in range(2)]
    batches = _batch_items_by_session(items)
    assert len(batches) == 1
    assert [(it[0].line_index, it[2], it[3]) for it in batches[0]] == [
        (1, "candidate", 0),
        (1, "noise", 0),
        (1, "noise", 1),
        (2, "candidate", 0),
        (2, "noise", 0),
        (2, "noise", 1),
    ]


def test_batch_keeps_one_session_per_worker() -> None:
    from claude_drift.cli import _batch_items_by_session

    items = [
        (make_cut("s2", 1), "new", "candidate", 0),
        (make_cut("s1", 1), "new", "candidate", 0),
    ]
    batches = _batch_items_by_session(items)
    assert [b[0][0].session_id for b in batches] == ["s1", "s2"]


def test_run_replay_records_every_self_replay_attempt(
    projects_dir: Path, drift_home: Path
) -> None:
    run = run_replay(
        from_model="opus-5",
        to_model="new",
        turns=10,
        workers=1,
        noise=True,
        project=None,
        seed=0,
        timeout=1.0,
        runner=ScriptedRunner(),
        echo=lambda s: None,
        self_replays=3,
    )
    cuts = run.read_cuts()
    replays = run.read_replays()
    assert run.read_manifest()["self_replays"] == 3
    assert len(replays) == len(cuts) * 4  # 1 candidate + 3 noise attempts per cut
    noise = [r for r in replays if r.role == "noise"]
    assert sorted(r.attempt for r in noise) == sorted(list(range(3)) * len(cuts))
    assert all(r.attempt == 0 for r in replays if r.role == "candidate")


def test_cli_replay_self_replays_option_counts_replays(
    projects_dir: Path, drift_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import claude_drift.cli as cli

    pretend_claude_installed(monkeypatch)
    monkeypatch.setattr(cli, "SubprocessRunner", ScriptedRunner)
    result = CliRunner().invoke(
        main,
        ["replay", "--from", "opus-5", "--to", "new", "--turns", "2",
         "--self-replays", "2", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "cuts: 2  replays: 6" in result.output  # 2 * (1 candidate + 2 noise)
    lr = latest_run()
    assert lr is not None and lr.read_manifest()["self_replays"] == 2


def test_cli_replay_self_replays_defaults_to_one() -> None:
    from claude_drift.cli import replay

    param = next(p for p in replay.params if p.name == "self_replays")
    assert param.default == 1


def test_pending_targets_covers_every_self_replay_attempt() -> None:
    from claude_drift.cli import _pending_targets

    cuts = [make_cut("s1", 1)]
    manifest = {"to": "new", "from": "old", "noise": True, "self_replays": 3}
    done = [
        ReplayResult("s1:1", "new", "candidate", None, None, {}, None, 1, 0),
        ReplayResult("s1:1", "old", "noise", None, None, {}, None, 1, 0),
        ReplayResult("s1:1", "old", "noise", None, None, {}, "boom", 1, 1),
    ]
    pending = _pending_targets(cuts, done, manifest)
    assert [(m, role, a) for _, m, role, a in pending] == [("old", "noise", 1), ("old", "noise", 2)]


def test_pending_targets_defaults_to_one_attempt_for_old_manifests() -> None:
    from claude_drift.cli import _pending_targets

    cuts = [make_cut("s1", 1)]
    manifest = {"to": "new", "from": "old", "noise": True}
    pending = _pending_targets(cuts, [], manifest)
    assert [(role, a) for _, _, role, a in pending] == [("candidate", 0), ("noise", 0)]
