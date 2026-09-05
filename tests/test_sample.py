from __future__ import annotations

from collections import Counter

from claude_drift.models import CutPoint, RecordedAction
from claude_drift.sample import batch_by_session, sample_cuts


def cut(session: str, idx: int, tool: str) -> CutPoint:
    return CutPoint(
        cut_id=f"{session}:{idx}",
        session_path=f"/p/{session}.jsonl",
        session_id=session,
        line_index=idx,
        prompt="p",
        recorded=RecordedAction(tool=tool),
        model="m",
        cwd="/c",
        version="v",
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
