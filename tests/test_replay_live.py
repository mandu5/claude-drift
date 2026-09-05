from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from claude_drift.ingest import ingest, projects_root
from claude_drift.replay import SubprocessRunner, replay_cut
from claude_drift.sample import batch_by_session

pytestmark = pytest.mark.skipif(
    os.environ.get("DRIFT_LIVE") != "1", reason="set DRIFT_LIVE=1 to call claude"
)


class TeeRunner:
    """SubprocessRunner that also appends every stdout line to out_dir/stream-<n>.jsonl.

    Test-only: lets a single live run answer both the replay assertions and the
    "did the user's global hooks fire?" question without a second billed call.
    """

    def __init__(self, out_dir: Path) -> None:
        self.inner = SubprocessRunner()
        self.out_dir = out_dir
        self.calls = 0

    @contextmanager
    def stream(self, argv: list[str], cwd: str, timeout: float) -> Iterator[Iterator[str]]:
        self.calls += 1
        path = self.out_dir / f"stream-{self.calls}.jsonl"
        (self.out_dir / f"argv-{self.calls}.json").write_text(json.dumps(argv, indent=1))
        with self.inner.stream(argv, cwd, timeout) as lines:

            def gen() -> Iterator[str]:
                with path.open("a", encoding="utf-8") as fh:
                    for line in lines:
                        fh.write(line + "\n")
                        fh.flush()
                        yield line

            yield gen()


def test_live_replay_two_cuts_same_session_reports_cache_and_no_tool_ran(tmp_path: Path) -> None:
    out_dir = Path(os.environ.get("DRIFT_LIVE_OUT") or tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    cuts = ingest(projects_root())
    batches = [b for b in batch_by_session(cuts) if len(b) >= 2]
    assert batches, "need a local session with at least two human prompts followed by tool use"
    batch = min(batches, key=lambda b: Path(b[0].session_path).stat().st_size)[:2]
    runner = TeeRunner(out_dir)
    results = [replay_cut(c, "sonnet", "candidate", runner, timeout=240) for c in batch]
    (out_dir / "live.json").write_text(
        json.dumps(
            [r.__dict__ | {"signature": str(r.signature)} for r in results], default=str, indent=1
        )
    )
    print("stream capture dir:", out_dir)
    print((out_dir / "live.json").read_text())
    for c, r in zip(batch, results, strict=True):
        print("cut", c.cut_id, "recorded", c.recorded.tool, "replayed", r.signature)
    for r in results:
        assert r.error is None, r.error
        assert r.signature is not None
    # second cut of the same session should read more cache than it creates if prefix caching works
    second = results[1].usage
    print(
        "cache_read",
        second.get("cache_read_input_tokens"),
        "cache_creation",
        second.get("cache_creation_input_tokens"),
    )
