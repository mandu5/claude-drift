from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_drift.ingest import cuts_from_session
from claude_drift.replay import blocking_settings_path, build_argv, temp_session


def alpha_cuts(projects_dir: Path):  # type: ignore[no-untyped-def]
    return cuts_from_session(projects_dir / "-fake-project" / "alpha.jsonl")


def test_temp_session_is_prefix_with_new_id_and_is_removed(projects_dir: Path) -> None:
    cut = alpha_cuts(projects_dir)[1]  # line_index 7
    original = Path(cut.session_path).read_text()
    with temp_session(cut) as tid:
        tmp = Path(cut.session_path).with_name(f"{tid}.jsonl")
        assert tmp.exists()
        lines = [json.loads(line) for line in tmp.read_text().splitlines() if line.strip()]
        assert len(lines) == 7
        assert all(e["sessionId"] == tid for e in lines)
        assert lines[-1]["message"]["content"][0]["type"] == "text"  # "Done." is line 6
    assert not tmp.exists()
    assert Path(cut.session_path).read_text() == original


def test_temp_session_removed_on_exception(projects_dir: Path) -> None:
    cut = alpha_cuts(projects_dir)[0]
    with pytest.raises(RuntimeError):
        with temp_session(cut) as tid:
            tmp = Path(cut.session_path).with_name(f"{tid}.jsonl")
            assert tmp.exists()
            raise RuntimeError("boom")
    assert not tmp.exists()


def test_blocking_settings_denies_all_tools(drift_home: Path) -> None:
    p = blocking_settings_path()
    data = json.loads(p.read_text())
    hooks = data["hooks"]["PreToolUse"]
    assert hooks[0]["matcher"] == ""
    assert hooks[0]["hooks"][0]["type"] == "command"
    assert "exit 2" in hooks[0]["hooks"][0]["command"]


def test_blocking_settings_is_idempotent_and_atomic(drift_home: Path) -> None:
    first = blocking_settings_path()
    content = first.read_text()
    mtime = first.stat().st_mtime_ns
    second = blocking_settings_path()
    assert second == first
    assert second.read_text() == content
    assert second.stat().st_mtime_ns == mtime  # unchanged content is not rewritten
    # the rename target must never be left behind for `--settings` to pick up
    assert list(drift_home.glob("*.tmp")) == []


def test_build_argv(projects_dir: Path, drift_home: Path) -> None:
    cut = alpha_cuts(projects_dir)[0]
    argv = build_argv(cut, "tmp-id", "claude-sonnet-5", Path("/s.json"))
    assert argv[:3] == ["claude", "-p", "list the repo"]
    assert "--resume" in argv and argv[argv.index("--resume") + 1] == "tmp-id"
    assert "--fork-session" in argv
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5"
    assert argv[argv.index("--max-turns") + 1] == "1"
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert argv[argv.index("--settings") + 1] == "/s.json"
    assert "--bare" not in argv and "--disallowedTools" not in argv
