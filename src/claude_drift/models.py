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
    # Which of the K --self-replays attempts this is. Candidates only ever have one.
    attempt: int = 0


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
        tool=d["tool"],
        target=d["target"],
        path_scope=d.get("path_scope"),
        text_only=bool(d["text_only"]),
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
        attempt=int(d.get("attempt", 0)),
    )
