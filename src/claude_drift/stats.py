from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from claude_drift.classify import agree, signature_of_recorded
from claude_drift.models import ActionSignature, CutPoint, ReplayResult

FLAG_TARGETS = {"local-write", "delegate"}
ALPHA = 0.05


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
    skipped: int = 0


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


def _bootstrap(
    rows: list[_Row],
    stat: Callable[[list[_Row]], float],
    rng: random.Random,
    n_boot: int,
    alpha: float = ALPHA,
) -> Interval:
    if not rows:
        return Interval(0.0, 0.0)
    samples = [stat([rows[rng.randrange(len(rows))] for _ in rows]) for _ in range(n_boot)]
    return Interval(_percentile(samples, alpha / 2), _percentile(samples, 1 - alpha / 2))


def _rows(
    cuts: list[CutPoint], replays: list[ReplayResult]
) -> tuple[list[_Row], int, int, bool]:
    by_cut: dict[str, dict[str, ReplayResult]] = {}
    for r in replays:
        by_cut.setdefault(r.cut_id, {})[r.role] = r
    noise_on = any(r.role == "noise" for r in replays)
    rows: list[_Row] = []
    errors = 0
    skipped = 0
    for c in cuts:
        got = by_cut.get(c.cut_id, {})
        cand = got.get("candidate")
        noi = got.get("noise")
        missing_noise = noise_on and (noi is None or noi.signature is None)
        if cand is None or cand.signature is None or missing_noise:
            if (cand is not None and cand.error) or (noi is not None and noi.error):
                errors += 1
            else:
                skipped += 1
            continue
        rows.append(
            _Row(
                c.session_id,
                signature_of_recorded(c.recorded, c.cwd),
                cand.signature,
                noi.signature if noi else None,
            )
        )
    return rows, errors, skipped, noise_on


def _agreement(rows: list[_Row], which: str) -> float:
    if not rows:
        return 0.0
    hits = sum(
        1
        for r in rows
        if agree(r.recorded, r.candidate if which == "candidate" else r.noise or r.recorded)
    )
    return hits / len(rows)


def _transition_delta(rows: list[_Row], src: str, dst: str) -> float:
    c = sum(1 for r in rows if r.recorded.key == src and r.candidate.key == dst)
    n = sum(1 for r in rows if r.noise is not None and r.recorded.key == src and r.noise.key == dst)
    return float(c - n)


def compute(
    cuts: list[CutPoint], replays: list[ReplayResult], seed: int = 0, n_boot: int = 1000
) -> DriftStats:
    rows, errors, skipped, noise_on = _rows(cuts, replays)
    rng = random.Random(seed)
    cand_agree = _agreement(rows, "candidate")
    noise_agree = _agreement(rows, "noise") if noise_on else None
    band = (
        _bootstrap(rows, partial(_agreement, which="noise"), rng, n_boot, alpha=ALPHA)
        if noise_on
        else None
    )

    cand_counts = Counter(
        (r.recorded.key, r.candidate.key) for r in rows if r.recorded.key != r.candidate.key
    )
    noise_counts = Counter(
        (r.recorded.key, r.noise.key)
        for r in rows
        if r.noise is not None and r.recorded.key != r.noise.key
    )
    k = len(cand_counts)
    transition_alpha = ALPHA / k if k >= 1 else ALPHA
    transitions: list[Transition] = []
    for (src, dst), cc in cand_counts.items():
        nc = noise_counts.get((src, dst), 0)
        delta = cc - nc
        if noise_on:
            delta_stat = partial(_transition_delta, src=src, dst=dst)
            interval = _bootstrap(rows, delta_stat, rng, n_boot, alpha=transition_alpha)
            real = interval.low > 0 or interval.high < 0
        else:
            interval, real = Interval(delta, delta), False
        src_target = src.split("/")[-1] if "/" in src else src
        dst_target = dst.split("/")[-1] if "/" in dst else dst
        flagged = src_target in FLAG_TARGETS and dst_target not in FLAG_TARGETS
        transitions.append(Transition(src, dst, cc, nc, delta, interval, real, flagged))
    transitions.sort(key=lambda t: (-t.candidate_count, t.src, t.dst))
    return DriftStats(
        cuts=len(rows),
        sessions=len({r.session for r in rows}),
        candidate_agreement=cand_agree,
        noise_agreement=noise_agree,
        noise_band=band,
        transitions=transitions,
        errors=errors,
        skipped=skipped,
    )
