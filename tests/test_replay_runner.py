from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from claude_drift.ingest import cuts_from_session
from claude_drift.replay import (
    ParsedTurn,
    SubprocessRunner,
    claude_version,
    parse_stream,
    replay_cut,
)


def ev(type_: str, **kw: object) -> str:
    return json.dumps({"type": type_, **kw})


USAGE = {
    "input_tokens": 2,
    "cache_creation_input_tokens": 100,
    "cache_read_input_tokens": 50,
    "output_tokens": 7,
}


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
    lines = [
        assistant([{"type": "text", "text": "All done."}]),
        ev("result", subtype="success", is_error=False),
    ]
    turn = parse_stream(iter(lines))
    assert turn == ParsedTurn(tool_use=None, usage=USAGE, error=None)


def test_parse_stream_auth_error() -> None:
    lines = [
        assistant(
            [{"type": "text", "text": "Not logged in · Please run /login"}], model="<synthetic>"
        ),
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
    leftovers = [
        p
        for p in (projects_dir / "-fake-project").glob("*.jsonl")
        if p.stem not in {"alpha", "beta", "gamma"}
    ]
    assert leftovers == []


def test_replay_cut_text_only_signature(projects_dir: Path, drift_home: Path) -> None:
    cut = cuts_from_session(projects_dir / "-fake-project" / "alpha.jsonl")[0]
    runner = FakeRunner(
        [assistant([{"type": "text", "text": "ok"}]), ev("result", subtype="success")]
    )
    res = replay_cut(cut, "m", "noise", runner)
    assert res.signature is not None
    assert res.signature.text_only and res.signature.key == "text"


def test_replay_cut_runner_exception_becomes_error(projects_dir: Path, drift_home: Path) -> None:
    cut = cuts_from_session(projects_dir / "-fake-project" / "alpha.jsonl")[0]

    class Boom:
        @contextmanager
        def stream(self, argv: list[str], cwd: str, timeout: float) -> Iterator[Iterator[str]]:
            raise TimeoutError("timed out after 180s")
            yield iter([])  # pragma: no cover

    res = replay_cut(cut, "m", "candidate", Boom())
    assert res.signature is None and res.error == "TimeoutError: timed out after 180s"


def test_claude_version_is_unknown_when_the_binary_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", boom)
    assert claude_version() == "unknown"


def test_claude_version_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["claude", "--version"], returncode=0, stdout="2.1.261 (Claude Code)\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake)
    assert claude_version() == "2.1.261 (Claude Code)"


def test_subprocess_runner_streams_and_kills(tmp_path: Path) -> None:
    runner = SubprocessRunner()
    argv = [
        "python3",
        "-c",
        "import sys,time\nprint('a');sys.stdout.flush();print('b');sys.stdout.flush();"
        "time.sleep(30)",
    ]
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
