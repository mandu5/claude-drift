from __future__ import annotations

from typing import Any

from claude_drift.stats import DriftStats, Interval, Transition, compute
from claude_drift.store import Run

MAX_ROWS = 10


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _band(band: Interval | None) -> str:
    if band is None:
        return "(noise band not measured)"
    return f"(noise band {_pct(band.low)}-{_pct(band.high)})"


def _ci(t: Transition) -> str:
    return f"{t.delta} [{int(t.interval.low)}, {int(t.interval.high)}]"


def _real_heading(k: int) -> str:
    return f"REAL changes (outside noise band, Bonferroni-corrected over {k} transitions)"


def _header_lines(stats: DriftStats) -> list[str]:
    return [
        f"sessions replayed: {stats.sessions}   turns sampled: {stats.cuts}   "
        f"errors: {stats.errors}   skipped: {stats.skipped}",
        f"next-action agreement: {_pct(stats.candidate_agreement)}  {_band(stats.noise_band)}",
    ]


def _text_real_row(i: int, t: Transition) -> str:
    flag = "  !!" if t.flagged else ""
    return f"  {i}. {t.src} -> {t.dst}    +{t.candidate_count} turns{flag}"


def _text_noise_row(t: Transition) -> str:
    return f"  - {t.src} -> {t.dst}    +{t.candidate_count} turns"


def _md_row(i: int, t: Transition) -> str:
    flag = "!!" if t.flagged else ""
    return (
        f"| {i} | {t.src} -> {t.dst} | {t.candidate_count} | {t.noise_count} | "
        f"{_ci(t)} | {flag} |"
    )


def _md_table(rows: list[Transition]) -> list[str]:
    header = "| # | transition | candidate turns | noise turns | delta 95% CI | flag |"
    sep = "|---|---|---|---|---|---|"
    return [header, sep] + [_md_row(i, t) for i, t in enumerate(rows, 1)]


def render(stats: DriftStats, manifest: dict[str, Any], fmt: str = "text") -> str:
    real = [t for t in stats.transitions if t.real][:MAX_ROWS]
    noise = [t for t in stats.transitions if not t.real][:MAX_ROWS]
    k = len(stats.transitions)
    title = f"model-drift report  {manifest.get('from', '?')} -> {manifest.get('to', '?')}"

    if fmt == "md":
        lines = [f"# {title}", ""] + _header_lines(stats)
        lines += ["", f"## {_real_heading(k)}", ""]
        lines += _md_table(real) if real else ["(none)"]
        lines += ["", "## NOISE (within band, ignore)", ""]
        lines += _md_table(noise) if noise else ["(none)"]
        return "\n".join(lines) + "\n"

    lines = [title, ""] + _header_lines(stats)
    lines += ["", _real_heading(k)]
    lines += [_text_real_row(i, t) for i, t in enumerate(real, 1)] or ["  (none)"]
    lines += ["", "NOISE (within band, ignore)"]
    lines += [_text_noise_row(t) for t in noise] or ["  (none)"]
    return "\n".join(lines) + "\n"


def build_report(run: Run, fmt: str = "text") -> str:
    manifest = run.read_manifest()
    stats = compute(run.read_cuts(), run.read_replays(), seed=int(manifest.get("seed", 0)))
    run.report_path.write_text(render(stats, manifest, fmt="md"), encoding="utf-8")
    return render(stats, manifest, fmt=fmt)
