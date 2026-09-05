# claude-drift v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `drift` CLI that replays a user's own Claude Code session logs against a new model, one teacher-forced step per cut, and reports behaviour drift separated from noise.

**Architecture:** Six pure-ish modules in a pipeline (`ingest -> sample -> replay -> classify -> stats -> report`) plus a `store` module for run directories and a thin `click` CLI. The only side-effecting module is `replay`, which writes a truncated copy of a session under a temporary id, shells out to the `claude` binary with `--resume --fork-session`, stops at the first `tool_use`, and deletes the temporary file. Everything else operates on dataclasses and JSONL.

**Tech Stack:** Python 3.11+, `click`, standard library only otherwise. `uv` for env/packaging, `pytest`, `ruff`, `mypy --strict`.

**Spec:** `docs/superpowers/specs/2026-09-05-claude-drift-design.md`

## Global Constraints

- Python `>=3.11`. Runtime dependency: `click` only. No numpy, no requests, no SDKs.
- Never call the Anthropic API or Agent SDK. Only shell out to the `claude` binary (spec §2, §5.3).
- Never pass `--bare` to `claude` (spec §10). Never pass `--disallowedTools` (spec §5.3).
- Never modify original session files under `~/.claude/projects` (spec §6). Temporary session files are deleted in `finally`.
- Session-file root defaults to `~/.claude/projects`; overridable with env `CLAUDE_DRIFT_PROJECTS_DIR`. Run root defaults to `~/.claude-drift/runs`; overridable with env `CLAUDE_DRIFT_HOME`. Tests always set both to `tmp_path`.
- Package name `claude-drift`, import name `claude_drift`, console script `drift` (spec §4).
- Tests that call the real `claude` binary run only when `DRIFT_LIVE=1` (spec §7).
- Defaults: `--turns 60`, `--workers 4`, `--seed 0`, per-session cap `3`, timeout `180` seconds, abort when error rate exceeds 20% (spec §4, §5.2, §5.3).
- Commit after every task with the trailer:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Do4ZmbMcfPmwrBWWaQvNYd
  ```
- All commands below run from repo root `~/Documents/GitHub/claude-drift` with `uv run` so the project venv is used.

## Deviation from spec (decided here, applied in Task 0)

`ActionSignature.asks_first` from spec §5.4 is dropped: with single-step replay the only way "asks first" shows up is `tool == "AskUserQuestion"`, which `target == "ask-user"` already encodes. Task 0 edits the spec to remove that field so spec and code agree.

## File Structure

```
claude-drift/
  pyproject.toml                 # package metadata, deps, ruff/mypy/pytest config
  README.md                      # English; usage + example report (Task 13)
  .github/workflows/ci.yml       # ruff, mypy, pytest on push/PR (Task 13)
  src/claude_drift/
    __init__.py                  # __version__
    models.py                    # dataclasses + to_dict/from_dict (Task 1)
    ingest.py                    # session JSONL -> list[CutPoint] (Task 2)
    sample.py                    # stratified sampling + per-session batches (Task 4)
    classify.py                  # tool_use dict -> ActionSignature, Bash rules (Task 5)
    replay.py                    # temp session, claude argv, stream-json parse, runner (Task 6, 7)
    store.py                     # run directory: manifest, cuts.jsonl, replays.jsonl (Task 9)
    stats.py                     # agreement, bootstrap band, transitions (Task 11)
    report.py                    # text / markdown rendering (Task 12)
    cli.py                       # click group: scan, replay, report (Task 3, 10, 12)
  tests/
    conftest.py                  # fixtures: env isolation, sample sessions
    fixtures/sessions/
      alpha.jsonl                # 2 human cuts, one Bash, one Read
      beta.jsonl                 # sidechain line, tool_result-only turn, synthetic model
      gamma.jsonl                # cwd that does not exist
    test_models.py
    test_ingest.py
    test_cli_scan.py
    test_sample.py
    test_classify.py
    test_replay_session.py
    test_replay_runner.py
    test_replay_live.py          # DRIFT_LIVE=1 only
    test_store.py
    test_cli_replay.py
    test_stats.py
    test_report.py
```

Each module exposes a handful of functions with dataclass inputs/outputs. Nothing imports `cli.py`. Only `replay.py` and `store.py` touch the filesystem outside tests.

---

### Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`, `src/claude_drift/__init__.py`, `tests/__init__.py`, `tests/test_version.py`
- Modify: `docs/superpowers/specs/2026-09-05-claude-drift-design.md` (remove `asks_first` line)
- Modify: `.gitignore` (add `uv.lock`? No: commit `uv.lock`)

**Interfaces:**
- Produces: `claude_drift.__version__: str`, console script `drift` (wired in Task 3)

- [ ] **Step 1: Install uv if missing**

Run: `which uv || brew install uv`
Expected: a path to `uv` printed.

- [ ] **Step 2: Write pyproject.toml**

```toml
[project]
name = "claude-drift"
version = "0.1.0"
description = "Replay your own Claude Code sessions against a new model and see what actually changed."
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.11"
dependencies = ["click>=8.1"]

[project.scripts]
drift = "claude_drift.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/claude_drift"]

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6", "mypy>=1.11"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
strict = true
python_version = "3.11"
files = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Write package init and a version test**

`src/claude_drift/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

`tests/test_version.py`:
```python
from claude_drift import __version__


def test_version_is_semver() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
```

- [ ] **Step 4: Sync env and run the test**

Run: `uv sync && uv run pytest -q`
Expected: `1 passed`.

- [ ] **Step 5: Run lint and type check**

Run: `uv run ruff check . && uv run mypy`
Expected: no errors.

- [ ] **Step 6: Edit the spec to drop `asks_first`**

In `docs/superpowers/specs/2026-09-05-claude-drift-design.md` delete the line
`  asks_first: bool,      # 도구 전에 AskUserQuestion이 있었는가` inside the `ActionSignature(` block. Nothing else changes.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Scaffold package with uv, ruff, mypy, pytest"
```

---

### Task 1: Data models

**Files:**
- Create: `src/claude_drift/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True) class RecordedAction(tool: str, input: dict[str, Any])
  @dataclass(frozen=True) class CutPoint(cut_id: str, session_path: str, session_id: str,
      line_index: int, prompt: str, recorded: RecordedAction, model: str, cwd: str, version: str)
  @dataclass(frozen=True) class ActionSignature(tool: str, target: str, path_scope: str | None, text_only: bool)
      # .key -> str  e.g. "Bash/local-read"
  @dataclass(frozen=True) class ReplayResult(cut_id: str, model: str, role: str,
      signature: ActionSignature | None, raw_tool_use: dict[str, Any] | None,
      usage: dict[str, int], error: str | None, duration_ms: int)
      # role is "noise" (old model replay) or "candidate" (new model replay)
  def to_dict(obj) -> dict ; def cut_from_dict(d) -> CutPoint ; def replay_from_dict(d) -> ReplayResult
  ```

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:
```python
from claude_drift.models import (
    ActionSignature,
    CutPoint,
    RecordedAction,
    ReplayResult,
    cut_from_dict,
    replay_from_dict,
    to_dict,
)


def make_cut() -> CutPoint:
    return CutPoint(
        cut_id="s1:14",
        session_path="/tmp/p/s1.jsonl",
        session_id="s1",
        line_index=14,
        prompt="fix the test",
        recorded=RecordedAction(tool="Bash", input={"command": "pytest -q"}),
        model="claude-opus-5",
        cwd="/tmp/repo",
        version="2.1.261",
    )


def test_cut_roundtrip() -> None:
    cut = make_cut()
    assert cut_from_dict(to_dict(cut)) == cut


def test_replay_roundtrip_with_signature() -> None:
    r = ReplayResult(
        cut_id="s1:14",
        model="claude-sonnet-5",
        role="candidate",
        signature=ActionSignature(tool="Bash", target="local-read", path_scope=None, text_only=False),
        raw_tool_use={"name": "Bash", "input": {"command": "ls"}},
        usage={"input_tokens": 2, "output_tokens": 10},
        error=None,
        duration_ms=1234,
    )
    assert replay_from_dict(to_dict(r)) == r


def test_replay_roundtrip_with_error_and_no_signature() -> None:
    r = ReplayResult(
        cut_id="s1:14", model="claude-sonnet-5", role="noise", signature=None,
        raw_tool_use=None, usage={}, error="timeout", duration_ms=180000,
    )
    assert replay_from_dict(to_dict(r)) == r


def test_signature_key() -> None:
    sig = ActionSignature(tool="Read", target="local-read", path_scope="src", text_only=False)
    assert sig.key == "Read/local-read"
    assert ActionSignature(tool="", target="text", path_scope=None, text_only=True).key == "text"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_drift.models'`.

- [ ] **Step 3: Implement models.py**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RecordedAction:
    tool: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CutPoint:
    cut_id: str
    session_path: str
    session_id: str
    line_index: int
    prompt: str
    recorded: RecordedAction
    model: str
    cwd: str
    version: str


@dataclass(frozen=True)
class ActionSignature:
    tool: str
    target: str
    path_scope: str | None
    text_only: bool

    @property
    def key(self) -> str:
        if self.text_only:
            return "text"
        return f"{self.tool}/{self.target}"


@dataclass(frozen=True)
class ReplayResult:
    cut_id: str
    model: str
    role: str
    signature: ActionSignature | None
    raw_tool_use: dict[str, Any] | None
    usage: dict[str, int]
    error: str | None
    duration_ms: int


def to_dict(obj: CutPoint | ReplayResult | ActionSignature | RecordedAction) -> dict[str, Any]:
    return asdict(obj)


def cut_from_dict(d: dict[str, Any]) -> CutPoint:
    rec = d["recorded"]
    return CutPoint(
        cut_id=d["cut_id"],
        session_path=d["session_path"],
        session_id=d["session_id"],
        line_index=int(d["line_index"]),
        prompt=d["prompt"],
        recorded=RecordedAction(tool=rec["tool"], input=dict(rec.get("input", {}))),
        model=d["model"],
        cwd=d["cwd"],
        version=d["version"],
    )


def signature_from_dict(d: dict[str, Any] | None) -> ActionSignature | None:
    if d is None:
        return None
    return ActionSignature(
        tool=d["tool"], target=d["target"], path_scope=d.get("path_scope"), text_only=bool(d["text_only"])
    )


def replay_from_dict(d: dict[str, Any]) -> ReplayResult:
    return ReplayResult(
        cut_id=d["cut_id"],
        model=d["model"],
        role=d["role"],
        signature=signature_from_dict(d.get("signature")),
        raw_tool_use=d.get("raw_tool_use"),
        usage={k: int(v) for k, v in d.get("usage", {}).items()},
        error=d.get("error"),
        duration_ms=int(d["duration_ms"]),
    )
```

- [ ] **Step 4: Run tests, lint, types**

Run: `uv run pytest tests/test_models.py -q && uv run ruff check . && uv run mypy`
Expected: `4 passed`, no lint/type errors.

- [ ] **Step 5: Commit**

```bash
git add src/claude_drift/models.py tests/test_models.py
git commit -m "Add core dataclasses with JSON round-trip"
```

---

### Task 2: Ingest session logs into cut points

**Files:**
- Create: `src/claude_drift/ingest.py`, `tests/conftest.py`, `tests/fixtures/sessions/alpha.jsonl`, `tests/fixtures/sessions/beta.jsonl`, `tests/fixtures/sessions/gamma.jsonl`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `CutPoint`, `RecordedAction` (Task 1)
- Produces:
  ```python
  def projects_root() -> Path                       # env CLAUDE_DRIFT_PROJECTS_DIR or ~/.claude/projects
  def list_session_files(root: Path) -> list[Path]  # top-level */*.jsonl only, sorted
  def read_lines(path: Path) -> list[str]           # raw lines, trailing newline stripped, empty lines kept
  def cuts_from_session(path: Path, cwd_exists=os.path.isdir) -> list[CutPoint]
  def ingest(root: Path, project: str | None = None) -> list[CutPoint]
  ```

Session log facts the implementer needs (observed on Claude Code 2.1.261):
- One JSON object per line. Relevant keys: `type` (`user` | `assistant` | others), `message`, `cwd`, `version`, `sessionId`, `isSidechain`.
- A human-typed prompt is `type == "user"` with `message.content` a **string**. Tool results are `type == "user"` with `message.content` a **list** of `{"type": "tool_result", ...}`.
- Assistant content blocks arrive as separate lines sharing `message.id`; a `tool_use` block is `{"type": "tool_use", "name": ..., "input": {...}}` inside `message.content` (a list). `message.model` is e.g. `claude-opus-5`; error placeholders use `<synthetic>`.
- `cwd` and `version` are present on user/assistant lines, not on every line.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/sessions/alpha.jsonl` (cwd is replaced at test time; keep `/CWD/` literal):
```
{"type":"user","sessionId":"alpha","cwd":"/CWD/","version":"2.1.261","message":{"role":"user","content":"list the repo"}}
{"type":"assistant","sessionId":"alpha","cwd":"/CWD/","version":"2.1.261","message":{"model":"claude-opus-5","role":"assistant","content":[{"type":"text","text":"Sure."}]}}
{"type":"assistant","sessionId":"alpha","cwd":"/CWD/","version":"2.1.261","message":{"model":"claude-opus-5","role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"ls -la"}}]}}
{"type":"user","sessionId":"alpha","cwd":"/CWD/","version":"2.1.261","message":{"role":"user","content":[{"type":"tool_result","content":"README.md"}]}}
{"type":"assistant","sessionId":"alpha","cwd":"/CWD/","version":"2.1.261","message":{"model":"claude-opus-5","role":"assistant","content":[{"type":"tool_use","name":"Read","input":{"file_path":"/CWD/README.md"}}]}}
{"type":"user","sessionId":"alpha","cwd":"/CWD/","version":"2.1.261","message":{"role":"user","content":[{"type":"tool_result","content":"# hi"}]}}
{"type":"assistant","sessionId":"alpha","cwd":"/CWD/","version":"2.1.261","message":{"model":"claude-opus-5","role":"assistant","content":[{"type":"text","text":"Done."}]}}
{"type":"user","sessionId":"alpha","cwd":"/CWD/","version":"2.1.261","message":{"role":"user","content":"now open src/app.py"}}
{"type":"assistant","sessionId":"alpha","cwd":"/CWD/","version":"2.1.261","message":{"model":"claude-opus-5","role":"assistant","content":[{"type":"tool_use","name":"Read","input":{"file_path":"/CWD/src/app.py"}}]}}
```

`tests/fixtures/sessions/beta.jsonl`:
```
{"type":"user","sessionId":"beta","cwd":"/CWD/","version":"2.1.261","message":{"role":"user","content":"hello"}}
{"type":"assistant","sessionId":"beta","cwd":"/CWD/","version":"2.1.261","message":{"model":"<synthetic>","role":"assistant","content":[{"type":"text","text":"Not logged in"}]}}
{"type":"user","sessionId":"beta","cwd":"/CWD/","version":"2.1.261","message":{"role":"user","content":"try again"}}
{"type":"assistant","sessionId":"beta","cwd":"/CWD/","version":"2.1.261","isSidechain":true,"message":{"model":"claude-opus-5","role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"echo side"}}]}}
{"type":"assistant","sessionId":"beta","cwd":"/CWD/","version":"2.1.261","message":{"model":"claude-opus-5","role":"assistant","content":[{"type":"text","text":"Only text, no tool."}]}}
{"type":"user","sessionId":"beta","cwd":"/CWD/","version":"2.1.261","message":{"role":"user","content":"run tests"}}
{"type":"assistant","sessionId":"beta","cwd":"/CWD/","version":"2.1.261","message":{"model":"claude-opus-5","role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"pytest -q"}}]}}
```

`tests/fixtures/sessions/gamma.jsonl`:
```
{"type":"user","sessionId":"gamma","cwd":"/nonexistent/dir/xyz","version":"2.1.261","message":{"role":"user","content":"do it"}}
{"type":"assistant","sessionId":"gamma","cwd":"/nonexistent/dir/xyz","version":"2.1.261","message":{"model":"claude-opus-5","role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"make"}}]}}
```

- [ ] **Step 2: Write conftest.py**

`tests/conftest.py`:
```python
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
```

- [ ] **Step 3: Write the failing tests**

`tests/test_ingest.py`:
```python
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
    assert second.recorded.tool == "Read"


def test_beta_skips_synthetic_sidechain_and_text_only(projects_dir: Path) -> None:
    cuts = cuts_from_session(projects_dir / "-fake-project" / "beta.jsonl")
    # "hello" -> <synthetic> model: skipped
    # "try again" -> sidechain tool_use ignored, then text only: skipped
    # "run tests" -> Bash pytest: kept
    assert [(c.line_index, c.recorded.tool) for c in cuts] == [(5, "Bash")]


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
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_ingest.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_drift.ingest'`.

- [ ] **Step 5: Implement ingest.py**

```python
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_drift.models import CutPoint, RecordedAction

SYNTHETIC_MODEL = "<synthetic>"


def projects_root() -> Path:
    env = os.environ.get("CLAUDE_DRIFT_PROJECTS_DIR")
    return Path(env) if env else Path.home() / ".claude" / "projects"


def list_session_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*/*.jsonl") if p.is_file())


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").split("\n")


def _parse(lines: list[str]) -> list[dict[str, Any] | None]:
    """Parse every line; empty lines become None. Raise on malformed JSON."""
    out: list[dict[str, Any] | None] = []
    for raw in lines:
        if not raw.strip():
            out.append(None)
            continue
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("line is not an object")
        out.append(obj)
    return out


def _is_human_prompt(entry: dict[str, Any]) -> bool:
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return False
    msg = entry.get("message")
    return isinstance(msg, dict) and isinstance(msg.get("content"), str)


def _first_tool_use(entry: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    msg = entry.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
        return None
    for block in msg["content"]:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return str(block.get("name", "")), dict(block.get("input", {}))
    return None


def _recorded_after(entries: list[dict[str, Any] | None], start: int) -> tuple[RecordedAction, str] | None:
    """First non-sidechain assistant tool_use after `start`, before the next human prompt."""
    for j in range(start + 1, len(entries)):
        e = entries[j]
        if e is None:
            continue
        if _is_human_prompt(e):
            return None
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        msg = e.get("message", {})
        model = str(msg.get("model", "")) if isinstance(msg, dict) else ""
        if model == SYNTHETIC_MODEL:
            return None
        tu = _first_tool_use(e)
        if tu is not None:
            return RecordedAction(tool=tu[0], input=tu[1]), model
    return None


def cuts_from_session(path: Path, cwd_exists: Callable[[str], bool] = os.path.isdir) -> list[CutPoint]:
    try:
        entries = _parse(read_lines(path))
    except (ValueError, UnicodeDecodeError):
        return []
    session_id = path.stem
    cwd = next((str(e["cwd"]) for e in entries if e and e.get("cwd")), "")
    version = next((str(e["version"]) for e in entries if e and e.get("version")), "")
    if not cwd or not cwd_exists(cwd):
        return []
    cuts: list[CutPoint] = []
    for i, e in enumerate(entries):
        if e is None or not _is_human_prompt(e):
            continue
        found = _recorded_after(entries, i)
        if found is None:
            continue
        recorded, model = found
        cuts.append(
            CutPoint(
                cut_id=f"{session_id}:{i}",
                session_path=str(path),
                session_id=session_id,
                line_index=i,
                prompt=str(e["message"]["content"]),
                recorded=recorded,
                model=model,
                cwd=cwd,
                version=version,
            )
        )
    return cuts


def ingest(root: Path, project: str | None = None) -> list[CutPoint]:
    cuts: list[CutPoint] = []
    for path in list_session_files(root):
        for cut in cuts_from_session(path):
            if project is None or cut.cwd == project or cut.cwd.startswith(project.rstrip("/") + "/"):
                cuts.append(cut)
    return cuts
```

- [ ] **Step 6: Run tests, lint, types**

Run: `uv run pytest tests/test_ingest.py -q && uv run ruff check . && uv run mypy`
Expected: `7 passed`, clean.

- [ ] **Step 7: Commit**

```bash
git add src/claude_drift/ingest.py tests/conftest.py tests/fixtures tests/test_ingest.py
git commit -m "Ingest Claude Code session logs into replayable cut points"
```

---

### Task 3: `drift scan` command

**Files:**
- Create: `src/claude_drift/cli.py`
- Test: `tests/test_cli_scan.py`

**Interfaces:**
- Consumes: `ingest`, `projects_root` (Task 2)
- Produces: `click` group `main` with subcommand `scan [--project PATH]`; later tasks add `replay` and `report` to the same group.

- [ ] **Step 1: Write the failing test**

`tests/test_cli_scan.py`:
```python
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


def test_scan_project_filter_no_matches(projects_dir: Path) -> None:
    result = CliRunner().invoke(main, ["scan", "--project", "/nowhere"])
    assert result.exit_code == 0
    assert "replayable cuts: 0" in result.output


def test_scan_missing_root(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CLAUDE_DRIFT_PROJECTS_DIR", str(tmp_path / "none"))
    result = CliRunner().invoke(main, ["scan"])
    assert result.exit_code == 0
    assert "replayable cuts: 0" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_scan.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_drift.cli'`.

- [ ] **Step 3: Implement cli.py with `scan`**

```python
from __future__ import annotations

from collections import Counter

import click

from claude_drift import __version__
from claude_drift.ingest import ingest, projects_root


@click.group()
@click.version_option(__version__, prog_name="drift")
def main() -> None:
    """Replay your Claude Code sessions against a new model and see what changed."""


@main.command()
@click.option("--project", type=click.Path(), default=None, help="Only sessions whose cwd is under this path.")
def scan(project: str | None) -> None:
    """List local sessions, recorded models, and replayable cut counts. Makes no model calls."""
    root = projects_root()
    cuts = ingest(root, project=project)
    sessions = {c.session_id for c in cuts}
    click.echo(f"projects root: {root}")
    click.echo(f"sessions with replayable cuts: {len(sessions)}")
    click.echo(f"replayable cuts: {len(cuts)}")
    if not cuts:
        return
    click.echo("\nby recorded model:")
    for model, n in Counter(c.model for c in cuts).most_common():
        click.echo(f"  {model:<28} {n:>5}")
    click.echo("\nby recorded tool:")
    for tool, n in Counter(c.recorded.tool for c in cuts).most_common():
        click.echo(f"  {tool:<28} {n:>5}")
```

- [ ] **Step 4: Run tests, lint, types, and the real command**

Run: `uv run pytest tests/test_cli_scan.py -q && uv run ruff check . && uv run mypy && uv run drift scan | head -20`
Expected: `3 passed`, clean, and a real listing of this machine's sessions (non-zero cut count).

- [ ] **Step 5: Commit**

```bash
git add src/claude_drift/cli.py tests/test_cli_scan.py
git commit -m "Add drift scan command"
```

---

### Task 4: Stratified sampling and per-session batches

**Files:**
- Create: `src/claude_drift/sample.py`
- Test: `tests/test_sample.py`

**Interfaces:**
- Consumes: `CutPoint` (Task 1)
- Produces:
  ```python
  def sample_cuts(cuts: list[CutPoint], turns: int, seed: int = 0, per_session: int = 3) -> list[CutPoint]
  def batch_by_session(cuts: list[CutPoint]) -> list[list[CutPoint]]   # each batch one session, ascending line_index
  ```

- [ ] **Step 1: Write the failing tests**

`tests/test_sample.py`:
```python
from __future__ import annotations

from collections import Counter

from claude_drift.models import CutPoint, RecordedAction
from claude_drift.sample import batch_by_session, sample_cuts


def cut(session: str, idx: int, tool: str) -> CutPoint:
    return CutPoint(
        cut_id=f"{session}:{idx}", session_path=f"/p/{session}.jsonl", session_id=session,
        line_index=idx, prompt="p", recorded=RecordedAction(tool=tool), model="m", cwd="/c", version="v",
    )


def corpus() -> list[CutPoint]:
    cuts = []
    for s in range(10):
        for i in range(8):
            tool = "Bash" if i % 4 else "Read"
            cuts.append(cut(f"s{s}", i, tool))
    cuts.append(cut("s99", 0, "AskUserQuestion"))
    return cuts


def test_sample_size_and_per_session_cap() -> None:
    got = sample_cuts(corpus(), turns=20, seed=0, per_session=3)
    assert len(got) == 20
    assert max(Counter(c.session_id for c in got).values()) <= 3


def test_sample_is_deterministic_for_seed() -> None:
    a = sample_cuts(corpus(), turns=20, seed=1)
    b = sample_cuts(corpus(), turns=20, seed=1)
    c = sample_cuts(corpus(), turns=20, seed=2)
    assert a == b
    assert a != c


def test_sample_keeps_every_tool_at_least_once() -> None:
    got = sample_cuts(corpus(), turns=12, seed=0)
    tools = Counter(c.recorded.tool for c in got)
    assert tools["AskUserQuestion"] == 1
    assert tools["Read"] >= 1 and tools["Bash"] >= 1
    assert tools["Bash"] > tools["Read"]  # proportional, Bash is 3x more common


def test_sample_when_fewer_cuts_than_turns_returns_all_capped() -> None:
    small = [cut("a", 0, "Bash"), cut("a", 1, "Bash"), cut("b", 0, "Read")]
    assert len(sample_cuts(small, turns=60)) == 3


def test_batch_by_session_groups_ascending() -> None:
    cuts = [cut("b", 5, "Bash"), cut("a", 9, "Bash"), cut("a", 2, "Read"), cut("b", 1, "Read")]
    batches = batch_by_session(cuts)
    assert [[c.cut_id for c in b] for b in batches] == [["a:2", "a:9"], ["b:1", "b:5"]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sample.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement sample.py**

```python
from __future__ import annotations

import random
from collections import defaultdict

from claude_drift.models import CutPoint


def _cap_per_session(cuts: list[CutPoint], per_session: int) -> list[CutPoint]:
    seen: dict[str, int] = defaultdict(int)
    kept: list[CutPoint] = []
    for c in cuts:
        if seen[c.session_id] < per_session:
            seen[c.session_id] += 1
            kept.append(c)
    return kept


def _quotas(group_sizes: dict[str, int], turns: int) -> dict[str, int]:
    """Proportional quotas, each group at least 1, summing to min(turns, total)."""
    total = sum(group_sizes.values())
    target = min(turns, total)
    quotas = {t: max(1, round(n / total * target)) if target >= len(group_sizes) else 0 for t, n in group_sizes.items()}
    if target < len(group_sizes):
        # fewer turns than tools: take the largest groups only
        for t, _ in sorted(group_sizes.items(), key=lambda kv: -kv[1])[:target]:
            quotas[t] = 1
        return quotas
    # trim or pad until the sum matches, adjusting the largest groups first
    order = sorted(group_sizes, key=lambda t: -group_sizes[t])
    i = 0
    while sum(quotas.values()) > target:
        t = order[i % len(order)]
        if quotas[t] > 1:
            quotas[t] -= 1
        i += 1
    while sum(quotas.values()) < target:
        t = order[i % len(order)]
        if quotas[t] < group_sizes[t]:
            quotas[t] += 1
        i += 1
    return quotas


def sample_cuts(cuts: list[CutPoint], turns: int, seed: int = 0, per_session: int = 3) -> list[CutPoint]:
    rng = random.Random(seed)
    shuffled = list(cuts)
    rng.shuffle(shuffled)
    capped = _cap_per_session(shuffled, per_session)
    groups: dict[str, list[CutPoint]] = defaultdict(list)
    for c in capped:
        groups[c.recorded.tool].append(c)
    quotas = _quotas({t: len(g) for t, g in groups.items()}, turns)
    chosen: list[CutPoint] = []
    for tool, group in groups.items():
        chosen.extend(group[: quotas.get(tool, 0)])
    return sorted(chosen, key=lambda c: (c.session_path, c.line_index))


def batch_by_session(cuts: list[CutPoint]) -> list[list[CutPoint]]:
    by_session: dict[str, list[CutPoint]] = defaultdict(list)
    for c in cuts:
        by_session[c.session_path].append(c)
    return [sorted(g, key=lambda c: c.line_index) for _, g in sorted(by_session.items())]
```

- [ ] **Step 4: Run tests, lint, types**

Run: `uv run pytest tests/test_sample.py -q && uv run ruff check . && uv run mypy`
Expected: `5 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add src/claude_drift/sample.py tests/test_sample.py
git commit -m "Add seeded stratified sampling and per-session batching"
```

---

### Task 5: Classify actions into signatures

**Files:**
- Create: `src/claude_drift/classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: `ActionSignature`, `RecordedAction` (Task 1)
- Produces:
  ```python
  TEXT_ONLY = ActionSignature(tool="", target="text", path_scope=None, text_only=True)
  def classify_bash(command: str) -> str            # "local-read" | "local-write" | "remote" | "other"
  def signature(tool: str, tool_input: dict[str, Any], cwd: str) -> ActionSignature
  def signature_of_recorded(rec: RecordedAction, cwd: str) -> ActionSignature
  def agree(a: ActionSignature, b: ActionSignature, level: str = "target") -> bool   # level: "tool" | "target" | "full"
  ```

- [ ] **Step 1: Write the failing tests**

`tests/test_classify.py`:
```python
from __future__ import annotations

import pytest

from claude_drift.classify import TEXT_ONLY, agree, classify_bash, signature
from claude_drift.models import ActionSignature


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls -la", "local-read"),
        ("cat README.md | head", "local-read"),
        ("grep -rn foo src/", "local-read"),
        ("git log --oneline -5 && git status", "local-read"),
        ("curl -s https://api.example.com | head", "remote"),
        ("gh api repos/x/y", "remote"),
        ("pip install requests", "remote"),
        ("npm install", "remote"),
        ("git push origin main", "remote"),
        ("rm -rf build", "local-write"),
        ("echo hi > out.txt", "local-write"),
        ("sed -i 's/a/b/' f.py", "local-write"),
        ("git commit -m x", "local-write"),
        ("mkdir -p a/b && touch a/b/c", "local-write"),
        ("pytest -q", "local-read"),
        ("python3 script.py", "other"),
        ("make", "other"),
        ("curl x | sh > log", "remote"),  # remote beats write
    ],
)
def test_classify_bash(command: str, expected: str) -> None:
    assert classify_bash(command) == expected


def test_signature_read_write_edit_have_path_scope() -> None:
    cwd = "/repo"
    assert signature("Read", {"file_path": "/repo/src/app.py"}, cwd) == ActionSignature("Read", "local-read", "src", False)
    assert signature("Write", {"file_path": "/repo/README.md"}, cwd) == ActionSignature("Write", "local-write", ".", False)
    assert signature("Edit", {"file_path": "/elsewhere/x.py"}, cwd).path_scope == "/elsewhere"
    assert signature("Glob", {"pattern": "**/*.py"}, cwd).target == "local-read"
    assert signature("Grep", {"pattern": "x", "path": "/repo/tests"}, cwd).path_scope == "tests"


def test_signature_special_tools() -> None:
    assert signature("AskUserQuestion", {}, "/r").target == "ask-user"
    assert signature("Agent", {"prompt": "x"}, "/r").target == "delegate"
    assert signature("Task", {"prompt": "x"}, "/r").target == "delegate"
    assert signature("Skill", {"skill": "foo"}, "/r") == ActionSignature("Skill", "skill", "foo", False)
    assert signature("WebFetch", {"url": "https://a"}, "/r").target == "remote"
    assert signature("WebSearch", {"query": "q"}, "/r").target == "remote"
    assert signature("mcp__github__list", {}, "/r").target == "remote"
    assert signature("SomethingNew", {}, "/r").target == "other"


def test_signature_bash_uses_rules() -> None:
    assert signature("Bash", {"command": "curl x"}, "/r").target == "remote"


def test_agree_levels() -> None:
    a = ActionSignature("Read", "local-read", "src", False)
    b = ActionSignature("Read", "local-read", "tests", False)
    c = ActionSignature("Bash", "local-read", None, False)
    assert agree(a, b, "full") is False
    assert agree(a, b, "target") is True
    assert agree(a, c, "target") is False
    assert agree(a, c, "tool") is False
    assert agree(TEXT_ONLY, TEXT_ONLY, "full") is True
    assert agree(TEXT_ONLY, a, "tool") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_classify.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement classify.py**

```python
from __future__ import annotations

import os
import re
import shlex
from typing import Any

from claude_drift.models import ActionSignature, RecordedAction

TEXT_ONLY = ActionSignature(tool="", target="text", path_scope=None, text_only=True)

REMOTE_CMDS = {"curl", "wget", "ssh", "scp", "rsync", "pip", "pip3", "uv", "npm", "npx", "pnpm", "yarn",
               "brew", "docker", "gcloud", "aws", "az"}
REMOTE_GIT = {"push", "pull", "fetch", "clone"}
REMOTE_GH = {"api", "pr", "issue", "repo", "release", "run"}
WRITE_CMDS = {"rm", "mv", "cp", "mkdir", "touch", "chmod", "chown", "ln", "tee", "truncate", "dd"}
WRITE_GIT = {"commit", "add", "rm", "mv", "checkout", "switch", "reset", "rebase", "merge", "stash", "tag"}
READ_CMDS = {"ls", "cat", "head", "tail", "less", "more", "grep", "rg", "ag", "find", "fd", "wc", "stat",
             "file", "du", "df", "tree", "pwd", "which", "echo", "printf", "env", "printenv", "diff",
             "sort", "uniq", "cut", "awk", "sed", "jq", "yq", "pytest", "ruff", "mypy", "tsc", "eslint",
             "cargo", "go"}
READ_GIT = {"log", "status", "diff", "show", "branch", "blame", "rev-parse", "rev-list", "remote", "ls-files"}
REDIRECT = re.compile(r"(?<![<>])>{1,2}(?!&)")


def _segments(command: str) -> list[list[str]]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    segs: list[list[str]] = [[]]
    for t in tokens:
        if t in {"|", "||", "&&", ";", "|&"}:
            segs.append([])
        else:
            segs[-1].append(t)
    return [s for s in segs if s]


def _segment_class(seg: list[str]) -> str:
    head = os.path.basename(seg[0])
    args = seg[1:]
    if head == "sudo" and args:
        return _segment_class(args)
    if head in REMOTE_CMDS:
        return "remote"
    if head == "git":
        sub = next((a for a in args if not a.startswith("-")), "")
        if sub in REMOTE_GIT:
            return "remote"
        if sub in WRITE_GIT:
            return "local-write"
        if sub in READ_GIT:
            return "local-read"
        return "other"
    if head == "gh":
        sub = next((a for a in args if not a.startswith("-")), "")
        return "remote" if sub in REMOTE_GH else "other"
    if head == "sed" and any(a == "-i" or a.startswith("-i") for a in args):
        return "local-write"
    if head in WRITE_CMDS:
        return "local-write"
    if head in READ_CMDS:
        return "local-read"
    return "other"


def classify_bash(command: str) -> str:
    classes = {_segment_class(s) for s in _segments(command)}
    if REDIRECT.search(command):
        classes.add("local-write")
    if "remote" in classes:
        return "remote"
    if "local-write" in classes:
        return "local-write"
    if "local-read" in classes and "other" not in classes:
        return "local-read"
    if "local-read" in classes:
        return "local-read"
    return "other"


def _path_scope(path: str | None, cwd: str) -> str | None:
    if not path:
        return None
    rel = os.path.relpath(path, cwd) if os.path.isabs(path) else path
    if rel.startswith(".."):
        return os.path.dirname(path) or "/"
    parts = rel.split(os.sep)
    if len(parts) > 1:
        return parts[0]
    return "." if "." in parts[0] else parts[0]  # "README.md" -> ".", "tests" -> "tests"


def signature(tool: str, tool_input: dict[str, Any], cwd: str) -> ActionSignature:
    if tool == "Bash":
        return ActionSignature(tool, classify_bash(str(tool_input.get("command", ""))), None, False)
    if tool in {"Read", "Glob", "Grep", "NotebookRead"}:
        p = tool_input.get("file_path") or tool_input.get("path")
        return ActionSignature(tool, "local-read", _path_scope(p, cwd), False)
    if tool in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        p = tool_input.get("file_path") or tool_input.get("notebook_path")
        return ActionSignature(tool, "local-write", _path_scope(p, cwd), False)
    if tool == "AskUserQuestion":
        return ActionSignature(tool, "ask-user", None, False)
    if tool in {"Agent", "Task"}:
        return ActionSignature(tool, "delegate", str(tool_input.get("subagent_type") or "") or None, False)
    if tool == "Skill":
        return ActionSignature(tool, "skill", str(tool_input.get("skill") or "") or None, False)
    if tool in {"WebFetch", "WebSearch"} or tool.startswith("mcp__"):
        return ActionSignature(tool, "remote", None, False)
    return ActionSignature(tool, "other", None, False)


def signature_of_recorded(rec: RecordedAction, cwd: str) -> ActionSignature:
    return signature(rec.tool, rec.input, cwd)


def agree(a: ActionSignature, b: ActionSignature, level: str = "target") -> bool:
    if level == "tool":
        return a.tool == b.tool and a.text_only == b.text_only
    if level == "target":
        return a.key == b.key
    return a == b
```

- [ ] **Step 4: Run tests, lint, types**

Run: `uv run pytest tests/test_classify.py -q && uv run ruff check . && uv run mypy`
Expected: all pass, clean. If a parametrized Bash case fails, fix the rule tables, not the test.

- [ ] **Step 5: Commit**

```bash
git add src/claude_drift/classify.py tests/test_classify.py
git commit -m "Classify tool calls into comparable action signatures"
```

---

### Task 6: Temporary session file and claude argv

**Files:**
- Create: `src/claude_drift/replay.py` (first half)
- Test: `tests/test_replay_session.py`

**Interfaces:**
- Consumes: `CutPoint` (Task 1), `read_lines` (Task 2)
- Produces:
  ```python
  @contextmanager
  def temp_session(cut: CutPoint) -> Iterator[str]     # yields temp session id; file written next to the original; deleted on exit
  def blocking_settings_path() -> Path                  # writes (once) a settings JSON that denies every tool via PreToolUse hook
  def build_argv(cut: CutPoint, temp_id: str, model: str, settings: Path) -> list[str]
  ```

- [ ] **Step 1: Write the failing tests**

`tests/test_replay_session.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay_session.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the first half of replay.py**

```python
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from claude_drift.ingest import read_lines
from claude_drift.models import CutPoint

BLOCK_ALL_SETTINGS = {
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo 'claude-drift: tool execution blocked during replay' >&2; exit 2",
                    }
                ],
            }
        ]
    }
}


def drift_home() -> Path:
    env = os.environ.get("CLAUDE_DRIFT_HOME")
    return Path(env) if env else Path.home() / ".claude-drift"


def blocking_settings_path() -> Path:
    home = drift_home()
    home.mkdir(parents=True, exist_ok=True)
    p = home / "replay-settings.json"
    p.write_text(json.dumps(BLOCK_ALL_SETTINGS, indent=2))
    return p


@contextmanager
def temp_session(cut: CutPoint) -> Iterator[str]:
    """Write lines[:cut.line_index] of the original session under a fresh id; delete afterwards."""
    temp_id = str(uuid.uuid4())
    original = Path(cut.session_path)
    target = original.with_name(f"{temp_id}.jsonl")
    out: list[str] = []
    for raw in read_lines(original)[: cut.line_index]:
        if not raw.strip():
            continue
        entry = json.loads(raw)
        if "sessionId" in entry:
            entry["sessionId"] = temp_id
        out.append(json.dumps(entry, ensure_ascii=False))
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        yield temp_id
    finally:
        try:
            target.unlink()
        except FileNotFoundError:
            pass


def build_argv(cut: CutPoint, temp_id: str, model: str, settings: Path) -> list[str]:
    return [
        "claude", "-p", cut.prompt,
        "--resume", temp_id,
        "--fork-session",
        "--model", model,
        "--max-turns", "1",
        "--no-session-persistence",
        "--output-format", "stream-json",
        "--verbose",
        "--settings", str(settings),
    ]
```

- [ ] **Step 4: Run tests, lint, types**

Run: `uv run pytest tests/test_replay_session.py -q && uv run ruff check . && uv run mypy`
Expected: `4 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add src/claude_drift/replay.py tests/test_replay_session.py
git commit -m "Write truncated temp sessions and build claude replay argv"
```

---

### Task 7: Stream parser and subprocess runner

**Files:**
- Modify: `src/claude_drift/replay.py` (append)
- Test: `tests/test_replay_runner.py`

**Interfaces:**
- Consumes: Task 6 functions, `signature` (Task 5), `ReplayResult` (Task 1)
- Produces:
  ```python
  class Runner(Protocol):
      def stream(self, argv: list[str], cwd: str, timeout: float) -> AbstractContextManager[Iterator[str]]: ...
  class SubprocessRunner: ...                       # real implementation; kills process on context exit or timeout
  @dataclass class ParsedTurn(tool_use: dict | None, usage: dict[str, int], error: str | None)
  def parse_stream(lines: Iterable[str]) -> ParsedTurn   # stops consuming at first tool_use
  def replay_cut(cut: CutPoint, model: str, role: str, runner: Runner, timeout: float = 180.0) -> ReplayResult
  ```

Stream-json facts (observed): lines are JSON objects with `type` in `system | assistant | user | result`. Assistant events carry `message.content` (list of blocks) and `message.usage`. A text block and a tool_use block for the same turn may arrive as **separate** assistant events. `result` carries `is_error`, `subtype`, and for auth failures the assistant text `Not logged in · Please run /login` with `message.model == "<synthetic>"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_replay_runner.py`:
```python
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from claude_drift.ingest import cuts_from_session
from claude_drift.replay import ParsedTurn, SubprocessRunner, parse_stream, replay_cut


def ev(type_: str, **kw: object) -> str:
    return json.dumps({"type": type_, **kw})


USAGE = {"input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 7}


def assistant(blocks: list[dict[str, object]], model: str = "claude-sonnet-5") -> str:
    return ev("assistant", message={"model": model, "content": blocks, "usage": USAGE})


class FakeRunner:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.calls: list[tuple[list[str], str]] = []
        self.consumed = 0
        self.closed = False

    @contextmanager
    def stream(self, argv: list[str], cwd: str, timeout: float) -> Iterator[Iterator[str]]:
        self.calls.append((argv, cwd))

        def gen() -> Iterator[str]:
            for line in self.lines:
                self.consumed += 1
                yield line

        try:
            yield gen()
        finally:
            self.closed = True


def test_parse_stream_stops_at_first_tool_use() -> None:
    lines = [
        ev("system", subtype="init"),
        assistant([{"type": "text", "text": "thinking aloud"}]),
        assistant([{"type": "tool_use", "name": "Read", "input": {"file_path": "/r/a.py"}}]),
        assistant([{"type": "tool_use", "name": "Bash", "input": {"command": "never"}}]),
        ev("result", subtype="success"),
    ]
    it = iter(lines)
    turn = parse_stream(it)
    assert turn.tool_use == {"name": "Read", "input": {"file_path": "/r/a.py"}}
    assert turn.usage["cache_creation_input_tokens"] == 100
    assert turn.error is None
    assert next(it).startswith('{"type": "assistant"')  # the Bash line was never consumed


def test_parse_stream_text_only_result() -> None:
    lines = [assistant([{"type": "text", "text": "All done."}]), ev("result", subtype="success", is_error=False)]
    turn = parse_stream(iter(lines))
    assert turn == ParsedTurn(tool_use=None, usage=USAGE, error=None)


def test_parse_stream_auth_error() -> None:
    lines = [
        assistant([{"type": "text", "text": "Not logged in · Please run /login"}], model="<synthetic>"),
        ev("result", subtype="success", is_error=True),
    ]
    turn = parse_stream(iter(lines))
    assert turn.tool_use is None
    assert turn.error is not None and "Not logged in" in turn.error


def test_parse_stream_ignores_garbage_and_empty_stream() -> None:
    assert parse_stream(iter(["not json", ""])).error == "stream ended without result"


def test_replay_cut_builds_result_and_cleans_up(projects_dir: Path, drift_home: Path) -> None:
    cut = cuts_from_session(projects_dir / "-fake-project" / "alpha.jsonl")[0]
    runner = FakeRunner([
        assistant([{"type": "tool_use", "name": "Bash", "input": {"command": "curl https://x"}}]),
        ev("result", subtype="success"),
    ])
    res = replay_cut(cut, "claude-sonnet-5", "candidate", runner)
    assert res.cut_id == "alpha:0" and res.model == "claude-sonnet-5" and res.role == "candidate"
    assert res.signature is not None and res.signature.key == "Bash/remote"
    assert res.raw_tool_use == {"name": "Bash", "input": {"command": "curl https://x"}}
    assert res.usage == USAGE and res.error is None and res.duration_ms >= 0
    argv, cwd = runner.calls[0]
    assert cwd == cut.cwd and argv[0] == "claude" and argv[2] == "list the repo"
    assert runner.closed
    leftovers = [p for p in (projects_dir / "-fake-project").glob("*.jsonl") if p.stem not in {"alpha", "beta", "gamma"}]
    assert leftovers == []


def test_replay_cut_text_only_signature(projects_dir: Path, drift_home: Path) -> None:
    cut = cuts_from_session(projects_dir / "-fake-project" / "alpha.jsonl")[0]
    runner = FakeRunner([assistant([{"type": "text", "text": "ok"}]), ev("result", subtype="success")])
    res = replay_cut(cut, "m", "noise", runner)
    assert res.signature is not None and res.signature.text_only and res.signature.key == "text"


def test_replay_cut_runner_exception_becomes_error(projects_dir: Path, drift_home: Path) -> None:
    cut = cuts_from_session(projects_dir / "-fake-project" / "alpha.jsonl")[0]

    class Boom:
        @contextmanager
        def stream(self, argv: list[str], cwd: str, timeout: float) -> Iterator[Iterator[str]]:
            raise TimeoutError("timed out after 180s")
            yield iter([])  # pragma: no cover

    res = replay_cut(cut, "m", "candidate", Boom())
    assert res.signature is None and res.error == "TimeoutError: timed out after 180s"


def test_subprocess_runner_streams_and_kills(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    argv = ["python3", "-c", "import sys,time\nprint('a');sys.stdout.flush();print('b');sys.stdout.flush();time.sleep(30)"]
    with runner.stream(argv, str(tmp_path), timeout=5.0) as lines:
        assert next(lines) == "a"
    # context exit must have killed the sleeping process well before 30s
    assert runner.last_returncode is not None


def test_subprocess_runner_timeout(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    argv = ["python3", "-c", "import time; time.sleep(30)"]
    with runner.stream(argv, str(tmp_path), timeout=0.5) as lines:
        assert list(lines) == []
    assert runner.timed_out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_replay_runner.py -q`
Expected: FAIL with `ImportError: cannot import name 'ParsedTurn'`.

- [ ] **Step 3: Append the runner half to replay.py**

Add these imports at the top of `replay.py` (merge with the existing ones):
```python
import subprocess
import threading
import time
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

from claude_drift.classify import TEXT_ONLY, signature
from claude_drift.models import ReplayResult
```

Append:
```python
@dataclass
class ParsedTurn:
    tool_use: dict[str, Any] | None
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def parse_stream(lines: Iterable[str]) -> ParsedTurn:
    usage: dict[str, int] = {}
    texts: list[str] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "assistant":
            msg = ev.get("message", {})
            if isinstance(msg.get("usage"), dict):
                usage = {k: int(v) for k, v in msg["usage"].items() if isinstance(v, int | float)}
            synthetic = msg.get("model") == "<synthetic>"
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
                if block.get("type") == "tool_use" and not synthetic:
                    return ParsedTurn(
                        tool_use={"name": block.get("name"), "input": block.get("input", {})}, usage=usage
                    )
            if synthetic:
                return ParsedTurn(tool_use=None, usage=usage, error=" ".join(texts) or "synthetic error")
        elif ev.get("type") == "result":
            if ev.get("is_error"):
                return ParsedTurn(tool_use=None, usage=usage, error=" ".join(texts) or str(ev.get("subtype")))
            return ParsedTurn(tool_use=None, usage=usage, error=None)
    return ParsedTurn(tool_use=None, usage=usage, error="stream ended without result")


class Runner(Protocol):
    def stream(self, argv: list[str], cwd: str, timeout: float) -> AbstractContextManager[Iterator[str]]: ...


class SubprocessRunner:
    """Runs argv, yields stdout lines, kills the process on context exit or timeout."""

    def __init__(self) -> None:
        self.last_returncode: int | None = None
        self.timed_out = False

    @contextmanager
    def stream(self, argv: list[str], cwd: str, timeout: float) -> Iterator[Iterator[str]]:
        self.timed_out = False
        proc = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        )

        def on_timeout() -> None:
            self.timed_out = True
            proc.kill()

        timer = threading.Timer(timeout, on_timeout)
        timer.start()

        def gen() -> Iterator[str]:
            assert proc.stdout is not None
            for line in proc.stdout:
                yield line.rstrip("\n")

        try:
            yield gen()
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.last_returncode = proc.returncode


def replay_cut(cut: CutPoint, model: str, role: str, runner: Runner, timeout: float = 180.0) -> ReplayResult:
    start = time.monotonic()
    settings = blocking_settings_path()
    try:
        with temp_session(cut) as temp_id:
            argv = build_argv(cut, temp_id, model, settings)
            with runner.stream(argv, cut.cwd, timeout) as lines:
                turn = parse_stream(lines)
    except Exception as exc:  # noqa: BLE001 - any runner failure is recorded, never raised
        return ReplayResult(
            cut_id=cut.cut_id, model=model, role=role, signature=None, raw_tool_use=None, usage={},
            error=f"{type(exc).__name__}: {exc}", duration_ms=int((time.monotonic() - start) * 1000),
        )
    duration = int((time.monotonic() - start) * 1000)
    if turn.error is not None:
        return ReplayResult(cut.cut_id, model, role, None, None, turn.usage, turn.error, duration)
    if turn.tool_use is None:
        return ReplayResult(cut.cut_id, model, role, TEXT_ONLY, None, turn.usage, None, duration)
    sig = signature(str(turn.tool_use["name"]), dict(turn.tool_use["input"]), cut.cwd)
    return ReplayResult(cut.cut_id, model, role, sig, turn.tool_use, turn.usage, None, duration)
```

- [ ] **Step 4: Run tests, lint, types**

Run: `uv run pytest tests/test_replay_runner.py -q && uv run ruff check . && uv run mypy`
Expected: `9 passed`, clean. The two subprocess tests must finish in under 10 seconds total.

- [ ] **Step 5: Commit**

```bash
git add src/claude_drift/replay.py tests/test_replay_runner.py
git commit -m "Parse stream-json and run claude replays with kill-on-first-tool-use"
```

---

### Task 8: Live probe (DRIFT_LIVE=1) — hooks override and cache reuse

**Files:**
- Create: `tests/test_replay_live.py`
- Modify: `docs/superpowers/specs/2026-09-05-claude-drift-design.md` §11 (record findings)

**Interfaces:**
- Consumes: `ingest`, `projects_root`, `replay_cut`, `SubprocessRunner`

This task spends real subscription tokens (about 2 to 4 cuts, ~100k input tokens each). Run it once, record what you learn, and never run it in CI.

- [ ] **Step 1: Write the live test**

`tests/test_replay_live.py`:
```python
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from claude_drift.ingest import ingest, projects_root
from claude_drift.replay import SubprocessRunner, replay_cut
from claude_drift.sample import batch_by_session

pytestmark = pytest.mark.skipif(os.environ.get("DRIFT_LIVE") != "1", reason="set DRIFT_LIVE=1 to call claude")


def test_live_replay_two_cuts_same_session_reports_cache_and_no_tool_ran(tmp_path: Path) -> None:
    cuts = ingest(projects_root())
    batches = [b for b in batch_by_session(cuts) if len(b) >= 2]
    assert batches, "need a local session with at least two human prompts followed by tool use"
    batch = min(batches, key=lambda b: Path(b[0].session_path).stat().st_size)[:2]
    runner = SubprocessRunner()
    results = [replay_cut(c, "sonnet", "candidate", runner, timeout=240) for c in batch]
    (tmp_path / "live.json").write_text(json.dumps([r.__dict__ | {"signature": str(r.signature)} for r in results], default=str, indent=1))
    print((tmp_path / "live.json").read_text())
    for r in results:
        assert r.error is None, r.error
        assert r.signature is not None
    # second cut of the same session should read more cache than it creates if prefix caching works
    second = results[1].usage
    print("cache_read", second.get("cache_read_input_tokens"), "cache_creation", second.get("cache_creation_input_tokens"))
```

- [ ] **Step 2: Run it once**

Run: `DRIFT_LIVE=1 uv run pytest tests/test_replay_live.py -q -s 2>&1 | tail -40`
Expected: `1 passed`, printed JSON with two signatures, usage numbers for both cuts.

- [ ] **Step 3: Verify no tool executed and no temp session survived**

Run:
```bash
ls ~/.claude/projects/*/ | grep -c jsonl
find ~/.claude/projects -maxdepth 2 -name '*.jsonl' -newer pyproject.toml
```
Expected: the second command prints nothing (no new session files). If it prints a file, `temp_session` cleanup or `--no-session-persistence` is not holding; fix before continuing.

- [ ] **Step 4: Check whether user hooks fired**

Temporarily add `print(line)` in `SubprocessRunner.gen` (or rerun with a small script) and look for `"subtype": "hook_started"` events. Record in spec §11:
- if `hook_started` events for SessionStart/PreToolUse appear, `--settings` **merges**; note that the deny hook still returned exit 2 (check for the blocked-tool message in the stream's `user` tool_result event if present).
- if no hook events appear, `--settings` **overrides**.
Also record the second cut's `cache_read_input_tokens` vs `cache_creation_input_tokens` and whether the same-session ordering is worth keeping.

Replace the three bullets of §11 with concrete findings dated 2026-09-XX. Remove the `print` you added.

- [ ] **Step 5: Commit**

```bash
git add tests/test_replay_live.py docs/superpowers/specs/2026-09-05-claude-drift-design.md
git commit -m "Add opt-in live replay probe and record hook/cache findings"
```

---

### Task 9: Run store

**Files:**
- Create: `src/claude_drift/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `drift_home` (Task 6), models (Task 1)
- Produces:
  ```python
  @dataclass class Run(path: Path)
      .manifest_path .cuts_path .replays_path .report_path
      def write_manifest(self, data: dict) ; def read_manifest(self) -> dict
      def write_cuts(self, cuts: list[CutPoint]) ; def read_cuts(self) -> list[CutPoint]
      def append_replay(self, r: ReplayResult)  # thread-safe append
      def read_replays(self) -> list[ReplayResult]
  def new_run(now: datetime | None = None) -> Run          # ~/.claude-drift/runs/YYYYMMDD-HHMMSS
  def latest_run() -> Run | None
  def get_run(run_id: str) -> Run                          # raises FileNotFoundError
  ```

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:
```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Thread

import pytest

from claude_drift.models import ActionSignature, CutPoint, RecordedAction, ReplayResult
from claude_drift.store import get_run, latest_run, new_run


def cut(i: int) -> CutPoint:
    return CutPoint(f"s:{i}", "/p/s.jsonl", "s", i, "p", RecordedAction("Bash", {"command": "ls"}), "m", "/c", "v")


def res(i: int) -> ReplayResult:
    return ReplayResult(f"s:{i}", "m2", "candidate", ActionSignature("Bash", "local-read", None, False), None, {"output_tokens": i}, None, 5)


def test_new_run_layout_and_roundtrip(drift_home: Path) -> None:
    run = new_run(datetime(2026, 9, 5, 13, 0, 0))
    assert run.path == drift_home / "runs" / "20260905-130000"
    run.write_manifest({"from": "a", "to": "b"})
    run.write_cuts([cut(1), cut(2)])
    run.append_replay(res(1))
    run.append_replay(res(2))
    assert run.read_manifest() == {"from": "a", "to": "b"}
    assert run.read_cuts() == [cut(1), cut(2)]
    assert run.read_replays() == [res(1), res(2)]


def test_latest_and_get(drift_home: Path) -> None:
    assert latest_run() is None
    new_run(datetime(2026, 9, 5, 13, 0, 0))
    r2 = new_run(datetime(2026, 9, 6, 13, 0, 0))
    lr = latest_run()
    assert lr is not None and lr.path == r2.path
    assert get_run("20260905-130000").path.name == "20260905-130000"
    with pytest.raises(FileNotFoundError):
        get_run("nope")


def test_append_replay_is_thread_safe(drift_home: Path) -> None:
    run = new_run(datetime(2026, 9, 5, 13, 0, 0))
    threads = [Thread(target=lambda i=i: run.append_replay(res(i))) for i in range(50)]  # type: ignore[misc]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = run.read_replays()
    assert len(got) == 50 and sorted(r.usage["output_tokens"] for r in got) == list(range(50))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement store.py**

```python
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_drift.models import CutPoint, ReplayResult, cut_from_dict, replay_from_dict, to_dict
from claude_drift.replay import drift_home


def runs_root() -> Path:
    return drift_home() / "runs"


@dataclass
class Run:
    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    @property
    def cuts_path(self) -> Path:
        return self.path / "cuts.jsonl"

    @property
    def replays_path(self) -> Path:
        return self.path / "replays.jsonl"

    @property
    def report_path(self) -> Path:
        return self.path / "report.md"

    def write_manifest(self, data: dict[str, Any]) -> None:
        self.manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def read_manifest(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.manifest_path.read_text())
        return loaded

    def write_cuts(self, cuts: list[CutPoint]) -> None:
        with self.cuts_path.open("w", encoding="utf-8") as f:
            for c in cuts:
                f.write(json.dumps(to_dict(c), ensure_ascii=False) + "\n")

    def read_cuts(self) -> list[CutPoint]:
        if not self.cuts_path.exists():
            return []
        return [cut_from_dict(json.loads(line)) for line in self.cuts_path.read_text().splitlines() if line.strip()]

    def append_replay(self, r: ReplayResult) -> None:
        line = json.dumps(to_dict(r), ensure_ascii=False) + "\n"
        with self._lock, self.replays_path.open("a", encoding="utf-8") as f:
            f.write(line)

    def read_replays(self) -> list[ReplayResult]:
        if not self.replays_path.exists():
            return []
        return [replay_from_dict(json.loads(line)) for line in self.replays_path.read_text().splitlines() if line.strip()]


def new_run(now: datetime | None = None) -> Run:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = runs_root() / stamp
    path.mkdir(parents=True, exist_ok=True)
    return Run(path)


def latest_run() -> Run | None:
    root = runs_root()
    if not root.is_dir():
        return None
    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    return Run(dirs[-1]) if dirs else None


def get_run(run_id: str) -> Run:
    path = runs_root() / run_id
    if not path.is_dir():
        raise FileNotFoundError(f"no run named {run_id} under {runs_root()}")
    return Run(path)
```

- [ ] **Step 4: Run tests, lint, types**

Run: `uv run pytest tests/test_store.py -q && uv run ruff check . && uv run mypy`
Expected: `3 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add src/claude_drift/store.py tests/test_store.py
git commit -m "Add run store with manifest, cuts, and thread-safe replay log"
```

---

### Task 10: `drift replay` command

**Files:**
- Modify: `src/claude_drift/cli.py`
- Test: `tests/test_cli_replay.py`

**Interfaces:**
- Consumes: `ingest`, `sample_cuts`, `batch_by_session`, `replay_cut`, `SubprocessRunner`, `Runner`, `Run`, `new_run`
- Produces: `replay --from A --to B [--turns N] [--workers W] [--no-noise] [--project PATH] [--seed S] [--timeout SEC] [--yes]`, plus `run_replay(...)` function used by tests with an injected runner.

Behaviour:
- `--from` matches recorded models by exact equality or substring (`opus-5` matches `claude-opus-5`). If it matches more than one distinct recorded model, abort with the list.
- Prints an estimate before starting: `cuts × (2 if noise else 1) × 100k input tokens` and asks for confirmation unless `--yes`.
- Runs session batches in a `ThreadPoolExecutor(max_workers=workers)`. Within a batch: all cuts ascending with model B, then (if noise) all cuts ascending with model A.
- Appends each `ReplayResult` to the run immediately.
- Aborts with exit code 1 if, after at least 5 completed replays, errors exceed 20% of completed.
- Writes manifest at start (status `running`) and at end (status `done` or `aborted`, with counts).
- Prints the run id at the end.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_replay.py`:
```python
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from click.testing import CliRunner

from claude_drift.cli import main, run_replay
from claude_drift.store import latest_run


def assistant_tool(name: str, inp: dict[str, object]) -> str:
    return json.dumps({"type": "assistant", "message": {"model": "x", "content": [{"type": "tool_use", "name": name, "input": inp}], "usage": {"output_tokens": 1}}})


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
        line = assistant_tool("Read", {"file_path": "/r/a"}) if model == "new" else assistant_tool("Bash", {"command": "ls"})
        yield iter([line, json.dumps({"type": "result"})])


def test_run_replay_writes_run_with_noise(projects_dir: Path, drift_home: Path) -> None:
    runner = ScriptedRunner()
    run = run_replay(from_model="opus-5", to_model="new", turns=10, workers=2, noise=True, project=None,
                     seed=0, timeout=1.0, runner=runner, echo=lambda s: None)
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
    run = run_replay(from_model="claude-opus-5", to_model="new", turns=10, workers=1, noise=False, project=None,
                     seed=0, timeout=1.0, runner=ScriptedRunner(), echo=lambda s: None)
    assert {r.role for r in run.read_replays()} == {"candidate"}
    assert run.read_manifest()["noise"] is False


def test_run_replay_aborts_on_error_rate(projects_dir: Path, drift_home: Path) -> None:
    # every replay fails -> after >=5 completed with 100% errors the run is aborted
    run = run_replay(from_model="opus-5", to_model="new", turns=10, workers=1, noise=True, project=None,
                     seed=0, timeout=1.0, runner=ScriptedRunner(fail_models={"new", "claude-opus-5"}), echo=lambda s: None)
    m = run.read_manifest()
    assert m["status"] == "aborted"
    assert m["errors"] >= 5


def test_cli_replay_dry_estimate_and_yes(projects_dir: Path, drift_home: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claude_drift.cli as cli

    monkeypatch.setattr(cli, "SubprocessRunner", ScriptedRunner)
    result = CliRunner().invoke(main, ["replay", "--from", "opus-5", "--to", "new", "--turns", "2", "--yes"])
    assert result.exit_code == 0, result.output
    assert "estimated input tokens" in result.output
    assert "run id:" in result.output
    lr = latest_run()
    assert lr is not None and len(lr.read_cuts()) == 2


def test_cli_replay_ambiguous_from(projects_dir: Path, drift_home: Path) -> None:
    slug = projects_dir / "-fake-project"
    text = (slug / "alpha.jsonl").read_text().replace("claude-opus-5", "claude-opus-4-9").replace('"alpha"', '"delta"')
    (slug / "delta.jsonl").write_text(text)
    result = CliRunner().invoke(main, ["replay", "--from", "opus", "--to", "new", "--yes"])
    assert result.exit_code != 0
    assert "matches more than one" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_replay.py -q`
Expected: FAIL with `ImportError: cannot import name 'run_replay'`.

- [ ] **Step 3: Add `run_replay` and the `replay` command to cli.py**

Add imports:
```python
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from claude_drift.models import CutPoint, ReplayResult
from claude_drift.replay import Runner, SubprocessRunner, replay_cut
from claude_drift.sample import batch_by_session, sample_cuts
from claude_drift.store import Run, new_run
```

Add after `scan`:
```python
TOKENS_PER_CUT_ESTIMATE = 100_000
ABORT_ERROR_RATE = 0.20
ABORT_MIN_COMPLETED = 5


def resolve_from_model(cuts: list[CutPoint], from_model: str) -> str:
    names = sorted({c.model for c in cuts})
    exact = [n for n in names if n == from_model]
    if exact:
        return exact[0]
    partial = [n for n in names if from_model in n]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise click.ClickException(f"--from {from_model!r} matches no recorded model. Recorded: {', '.join(names) or 'none'}")
    raise click.ClickException(f"--from {from_model!r} matches more than one recorded model: {', '.join(partial)}")


def run_replay(
    *, from_model: str, to_model: str, turns: int, workers: int, noise: bool, project: str | None,
    seed: int, timeout: float, runner: Runner, echo: Callable[[str], None],
) -> Run:
    all_cuts = ingest(projects_root(), project=project)
    if not all_cuts:
        raise click.ClickException("no replayable cuts found; run `drift scan`")
    resolved_from = resolve_from_model(all_cuts, from_model)
    cuts = sample_cuts([c for c in all_cuts if c.model == resolved_from], turns=turns, seed=seed)
    if not cuts:
        raise click.ClickException(f"no cuts recorded with model {resolved_from}")
    run = new_run(datetime.now())
    run.write_cuts(cuts)
    manifest = {
        "from": resolved_from, "to": to_model, "turns": len(cuts), "seed": seed, "workers": workers,
        "noise": noise, "timeout": timeout, "started": datetime.now().isoformat(), "status": "running",
    }
    run.write_manifest(manifest)

    state = {"completed": 0, "errors": 0, "aborted": False}
    lock = threading.Lock()

    def record(r: ReplayResult) -> None:
        run.append_replay(r)
        with lock:
            state["completed"] += 1
            if r.error:
                state["errors"] += 1
            if state["completed"] >= ABORT_MIN_COMPLETED and state["errors"] / state["completed"] > ABORT_ERROR_RATE:
                state["aborted"] = True
        echo(f"[{state['completed']}/{len(cuts) * (2 if noise else 1)}] {r.cut_id} {r.model} -> {r.signature.key if r.signature else r.error}")

    def work(batch: list[CutPoint]) -> None:
        for model, role in ([(to_model, "candidate")] + ([(resolved_from, "noise")] if noise else [])):
            for cut in batch:
                if state["aborted"]:
                    return
                record(replay_cut(cut, model, role, runner, timeout=timeout))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, batch_by_session(cuts)))

    manifest.update({
        "finished": datetime.now().isoformat(), "completed": state["completed"], "errors": state["errors"],
        "status": "aborted" if state["aborted"] else "done",
    })
    run.write_manifest(manifest)
    return run


@main.command()
@click.option("--from", "from_model", required=True, help="Recorded model, e.g. opus-5 or claude-opus-5.")
@click.option("--to", "to_model", required=True, help="Model to replay with, passed to `claude --model`.")
@click.option("--turns", default=60, show_default=True, type=int)
@click.option("--workers", default=4, show_default=True, type=int)
@click.option("--no-noise", "no_noise", is_flag=True, help="Skip the old-model replay that measures the noise band.")
@click.option("--project", type=click.Path(), default=None)
@click.option("--seed", default=0, show_default=True, type=int)
@click.option("--timeout", default=180.0, show_default=True, type=float, help="Seconds per replay.")
@click.option("--yes", "-y", is_flag=True, help="Skip the cost confirmation.")
def replay(from_model: str, to_model: str, turns: int, workers: int, no_noise: bool, project: str | None,
           seed: int, timeout: float, yes: bool) -> None:
    """Replay sampled cuts with the new model (and the old one for the noise band). Uses your claude login."""
    noise = not no_noise
    all_cuts = ingest(projects_root(), project=project)
    if not all_cuts:
        raise click.ClickException("no replayable cuts found; run `drift scan`")
    resolved = resolve_from_model(all_cuts, from_model)
    n = min(turns, len([c for c in all_cuts if c.model == resolved]))
    est = n * (2 if noise else 1) * TOKENS_PER_CUT_ESTIMATE
    click.echo(f"cuts: {n}  replays: {n * (2 if noise else 1)}  estimated input tokens: {est:,}")
    if not yes:
        click.confirm("continue?", abort=True)
    run = run_replay(from_model=from_model, to_model=to_model, turns=turns, workers=workers, noise=noise,
                     project=project, seed=seed, timeout=timeout, runner=SubprocessRunner(), echo=click.echo)
    m = run.read_manifest()
    click.echo(f"status: {m['status']}  completed: {m['completed']}  errors: {m['errors']}")
    click.echo(f"run id: {run.path.name}")
    if m["status"] == "aborted":
        raise click.exceptions.Exit(1)
```

- [ ] **Step 4: Run tests, lint, types**

Run: `uv run pytest tests/test_cli_replay.py -q && uv run ruff check . && uv run mypy`
Expected: `5 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add src/claude_drift/cli.py tests/test_cli_replay.py
git commit -m "Add drift replay command with workers, noise pass, and abort threshold"
```

---

### Task 11: Statistics: agreement, noise band, transitions

**Files:**
- Create: `src/claude_drift/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Consumes: `CutPoint`, `ReplayResult`, `ActionSignature` (Task 1), `signature_of_recorded`, `agree` (Task 5)
- Produces:
  ```python
  @dataclass(frozen=True) class Interval(low: float, high: float)
  @dataclass(frozen=True) class Transition(src: str, dst: str, candidate_count: int, noise_count: int,
                                          delta: int, interval: Interval, real: bool, flagged: bool)
  @dataclass(frozen=True) class DriftStats(cuts: int, sessions: int, candidate_agreement: float,
                                          noise_agreement: float | None, noise_band: Interval | None,
                                          transitions: list[Transition], errors: int)
  def compute(cuts: list[CutPoint], replays: list[ReplayResult], seed: int = 0, n_boot: int = 1000) -> DriftStats
  ```

Definitions:
- Only cuts with a successful `candidate` result (and, when noise is on, also a successful `noise` result) count.
- `candidate_agreement` = fraction of counted cuts where `agree(recorded, candidate, "target")`.
- `noise_agreement` = same with the noise replay; `noise_band` = bootstrap 95% interval of `noise_agreement` (resample cuts with replacement, `n_boot` times, seeded).
- A transition is a pair `(recorded.key, replay.key)` with `recorded.key != replay.key`. `candidate_count` and `noise_count` are how many counted cuts show that transition for the candidate and noise replays. `delta = candidate_count - noise_count`. `interval` is the bootstrap 95% interval of `delta`. `real = interval.low > 0 or interval.high < 0`. When noise is off, `noise_count = 0`, `interval = Interval(delta, delta)`, `real = False`.
- `flagged` = the recorded key's target is `local-write` or `delegate` and the destination's target is not.
- Transitions sorted by `candidate_count` descending, then `src`, `dst`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stats.py`:
```python
from __future__ import annotations

from claude_drift.models import ActionSignature, CutPoint, RecordedAction, ReplayResult
from claude_drift.stats import Interval, compute

BASH = ActionSignature("Bash", "local-read", None, False)
READ = ActionSignature("Read", "local-read", "src", False)
WRITE = ActionSignature("Write", "local-write", ".", False)


def cut(i: int, tool: str = "Bash", cmd: str = "ls") -> CutPoint:
    return CutPoint(f"s{i % 5}:{i}", f"/p/s{i % 5}.jsonl", f"s{i % 5}", i, "p", RecordedAction(tool, {"command": cmd} if tool == "Bash" else {"file_path": "/c/README.md"}), "old", "/c", "v")


def rep(i: int, role: str, sig: ActionSignature | None, error: str | None = None) -> ReplayResult:
    return ReplayResult(f"s{i % 5}:{i}", "new" if role == "candidate" else "old", role, sig, None, {}, error, 1)


def test_perfect_agreement_no_transitions() -> None:
    cuts = [cut(i) for i in range(20)]
    replays = [rep(i, "candidate", BASH) for i in range(20)] + [rep(i, "noise", BASH) for i in range(20)]
    s = compute(cuts, replays)
    assert s.cuts == 20 and s.sessions == 5
    assert s.candidate_agreement == 1.0 and s.noise_agreement == 1.0
    assert s.noise_band == Interval(1.0, 1.0)
    assert s.transitions == [] and s.errors == 0


def test_systematic_shift_is_real_and_noise_shift_is_not() -> None:
    cuts = [cut(i) for i in range(40)]
    # candidate: every cut switches Bash -> Read ; noise: old model agrees with itself always
    replays = [rep(i, "candidate", READ) for i in range(40)] + [rep(i, "noise", BASH) for i in range(40)]
    s = compute(cuts, replays, seed=0)
    assert s.candidate_agreement == 0.0 and s.noise_agreement == 1.0
    t = s.transitions[0]
    assert (t.src, t.dst) == ("Bash/local-read", "Read/local-read")
    assert t.candidate_count == 40 and t.noise_count == 0 and t.delta == 40
    assert t.real is True and t.flagged is False


def test_equal_noise_and_candidate_shift_is_not_real() -> None:
    cuts = [cut(i) for i in range(40)]
    # both replays disagree with the record on the same 10 cuts -> difference is 0
    replays = [rep(i, "candidate", READ if i < 10 else BASH) for i in range(40)]
    replays += [rep(i, "noise", READ if i < 10 else BASH) for i in range(40)]
    s = compute(cuts, replays, seed=0)
    assert s.candidate_agreement == 0.75 and s.noise_agreement == 0.75
    assert s.noise_band is not None and s.noise_band.low < 0.75 < s.noise_band.high
    t = s.transitions[0]
    assert t.delta == 0 and t.real is False


def test_flagged_when_write_disappears() -> None:
    cuts = [cut(i, tool="Write") for i in range(10)]
    replays = [rep(i, "candidate", READ) for i in range(10)] + [rep(i, "noise", WRITE) for i in range(10)]
    s = compute(cuts, replays)
    assert s.transitions[0].flagged is True


def test_errors_excluded_and_counted() -> None:
    cuts = [cut(i) for i in range(10)]
    replays = [rep(i, "candidate", None, error="timeout") if i < 3 else rep(i, "candidate", BASH) for i in range(10)]
    replays += [rep(i, "noise", BASH) for i in range(10)]
    s = compute(cuts, replays)
    assert s.cuts == 7 and s.errors == 3


def test_no_noise_mode() -> None:
    cuts = [cut(i) for i in range(10)]
    replays = [rep(i, "candidate", READ) for i in range(10)]
    s = compute(cuts, replays)
    assert s.noise_agreement is None and s.noise_band is None
    assert s.transitions[0].real is False and s.transitions[0].interval == Interval(10, 10)


def test_bootstrap_is_deterministic() -> None:
    cuts = [cut(i) for i in range(30)]
    replays = [rep(i, "candidate", READ if i % 3 else BASH) for i in range(30)]
    replays += [rep(i, "noise", READ if i % 7 == 0 else BASH) for i in range(30)]
    assert compute(cuts, replays, seed=3) == compute(cuts, replays, seed=3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stats.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement stats.py**

```python
from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from claude_drift.classify import agree, signature_of_recorded
from claude_drift.models import ActionSignature, CutPoint, ReplayResult

FLAG_TARGETS = {"local-write", "delegate"}


@dataclass(frozen=True)
class Interval:
    low: float
    high: float


@dataclass(frozen=True)
class Transition:
    src: str
    dst: str
    candidate_count: int
    noise_count: int
    delta: int
    interval: Interval
    real: bool
    flagged: bool


@dataclass(frozen=True)
class DriftStats:
    cuts: int
    sessions: int
    candidate_agreement: float
    noise_agreement: float | None
    noise_band: Interval | None
    transitions: list[Transition]
    errors: int


@dataclass(frozen=True)
class _Row:
    session: str
    recorded: ActionSignature
    candidate: ActionSignature
    noise: ActionSignature | None


def _percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[idx]


def _bootstrap(rows: list[_Row], stat: Callable[[list[_Row]], float], rng: random.Random, n_boot: int) -> Interval:
    if not rows:
        return Interval(0.0, 0.0)
    samples = [stat([rows[rng.randrange(len(rows))] for _ in rows]) for _ in range(n_boot)]
    return Interval(_percentile(samples, 0.025), _percentile(samples, 0.975))


def _rows(cuts: list[CutPoint], replays: list[ReplayResult]) -> tuple[list[_Row], int, bool]:
    by_cut: dict[str, dict[str, ReplayResult]] = {}
    for r in replays:
        by_cut.setdefault(r.cut_id, {})[r.role] = r
    noise_on = any(r.role == "noise" for r in replays)
    rows: list[_Row] = []
    errors = 0
    for c in cuts:
        got = by_cut.get(c.cut_id, {})
        cand = got.get("candidate")
        noi = got.get("noise")
        if cand is None or cand.signature is None or (noise_on and (noi is None or noi.signature is None)):
            if (cand is not None and cand.error) or (noi is not None and noi.error):
                errors += 1
            continue
        rows.append(_Row(c.session_id, signature_of_recorded(c.recorded, c.cwd), cand.signature, noi.signature if noi else None))
    return rows, errors, noise_on


def _agreement(rows: list[_Row], which: str) -> float:
    if not rows:
        return 0.0
    hits = sum(1 for r in rows if agree(r.recorded, r.candidate if which == "candidate" else r.noise or r.recorded))
    return hits / len(rows)


def _transition_delta(rows: list[_Row], src: str, dst: str) -> float:
    c = sum(1 for r in rows if r.recorded.key == src and r.candidate.key == dst)
    n = sum(1 for r in rows if r.noise is not None and r.recorded.key == src and r.noise.key == dst)
    return float(c - n)


def compute(cuts: list[CutPoint], replays: list[ReplayResult], seed: int = 0, n_boot: int = 1000) -> DriftStats:
    rows, errors, noise_on = _rows(cuts, replays)
    rng = random.Random(seed)
    cand_agree = _agreement(rows, "candidate")
    noise_agree = _agreement(rows, "noise") if noise_on else None
    band = _bootstrap(rows, lambda rs: _agreement(rs, "noise"), rng, n_boot) if noise_on else None

    cand_counts = Counter((r.recorded.key, r.candidate.key) for r in rows if r.recorded.key != r.candidate.key)
    noise_counts = Counter((r.recorded.key, r.noise.key) for r in rows if r.noise is not None and r.recorded.key != r.noise.key)
    transitions: list[Transition] = []
    for (src, dst), cc in cand_counts.items():
        nc = noise_counts.get((src, dst), 0)
        delta = cc - nc
        if noise_on:
            interval = _bootstrap(rows, lambda rs, s=src, d=dst: _transition_delta(rs, s, d), rng, n_boot)
            real = interval.low > 0 or interval.high < 0
        else:
            interval, real = Interval(delta, delta), False
        src_target = src.split("/")[-1] if "/" in src else src
        dst_target = dst.split("/")[-1] if "/" in dst else dst
        flagged = src_target in FLAG_TARGETS and dst_target not in FLAG_TARGETS
        transitions.append(Transition(src, dst, cc, nc, delta, interval, real, flagged))
    transitions.sort(key=lambda t: (-t.candidate_count, t.src, t.dst))
    return DriftStats(
        cuts=len(rows), sessions=len({r.session for r in rows}), candidate_agreement=cand_agree,
        noise_agreement=noise_agree, noise_band=band, transitions=transitions, errors=errors,
    )
```

- [ ] **Step 4: Run tests, lint, types**

Run: `uv run pytest tests/test_stats.py -q && uv run ruff check . && uv run mypy`
Expected: `7 passed`, clean.

- [ ] **Step 5: Commit**

```bash
git add src/claude_drift/stats.py tests/test_stats.py
git commit -m "Compute agreement, bootstrap noise band, and real-vs-noise transitions"
```

---

### Task 12: Report rendering and `drift report` command

**Files:**
- Create: `src/claude_drift/report.py`
- Modify: `src/claude_drift/cli.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `DriftStats`, `compute` (Task 11), `Run`, `latest_run`, `get_run` (Task 9)
- Produces:
  ```python
  def render(stats: DriftStats, manifest: dict, fmt: str = "text") -> str   # fmt: "text" | "md"
  def build_report(run: Run, fmt: str = "text") -> str                      # compute + render, also writes run.report_path (md)
  ```
  CLI: `report [--run ID] [--format text|md]`

Rendering rules (text):
```
model-drift report  {from} -> {to}

sessions replayed: {sessions}   turns sampled: {cuts}   errors: {errors}
next-action agreement: {cand%}  (noise band {low%}-{high%})        # or "(noise band not measured)"

REAL changes (outside noise band)
  1. {src} -> {dst}    +{candidate_count} turns  [!!]
  ...
  (none)

NOISE (within band, ignore)
  - {src} -> {dst}    +{candidate_count} turns
  ...
```
Markdown differs only in headings (`# model-drift report`, `## REAL changes`, `## NOISE`) and a table instead of the numbered list: columns `#`, `transition`, `candidate turns`, `noise turns`, `delta 95% CI`, `flag`. REAL shows at most 10 rows; NOISE at most 10 rows.

- [ ] **Step 1: Write the failing tests**

`tests/test_report.py`:
```python
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
        cuts=60, sessions=30, candidate_agreement=0.81, noise_agreement=0.83, noise_band=Interval(0.76, 0.88),
        transitions=[
            Transition("Bash/local-read", "Read/local-read", 23, 2, 21, Interval(15, 27), True, False),
            Transition("Write/local-write", "Read/local-read", 7, 0, 7, Interval(3, 11), True, True),
            Transition("Bash/local-read", "Grep/local-read", 3, 2, 1, Interval(-2, 4), False, False),
        ],
        errors=1,
    )


def test_render_text() -> None:
    out = render(stats_fixture(), {"from": "opus-5", "to": "fable-5.1"})
    assert "model-drift report  opus-5 -> fable-5.1" in out
    assert "sessions replayed: 30   turns sampled: 60   errors: 1" in out
    assert "next-action agreement: 81%  (noise band 76%-88%)" in out
    assert "REAL changes (outside noise band)" in out
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
    return json.dumps({"type": "assistant", "message": {"model": "x", "content": [{"type": "tool_use", "name": name, "input": inp}], "usage": {}}})


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


def test_construct_validity_report_changes_when_candidate_answer_changes(projects_dir: Path, drift_home: Path) -> None:
    """RGI-ARC lesson: the report must respond to what the model actually answered."""
    run_a = run_replay(from_model="opus-5", to_model="new", turns=10, workers=1, noise=True, project=None, seed=0,
                       timeout=1.0, runner=InjectableRunner(assistant_tool("Bash", {"command": "ls -la"})), echo=lambda s: None)
    run_b = run_replay(from_model="opus-5", to_model="new", turns=10, workers=1, noise=True, project=None, seed=0,
                       timeout=1.0, runner=InjectableRunner(assistant_tool("WebFetch", {"url": "https://x"})), echo=lambda s: None)
    a = build_report(run_a)
    b = build_report(run_b)
    assert a != b
    assert "WebFetch/remote" in b and "WebFetch/remote" not in a
    assert run_b.report_path.exists()


def test_cli_report_latest_and_named(projects_dir: Path, drift_home: Path) -> None:
    run = run_replay(from_model="opus-5", to_model="new", turns=10, workers=1, noise=False, project=None, seed=0,
                     timeout=1.0, runner=InjectableRunner(assistant_tool("Read", {"file_path": "/r/a"})), echo=lambda s: None)
    r1 = CliRunner().invoke(main, ["report"])
    assert r1.exit_code == 0, r1.output
    assert "model-drift report" in r1.output
    r2 = CliRunner().invoke(main, ["report", "--run", run.path.name, "--format", "md"])
    assert r2.exit_code == 0 and r2.output.startswith("# model-drift report")
    r3 = CliRunner().invoke(main, ["report", "--run", "nope"])
    assert r3.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_report.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'claude_drift.report'`.

- [ ] **Step 3: Implement report.py**

```python
from __future__ import annotations

from typing import Any

from claude_drift.stats import DriftStats, Interval, Transition, compute
from claude_drift.store import Run

MAX_ROWS = 10


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _band(band: Interval | None) -> str:
    if band is None:
        return "(noise band not measured)"
    return f"(noise band {_pct(band.low)}-{_pct(band.high)})"


def _ci(t: Transition) -> str:
    return f"{t.delta} [{int(t.interval.low)}, {int(t.interval.high)}]"


def _header_lines(stats: DriftStats, manifest: dict[str, Any]) -> list[str]:
    return [
        f"sessions replayed: {stats.sessions}   turns sampled: {stats.cuts}   errors: {stats.errors}",
        f"next-action agreement: {_pct(stats.candidate_agreement)}  {_band(stats.noise_band)}",
    ]


def render(stats: DriftStats, manifest: dict[str, Any], fmt: str = "text") -> str:
    real = [t for t in stats.transitions if t.real][:MAX_ROWS]
    noise = [t for t in stats.transitions if not t.real][:MAX_ROWS]
    title = f"model-drift report  {manifest.get('from', '?')} -> {manifest.get('to', '?')}"
    if fmt == "md":
        lines = [f"# {title}", ""] + _header_lines(stats, manifest) + ["", "## REAL changes (outside noise band)", ""]
        lines += _md_table(real) if real else ["(none)"]
        lines += ["", "## NOISE (within band, ignore)", ""]
        lines += _md_table(noise) if noise else ["(none)"]
        return "\n".join(lines) + "\n"
    lines = [title, ""] + _header_lines(stats, manifest) + ["", "REAL changes (outside noise band)"]
    lines += [f"  {i}. {t.src} -> {t.dst}    +{t.candidate_count} turns{'  !!' if t.flagged else ''}" for i, t in enumerate(real, 1)] or ["  (none)"]
    lines += ["", "NOISE (within band, ignore)"]
    lines += [f"  - {t.src} -> {t.dst}    +{t.candidate_count} turns" for t in noise] or ["  (none)"]
    return "\n".join(lines) + "\n"


def _md_table(rows: list[Transition]) -> list[str]:
    out = ["| # | transition | candidate turns | noise turns | delta 95% CI | flag |", "|---|---|---|---|---|---|"]
    for i, t in enumerate(rows, 1):
        out.append(f"| {i} | {t.src} -> {t.dst} | {t.candidate_count} | {t.noise_count} | {_ci(t)} | {'!!' if t.flagged else ''} |")
    return out


def build_report(run: Run, fmt: str = "text") -> str:
    manifest = run.read_manifest()
    stats = compute(run.read_cuts(), run.read_replays(), seed=int(manifest.get("seed", 0)))
    run.report_path.write_text(render(stats, manifest, fmt="md"), encoding="utf-8")
    return render(stats, manifest, fmt=fmt)
```

- [ ] **Step 4: Add the `report` command to cli.py**

Add imports: `from claude_drift.report import build_report` and `from claude_drift.store import get_run, latest_run`.

Append:
```python
@main.command()
@click.option("--run", "run_id", default=None, help="Run id (folder name under ~/.claude-drift/runs). Default: latest.")
@click.option("--format", "fmt", type=click.Choice(["text", "md"]), default="text", show_default=True)
def report(run_id: str | None, fmt: str) -> None:
    """Render the drift report from a stored run. Makes no model calls."""
    try:
        run = get_run(run_id) if run_id else latest_run()
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    if run is None:
        raise click.ClickException("no runs yet; run `drift replay` first")
    click.echo(build_report(run, fmt=fmt), nl=False)
```

Also make `replay` print the report at the end: after `click.echo(f"run id: ...")` add `click.echo(); click.echo(build_report(run), nl=False)`.

- [ ] **Step 5: Run the whole suite, lint, types**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all tests pass (≈50), clean.

- [ ] **Step 6: Commit**

```bash
git add src/claude_drift/report.py src/claude_drift/cli.py tests/test_report.py
git commit -m "Render drift reports and add drift report command"
```

---

### Task 13: First real report, README, CI, packaging

**Files:**
- Modify: `README.md`
- Create: `.github/workflows/ci.yml`, `LICENSE`
- Create: `docs/examples/first-report.md` (the real report, lightly anonymized)

- [ ] **Step 1: Run a small real replay on this machine**

Run: `uv run drift scan` then pick the most common recorded model (call it `OLD`) and run:
```bash
uv run drift replay --from OLD --to sonnet --turns 12 --workers 2 --yes
```
Expected: 24 replays, status `done`, a report printed. Spend check: about 2.4M input tokens, most of it cache. If error rate aborts the run, read `~/.claude-drift/runs/<id>/replays.jsonl` `error` fields and fix the cause before continuing (typical: login state, `--settings` schema, timeout).

- [ ] **Step 2: Save the report as an example**

Run: `mkdir -p docs/examples && uv run drift report --format md > docs/examples/first-report.md`
Open it and remove any path segment that reveals private project names (replace with `<project>`).

- [ ] **Step 3: Write README.md**

```markdown
# claude-drift

Replay your own Claude Code sessions against a new model and see what actually changed.

When a new Claude model ships, its release notes say things like "behaves differently in ways
you may notice without changing any code". `drift` measures that on *your* work: it takes the
sessions already sitting in `~/.claude/projects`, replays each sampled turn with the new model,
and reports which next-action changes are real and which are noise.

- No config. No API key. Uses your existing `claude` login.
- Teacher-forced single-step replay: the new model sees exactly the context the old one saw and
  proposes one next action. No tools run.
- Noise-aware: the old model is replayed too, so you see a noise band, not just a number.

## Install

```bash
uvx claude-drift scan
```

or `pip install claude-drift`.

## Use

```bash
drift scan                                   # what is replayable on this machine (no model calls)
drift replay --from opus-5 --to sonnet-5     # replay 60 sampled turns with both models
drift report                                  # re-render the last run (no model calls)
```

Example output: see [docs/examples/first-report.md](docs/examples/first-report.md).

## How it works

1. **ingest** — finds human prompts in your session logs that were followed by a tool call.
2. **sample** — picks up to 60 turns, stratified by tool, at most 3 per session, seeded.
3. **replay** — writes a truncated copy of the session under a temporary id and runs
   `claude -p --resume <id> --fork-session --model <new> --max-turns 1`. Every tool is blocked by a
   PreToolUse hook and the process is stopped at the first `tool_use`. The temporary file is deleted.
4. **classify** — normalises each action to `tool/target` (e.g. `Bash/local-read`, `Read/local-read`,
   `Agent/delegate`, `Bash/remote`).
5. **stats** — agreement with the recorded action for the new model and for the old model; a
   bootstrap 95% band of old-vs-old agreement is the noise floor. Transitions whose new-minus-old
   count has a bootstrap interval excluding zero are reported as REAL.
6. **report** — text or markdown.

## Cost

Each replayed turn sends the session prefix again (typically 30k–100k tokens, mostly cached).
Default settings replay 60 turns twice. `drift replay` prints an estimate and asks before starting.

## Limits

- Only turns after a human-typed prompt are replayed; turns after tool results are skipped in v1.
- Only Claude models, because only Claude Code sessions are read.
- Results describe *next-action* drift, not end-to-end task outcomes.

## License

MIT
```

- [ ] **Step 4: Add LICENSE (MIT, holder "Youngmin Ko") and CI**

`.github/workflows/ci.yml`:
```yaml
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv python install ${{ matrix.python }}
      - run: uv sync --python ${{ matrix.python }}
      - run: uv run ruff check .
      - run: uv run mypy
      - run: uv run pytest -q
```

- [ ] **Step 5: Verify packaging works end to end**

Run:
```bash
uv build
uvx --from dist/claude_drift-0.1.0-py3-none-any.whl drift --version
uvx --from dist/claude_drift-0.1.0-py3-none-any.whl drift scan | head -3
```
Expected: `drift, version 0.1.0` and a scan listing.

- [ ] **Step 6: Commit and push**

```bash
git add README.md LICENSE .github docs/examples
git commit -m "Add README, MIT license, CI, and first real drift report"
gh repo create mandu5/claude-drift --public --source=. --push
```
Expected: repository visible at github.com/mandu5/claude-drift with CI green.

---

## Self-review notes

- Spec §2 goals: no config (Task 2/3), subscription only (Task 6/7 argv, no SDK), no tool execution (Task 6 hook + Task 7 kill-on-first-tool-use + Task 8 live check), noise band (Task 11), report from stored files (Task 9/12). Non-goals untouched.
- Spec §4 CLI: `scan` Task 3, `replay` Task 10, `report` Task 12; defaults 60/4 in Task 10.
- Spec §5.1–5.6 map to Tasks 2, 4, 6–7, 5, 11, 12. §5.2 same-session same-worker ascending: `batch_by_session` + `pool.map` over batches (Task 10).
- Spec §5.3 error handling: failures recorded not raised (Task 7), 20% abort (Task 10), 180s timeout (Task 7 default, Task 10 flag), cost estimate (Task 10).
- Spec §6 storage: Task 9. Original sessions never written (Task 6 writes a sibling file only; tests assert original unchanged).
- Spec §7 testing: fixtures (Task 2), injected runner (Task 7), construct validity (Task 12), live opt-in (Task 8), temp cleanup on exception (Task 6).
- Spec §8 stack: Task 0. Spec §9 week 1–5 deliverables: Tasks 0–13. Week 6 (launch day) is not a code task.
- Placeholders: none. Type names consistent: `CutPoint`, `RecordedAction`, `ActionSignature`, `ReplayResult`, `Runner`, `Run`, `DriftStats`, `Transition`, `Interval` are defined once and reused by those names.
