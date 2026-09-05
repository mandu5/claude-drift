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
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

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
        self.manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False)
        )

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
        return [
            cut_from_dict(json.loads(line))
            for line in self.cuts_path.read_text().splitlines()
            if line.strip()
        ]

    def append_replay(self, r: ReplayResult) -> None:
        line = json.dumps(to_dict(r), ensure_ascii=False) + "\n"
        with self._lock, self.replays_path.open("a", encoding="utf-8") as f:
            f.write(line)

    def read_replays(self) -> list[ReplayResult]:
        if not self.replays_path.exists():
            return []
        return [
            replay_from_dict(json.loads(line))
            for line in self.replays_path.read_text().splitlines()
            if line.strip()
        ]


def new_run(now: datetime | None = None) -> Run:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    root = runs_root()
    path = root / stamp
    if path.exists():
        # Second-resolution stamps can collide when two runs start within the same
        # second; disambiguate instead of silently merging into the same directory.
        n = 2
        while (root / f"{stamp}-{n}").exists():
            n += 1
        path = root / f"{stamp}-{n}"
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
