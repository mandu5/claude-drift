from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from claude_drift.classify import agree, signature, signature_of_recorded
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
    candidate_agreement_tool: float = 0.0
    candidate_agreement_full: float = 0.0
    self_replays: int = 1
    self_agreement: float | None = None
    self_band: Interval | None = None


@dataclass(frozen=True)
class _Row:
    session: str
    recorded: ActionSignature
    candidate: ActionSignature
    noise: ActionSignature | None
    # Pairwise agreement among this cut's old-model attempts; None below two successes.
    self_rate: float | None = None


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
    """Clustered bootstrap: resample whole sessions, not individual turns.

    Turns from one session share a repo, a task and a CLAUDE.md, so they are not
    independent draws. Resampling turns treats every turn as its own evidence and
    reports a band far narrower than the data supports.
    """
    if not rows:
        return Interval(0.0, 0.0)
    grouped: dict[str, list[_Row]] = {}
    for r in rows:
        grouped.setdefault(r.session, []).append(r)
    clusters = list(grouped.values())
    samples = []
    for _ in range(n_boot):
        drawn: list[_Row] = []
        for _ in clusters:
            drawn.extend(clusters[rng.randrange(len(clusters))])
        samples.append(stat(drawn))
    return Interval(_percentile(samples, alpha / 2), _percentile(samples, 1 - alpha / 2))


def _classify(r: ReplayResult, cwd: str) -> ActionSignature | None:
    """Re-run the current classifier over the raw tool input the replay recorded.

    `ReplayResult.signature` was computed when the replay ran, so a classifier fix
    would never reach a stored run. The raw tool use is the durable record of what
    the model proposed; text-only turns and errors have none, and keep what was stored.
    """
    raw = r.raw_tool_use
    if raw is None:
        return r.signature
    name = raw.get("name")
    if not isinstance(name, str):
        return r.signature
    tool_input = raw.get("input")
    return signature(name, dict(tool_input) if isinstance(tool_input, dict) else {}, cwd)


def _pairwise_agreement(sigs: list[ActionSignature]) -> float | None:
    """Rate at which this cut's old-model attempts agree with each other.

    None below two successful attempts, because a single attempt says nothing about
    whether the model reproduces itself.
    """
    if len(sigs) < 2:
        return None
    pairs = [(a, b) for i, a in enumerate(sigs) for b in sigs[i + 1 :]]
    return sum(1 for a, b in pairs if agree(a, b, "target")) / len(pairs)


def _rows(
    cuts: list[CutPoint], replays: list[ReplayResult]
) -> tuple[list[_Row], int, int, bool, int]:
    # Last non-error result per (cut_id, role, attempt) wins: `drift resume` appends new
    # tries without deleting old error rows, so a later success must replace an earlier
    # error, but a later error (e.g. from re-running an already-successful slot) must
    # never replace an earlier success.
    by_cut: dict[str, dict[tuple[str, int], ReplayResult]] = {}
    for r in replays:
        slot = by_cut.setdefault(r.cut_id, {})
        key = (r.role, r.attempt)
        existing = slot.get(key)
        if existing is not None and existing.error is None and r.error is not None:
            continue
        slot[key] = r
    noise_attempts = [r.attempt for r in replays if r.role == "noise"]
    noise_on = bool(noise_attempts)
    self_replays = max(noise_attempts) + 1 if noise_attempts else 1
    rows: list[_Row] = []
    errors = 0
    skipped = 0
    for c in cuts:
        got = by_cut.get(c.cut_id, {})
        cand = got.get(("candidate", 0))
        noi = got.get(("noise", 0))
        missing_noise = noise_on and (noi is None or noi.signature is None)
        if cand is None or cand.signature is None or missing_noise:
            if (cand is not None and cand.error) or (noi is not None and noi.error):
                errors += 1
            else:
                skipped += 1
            continue
        attempts = []
        for a in range(self_replays):
            got_a = got.get(("noise", a))
            if got_a is None or got_a.error is not None:
                continue
            sig_a = _classify(got_a, c.cwd)
            if sig_a is not None:
                attempts.append(sig_a)
        rows.append(
            _Row(
                c.session_id,
                signature_of_recorded(c.recorded, c.cwd),
                _classify(cand, c.cwd) or cand.signature,
                _classify(noi, c.cwd) if noi else None,
                _pairwise_agreement(attempts),
            )
        )
    return rows, errors, skipped, noise_on, self_replays


def _agreement(rows: list[_Row], which: str, level: str = "target") -> float:
    if not rows:
        return 0.0
    hits = sum(
        1
        for r in rows
        if agree(r.recorded, r.candidate if which == "candidate" else r.noise or r.recorded, level)
    )
    return hits / len(rows)


def _self_agreement(rows: list[_Row]) -> float:
    rates = [r.self_rate for r in rows if r.self_rate is not None]
    return sum(rates) / len(rates) if rates else 0.0


def _transition_delta(rows: list[_Row], src: str, dst: str) -> float:
    c = sum(1 for r in rows if r.recorded.key == src and r.candidate.key == dst)
    n = sum(1 for r in rows if r.noise is not None and r.recorded.key == src and r.noise.key == dst)
    return float(c - n)


def compute(
    cuts: list[CutPoint], replays: list[ReplayResult], seed: int = 0, n_boot: int = 1000
) -> DriftStats:
    rows, errors, skipped, noise_on, self_replays = _rows(cuts, replays)
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

    measured_self = self_replays > 1 and any(r.self_rate is not None for r in rows)
    self_agreement = _self_agreement(rows) if measured_self else None
    self_band = _bootstrap(rows, _self_agreement, rng, n_boot) if measured_self else None
    return DriftStats(
        cuts=len(rows),
        sessions=len({r.session for r in rows}),
        candidate_agreement=cand_agree,
        noise_agreement=noise_agree,
        noise_band=band,
        transitions=transitions,
        errors=errors,
        skipped=skipped,
        candidate_agreement_tool=_agreement(rows, "candidate", "tool"),
        candidate_agreement_full=_agreement(rows, "candidate", "full"),
        self_replays=self_replays,
        self_agreement=self_agreement,
        self_band=self_band,
    )
