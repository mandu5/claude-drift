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


def _recorded_after(
    entries: list[dict[str, Any] | None], start: int
) -> tuple[RecordedAction, str] | None:
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


def cuts_from_session(
    path: Path, cwd_exists: Callable[[str], bool] = os.path.isdir
) -> list[CutPoint]:
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
            if (
                project is None
                or cut.cwd == project
                or cut.cwd.startswith(project.rstrip("/") + "/")
            ):
                cuts.append(cut)
    return cuts
