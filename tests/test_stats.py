from __future__ import annotations

from claude_drift.models import ActionSignature, CutPoint, RecordedAction, ReplayResult
from claude_drift.stats import Interval, compute

BASH = ActionSignature("Bash", "local-read", None, False)
READ = ActionSignature("Read", "local-read", "src", False)
WRITE = ActionSignature("Write", "local-write", ".", False)


def cut(i: int, tool: str = "Bash", cmd: str = "ls") -> CutPoint:
    return CutPoint(
        f"s{i % 5}:{i}",
        f"/p/s{i % 5}.jsonl",
        f"s{i % 5}",
        i,
        "p",
        RecordedAction(tool, {"command": cmd} if tool == "Bash" else {"file_path": "/c/README.md"}),
        "old",
        "/c",
        "v",
    )


def rep(i: int, role: str, sig: ActionSignature | None, error: str | None = None) -> ReplayResult:
    return ReplayResult(
        f"s{i % 5}:{i}", "new" if role == "candidate" else "old", role, sig, None, {}, error, 1
    )


def test_perfect_agreement_no_transitions() -> None:
    cuts = [cut(i) for i in range(20)]
    replays = [rep(i, "candidate", BASH) for i in range(20)]
    replays += [rep(i, "noise", BASH) for i in range(20)]
    s = compute(cuts, replays)
    assert s.cuts == 20 and s.sessions == 5
    assert s.candidate_agreement == 1.0 and s.noise_agreement == 1.0
    assert s.noise_band == Interval(1.0, 1.0)
    assert s.candidate_agreement_tool == 1.0 and s.candidate_agreement_full == 1.0
    assert s.transitions == [] and s.errors == 0


def test_agreement_levels_split_tool_from_target() -> None:
    # recorded Bash/local-read vs candidate Bash/remote: same tool, different target
    bash_remote = ActionSignature("Bash", "remote", None, False)
    cuts = [cut(i) for i in range(10)]
    replays = [rep(i, "candidate", bash_remote) for i in range(10)]
    s = compute(cuts, replays)
    assert s.candidate_agreement_tool == 1.0
    assert s.candidate_agreement == 0.0
    assert s.candidate_agreement_full == 0.0


def test_systematic_shift_is_real_and_noise_shift_is_not() -> None:
    cuts = [cut(i) for i in range(40)]
    # candidate: every cut switches Bash -> Read ; noise: old model agrees with itself always
    replays = [rep(i, "candidate", READ) for i in range(40)]
    replays += [rep(i, "noise", BASH) for i in range(40)]
    s = compute(cuts, replays, seed=0)
    assert s.candidate_agreement == 0.0 and s.noise_agreement == 1.0
    t = s.transitions[0]
    assert (t.src, t.dst) == ("Bash/local-read", "Read/local-read")
    assert t.candidate_count == 40 and t.noise_count == 0 and t.delta == 40
    assert t.real is True and t.flagged is False


def test_equal_noise_and_candidate_shift_is_not_real() -> None:
    cuts = [cut(i) for i in range(40)]
    # both replays disagree with the record on the same 10 cuts -> difference is 0
    replays = [rep(i, "candidate", READ if i < 10 else BASH) for i in range(40)]
    replays += [rep(i, "noise", READ if i < 10 else BASH) for i in range(40)]
    s = compute(cuts, replays, seed=0)
    assert s.candidate_agreement == 0.75 and s.noise_agreement == 0.75
    assert s.noise_band is not None and s.noise_band.low < 0.75 < s.noise_band.high
    t = s.transitions[0]
    assert t.delta == 0 and t.real is False


def test_flagged_when_write_disappears() -> None:
    cuts = [cut(i, tool="Write") for i in range(10)]
    replays = [rep(i, "candidate", READ) for i in range(10)]
    replays += [rep(i, "noise", WRITE) for i in range(10)]
    s = compute(cuts, replays)
    assert s.transitions[0].flagged is True


def test_errors_excluded_and_counted() -> None:
    cuts = [cut(i) for i in range(10)]
    replays = [
        rep(i, "candidate", None, error="timeout") if i < 3 else rep(i, "candidate", BASH)
        for i in range(10)
    ]
    replays += [rep(i, "noise", BASH) for i in range(10)]
    s = compute(cuts, replays)
    assert s.cuts == 7 and s.errors == 3 and s.skipped == 0


def test_no_noise_mode() -> None:
    cuts = [cut(i) for i in range(10)]
    replays = [rep(i, "candidate", READ) for i in range(10)]
    s = compute(cuts, replays)
    assert s.noise_agreement is None and s.noise_band is None
    assert s.transitions[0].real is False and s.transitions[0].interval == Interval(10, 10)


def test_bootstrap_is_deterministic() -> None:
    cuts = [cut(i) for i in range(30)]
    replays = [rep(i, "candidate", READ if i % 3 else BASH) for i in range(30)]
    replays += [rep(i, "noise", READ if i % 7 == 0 else BASH) for i in range(30)]
    assert compute(cuts, replays, seed=3) == compute(cuts, replays, seed=3)


def test_missing_replays_count_as_skipped() -> None:
    cuts = [cut(i) for i in range(10)]
    # only cuts 0-6 have a candidate (and noise) record at all; 7-9 have no replay
    replays = [rep(i, "candidate", BASH) for i in range(7)]
    replays += [rep(i, "noise", BASH) for i in range(7)]
    s = compute(cuts, replays)
    assert s.cuts == 7 and s.errors == 0 and s.skipped == 3


def test_bonferroni_widens_transition_intervals() -> None:
    cuts = [cut(i) for i in range(60)]

    def make_replays(with_extra_transitions: bool) -> list[ReplayResult]:
        replays = []
        for i in range(60):
            if i < 6:
                sig = READ
            elif with_extra_transitions and 20 <= i < 30:
                sig = ActionSignature(f"Tool{i - 20}", "other", None, False)
            else:
                sig = BASH
            replays.append(rep(i, "candidate", sig))
        replays += [rep(i, "noise", BASH) for i in range(60)]
        return replays

    # single tested transition (Bash -> Read): interval uses alpha = 0.05 (k=1)
    single = compute(cuts, make_replays(with_extra_transitions=False), seed=0)
    # 10 more distinct candidate transitions added on cuts 20-29: k=11, alpha = 0.05/11
    multi = compute(cuts, make_replays(with_extra_transitions=True), seed=0)

    t_single = single.transitions[0]
    t_multi = multi.transitions[0]
    assert (t_single.src, t_single.dst) == ("Bash/local-read", "Read/local-read")
    assert (t_multi.src, t_multi.dst) == ("Bash/local-read", "Read/local-read")
    # Bonferroni-corrected alpha (0.05/11) is smaller, so the CI uses more extreme
    # percentiles and must be at least as wide as the uncorrected (k=1, alpha=0.05) one.
    width_single = t_single.interval.high - t_single.interval.low
    width_multi = t_multi.interval.high - t_multi.interval.low
    assert width_multi >= width_single
    # with only one comparison tested, delta=6 over 60 cuts (noise never drifts) is
    # still real at alpha=0.05.
    assert t_single.real is True
