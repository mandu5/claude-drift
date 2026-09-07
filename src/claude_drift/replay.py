from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from claude_drift.classify import TEXT_ONLY, signature
from claude_drift.ingest import read_lines
from claude_drift.models import CutPoint, ReplayResult

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
    """Path to the deny-all settings file, written atomically and only when it must change.

    Replay workers call this concurrently; a plain write leaves a window in which a
    worker passes `--settings` a truncated file, so write a sibling temp file and
    rename it into place instead.
    """
    home = drift_home()
    home.mkdir(parents=True, exist_ok=True)
    p = home / "replay-settings.json"
    content = json.dumps(BLOCK_ALL_SETTINGS, indent=2)
    try:
        if p.read_text(encoding="utf-8") == content:
            return p
    except OSError:
        pass
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, p)
    return p


def claude_version() -> str:
    """`claude --version` output, or "unknown" if the binary is missing or misbehaves."""
    try:
        done = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=15
        )
        return done.stdout.strip()
    except Exception:
        return "unknown"


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


def build_argv(
    cut: CutPoint, temp_id: str, model: str, settings: Path, effort_match: bool = True
) -> list[str]:
    argv = [
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
    if effort_match and cut.effort is not None:
        argv += ["--effort", cut.effort]
    return argv


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
                usage = {
                    k: int(v) for k, v in msg["usage"].items() if isinstance(v, int | float)
                }
            synthetic = msg.get("model") == "<synthetic>"
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
                if block.get("type") == "tool_use" and not synthetic:
                    return ParsedTurn(
                        tool_use={"name": block.get("name"), "input": block.get("input", {})},
                        usage=usage,
                    )
            if synthetic:
                return ParsedTurn(
                    tool_use=None, usage=usage, error=" ".join(texts) or "synthetic error"
                )
        elif ev.get("type") == "result":
            if ev.get("is_error"):
                return ParsedTurn(
                    tool_use=None, usage=usage, error=" ".join(texts) or str(ev.get("subtype"))
                )
            return ParsedTurn(tool_use=None, usage=usage, error=None)
    return ParsedTurn(tool_use=None, usage=usage, error="stream ended without result")


class Runner(Protocol):
    def stream(
        self, argv: list[str], cwd: str, timeout: float
    ) -> AbstractContextManager[Iterator[str]]: ...


class SubprocessRunner:
    """Runs argv, yields stdout lines, kills the process on context exit or timeout."""

    def __init__(self) -> None:
        self.last_returncode: int | None = None
        self.timed_out = False

    @contextmanager
    def stream(self, argv: list[str], cwd: str, timeout: float) -> Iterator[Iterator[str]]:
        self.timed_out = False
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
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


def replay_cut(
    cut: CutPoint,
    model: str,
    role: str,
    runner: Runner,
    timeout: float = 180.0,
    attempt: int = 0,
    effort_match: bool = True,
) -> ReplayResult:
    start = time.monotonic()
    settings = blocking_settings_path()
    try:
        with temp_session(cut) as temp_id:
            argv = build_argv(cut, temp_id, model, settings, effort_match)
            with runner.stream(argv, cut.cwd, timeout) as lines:
                turn = parse_stream(lines)
    except Exception as exc:  # noqa: BLE001 - any runner failure is recorded, never raised
        return ReplayResult(
            cut_id=cut.cut_id,
            model=model,
            role=role,
            signature=None,
            raw_tool_use=None,
            usage={},
            error=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.monotonic() - start) * 1000),
            attempt=attempt,
        )
    duration = int((time.monotonic() - start) * 1000)
    if turn.error is not None:
        return ReplayResult(
            cut.cut_id, model, role, None, None, turn.usage, turn.error, duration, attempt
        )
    if turn.tool_use is None:
        return ReplayResult(
            cut.cut_id, model, role, TEXT_ONLY, None, turn.usage, None, duration, attempt
        )
    sig = signature(str(turn.tool_use["name"]), dict(turn.tool_use["input"]), cut.cwd)
    return ReplayResult(
        cut.cut_id, model, role, sig, turn.tool_use, turn.usage, None, duration, attempt
    )
