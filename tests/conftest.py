from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "sessions"


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    (d / "src").mkdir(parents=True)
    (d / "src" / "app.py").write_text("print('hi')\n")
    (d / "README.md").write_text("# hi\n")
    return d


@pytest.fixture
def projects_dir(tmp_path: Path, repo_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy fixture sessions into a fake ~/.claude/projects, substituting /CWD/ with repo_dir."""
    root = tmp_path / "projects"
    slug = root / "-fake-project"
    slug.mkdir(parents=True)
    for src in FIXTURES.glob("*.jsonl"):
        text = src.read_text().replace("/CWD/", str(repo_dir))
        (slug / src.name).write_text(text)
    # a subagent file that must be ignored
    sub = slug / "subagents"
    sub.mkdir()
    shutil.copy(FIXTURES / "alpha.jsonl", sub / "agent-x.jsonl")
    monkeypatch.setenv("CLAUDE_DRIFT_PROJECTS_DIR", str(root))
    return root


@pytest.fixture
def drift_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "drift-home"
    monkeypatch.setenv("CLAUDE_DRIFT_HOME", str(home))
    return home
