from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from claude_drift.cli import main


def test_scan_lists_sessions_models_and_cut_counts(projects_dir: Path, repo_dir: Path) -> None:
    result = CliRunner().invoke(main, ["scan"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "sessions with replayable cuts: 2" in out
    assert "replayable cuts: 3" in out
    assert "claude-opus-5" in out and "3" in out
    assert "Bash" in out and "Read" in out
    assert "by recorded effort" in out
    assert "high" in out and "max" in out and "unknown" in out  # beta's cut has no effort


def test_scan_project_filter_no_matches(projects_dir: Path) -> None:
    result = CliRunner().invoke(main, ["scan", "--project", "/nowhere"])
    assert result.exit_code == 0
    assert "replayable cuts: 0" in result.output


def test_scan_missing_root(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CLAUDE_DRIFT_PROJECTS_DIR", str(tmp_path / "none"))
    result = CliRunner().invoke(main, ["scan"])
    assert result.exit_code == 0
    assert "replayable cuts: 0" in result.output
