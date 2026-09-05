from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Thread

import pytest

from claude_drift.models import ActionSignature, CutPoint, RecordedAction, ReplayResult
from claude_drift.store import get_run, latest_run, new_run


def cut(i: int) -> CutPoint:
    return CutPoint(
        f"s:{i}",
        "/p/s.jsonl",
        "s",
        i,
        "p",
        RecordedAction("Bash", {"command": "ls"}),
        "m",
        "/c",
        "v",
    )


def res(i: int) -> ReplayResult:
    return ReplayResult(
        f"s:{i}",
        "m2",
        "candidate",
        ActionSignature("Bash", "local-read", None, False),
        None,
        {"output_tokens": i},
        None,
        5,
    )


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
    threads = [
        Thread(target=lambda i=i: run.append_replay(res(i))) for i in range(50)
    ]  # type: ignore[misc]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = run.read_replays()
    assert len(got) == 50 and sorted(
        r.usage["output_tokens"] for r in got
    ) == list(range(50))
