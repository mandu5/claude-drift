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
                        "command": (
                            "echo 'claude-drift: tool execution blocked during replay' >&2; "
                            "exit 2"
                        ),
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
        "claude",
        "-p",
        cut.prompt,
        "--resume",
        temp_id,
        "--fork-session",
        "--model",
        model,
        "--max-turns",
        "1",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
        "--settings",
        str(settings),
    ]
