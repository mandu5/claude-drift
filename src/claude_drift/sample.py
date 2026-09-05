from __future__ import annotations

import random
from collections import defaultdict

from claude_drift.models import CutPoint


def _cap_per_session(cuts: list[CutPoint], per_session: int) -> list[CutPoint]:
    seen: dict[str, int] = defaultdict(int)
    kept: list[CutPoint] = []
    for c in cuts:
        if seen[c.session_id] < per_session:
            seen[c.session_id] += 1
            kept.append(c)
    return kept


def _quotas(group_sizes: dict[str, int], turns: int) -> dict[str, int]:
    """Proportional quotas, each group at least 1, summing to min(turns, total)."""
    total = sum(group_sizes.values())
    target = min(turns, total)
    quotas = {
        t: max(1, round(n / total * target))
        if target >= len(group_sizes)
        else 0
        for t, n in group_sizes.items()
    }
    if target < len(group_sizes):
        # fewer turns than tools: take the largest groups only
        for t, _ in sorted(group_sizes.items(), key=lambda kv: -kv[1])[:target]:
            quotas[t] = 1
        return quotas
    # trim or pad until the sum matches, adjusting the largest groups first
    order = sorted(group_sizes, key=lambda t: -group_sizes[t])
    i = 0
    while sum(quotas.values()) > target:
        t = order[i % len(order)]
        if quotas[t] > 1:
            quotas[t] -= 1
        i += 1
    while sum(quotas.values()) < target:
        t = order[i % len(order)]
        if quotas[t] < group_sizes[t]:
            quotas[t] += 1
        i += 1
    return quotas


def sample_cuts(
    cuts: list[CutPoint], turns: int, seed: int = 0, per_session: int = 3
) -> list[CutPoint]:
    rng = random.Random(seed)
    shuffled = list(cuts)
    rng.shuffle(shuffled)
    capped = _cap_per_session(shuffled, per_session)
    groups: dict[str, list[CutPoint]] = defaultdict(list)
    for c in capped:
        groups[c.recorded.tool].append(c)
    quotas = _quotas({t: len(g) for t, g in groups.items()}, turns)
    chosen: list[CutPoint] = []
    for tool, group in groups.items():
        chosen.extend(group[: quotas.get(tool, 0)])
    return sorted(chosen, key=lambda c: (c.session_path, c.line_index))


def batch_by_session(cuts: list[CutPoint]) -> list[list[CutPoint]]:
    by_session: dict[str, list[CutPoint]] = defaultdict(list)
    for c in cuts:
        by_session[c.session_path].append(c)
    return [
        sorted(g, key=lambda c: c.line_index) for _, g in sorted(by_session.items())
    ]
