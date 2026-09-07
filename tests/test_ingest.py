from __future__ import annotations

from pathlib import Path

from claude_drift.ingest import cuts_from_session, ingest, list_session_files, projects_root


def test_projects_root_uses_env(projects_dir: Path) -> None:
    assert projects_root() == projects_dir


def test_list_session_files_ignores_subagents(projects_dir: Path) -> None:
    names = [p.name for p in list_session_files(projects_dir)]
    assert names == ["alpha.jsonl", "beta.jsonl", "gamma.jsonl"]


def test_alpha_yields_two_cuts_after_human_prompts(projects_dir: Path, repo_dir: Path) -> None:
    cuts = cuts_from_session(projects_dir / "-fake-project" / "alpha.jsonl")
    assert [c.line_index for c in cuts] == [0, 7]
    first, second = cuts
    assert first.cut_id == "alpha:0"
    assert first.prompt == "list the repo"
    assert first.recorded.tool == "Bash"
    assert first.recorded.input == {"command": "ls -la"}
    assert first.model == "claude-opus-5"
    assert first.cwd == str(repo_dir)
    assert first.version == "2.1.261"
    assert first.effort == "high"
    assert second.recorded.tool == "Read"
    assert second.effort == "max"


def test_beta_skips_synthetic_sidechain_and_text_only(projects_dir: Path) -> None:
    cuts = cuts_from_session(projects_dir / "-fake-project" / "beta.jsonl")
    # "hello" -> <synthetic> model: skipped
    # "try again" -> sidechain tool_use ignored, then text only: skipped
    # "run tests" -> Bash pytest: kept
    assert [(c.line_index, c.recorded.tool) for c in cuts] == [(5, "Bash")]
    assert cuts[0].effort is None


def test_gamma_skipped_when_cwd_missing(projects_dir: Path) -> None:
    assert cuts_from_session(projects_dir / "-fake-project" / "gamma.jsonl") == []


def test_ingest_all_and_project_filter(projects_dir: Path, repo_dir: Path) -> None:
    all_cuts = ingest(projects_dir)
    assert sorted(c.cut_id for c in all_cuts) == ["alpha:0", "alpha:7", "beta:5"]
    assert ingest(projects_dir, project=str(repo_dir)) == all_cuts
    assert ingest(projects_dir, project="/somewhere/else") == []


def test_malformed_line_skips_session_not_process(projects_dir: Path) -> None:
    bad = projects_dir / "-fake-project" / "bad.jsonl"
    bad.write_text('{"type":"user"\nnot json\n')
    assert cuts_from_session(bad) == []
    assert len(ingest(projects_dir)) == 3


def test_invalid_effort_value_is_ignored(projects_dir: Path, repo_dir: Path) -> None:
    path = projects_dir / "-fake-project" / "alpha.jsonl"
    text = path.read_text().replace('"effort":"high"', '"effort":"turbo"')
    assert '"effort":"turbo"' in text  # replacement actually happened
    path.write_text(text)
    cuts = cuts_from_session(path)
    first, _second = cuts
    assert first.effort is None
