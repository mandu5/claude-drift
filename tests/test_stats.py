from __future__ import annotations

from claude_drift.classify import TEXT_ONLY
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


def rep(
    i: int,
    role: str,
    sig: ActionSignature | None,
    error: str | None = None,
    raw: dict[str, object] | None = None,
) -> ReplayResult:
    return ReplayResult(
        f"s{i % 5}:{i}", "new" if role == "candidate" else "old", role, sig, raw, {}, error, 1
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
    # The band is clustered by session and this fixture spreads the 10 disagreements
    # evenly over all 5 sessions, so every resample of sessions lands on 0.75 exactly.
    assert s.noise_band is not None and s.noise_band.low <= 0.75 <= s.noise_band.high
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


def test_last_non_error_replay_wins() -> None:
    # `drift resume` appends new attempts without deleting old rows, so a cut can have
    # more than one replay per role. An error row must never displace an earlier
    # success, but a later success must replace an earlier error.
    cuts = [cut(0)]
    error_then_success = [
        rep(0, "candidate", None, error="boom"),
        rep(0, "candidate", BASH),
        rep(0, "noise", BASH),
    ]
    s = compute(cuts, error_then_success)
    assert s.cuts == 1 and s.errors == 0 and s.skipped == 0

    success_then_error = [
        rep(0, "candidate", BASH),
        rep(0, "candidate", None, error="boom"),
        rep(0, "noise", BASH),
    ]
    s = compute(cuts, success_then_error)
    assert s.cuts == 1 and s.errors == 0 and s.skipped == 0


BASH_WRITE = ActionSignature("Bash", "local-write", None, False)


def test_stored_signatures_are_reclassified_from_raw_tool_use() -> None:
    # The stored signature was written by an older classifier that called every `>` a
    # write. The raw tool input is the record of what the model actually proposed, so
    # the current classifier is re-run over it at report time.
    raw = {"name": "Bash", "input": {"command": "cat a 2>/dev/null"}}
    cuts = [cut(i) for i in range(10)]
    replays = [rep(i, "candidate", BASH_WRITE, raw=raw) for i in range(10)]
    replays += [rep(i, "noise", BASH) for i in range(10)]
    s = compute(cuts, replays)
    assert s.candidate_agreement == 1.0
    assert s.transitions == []


def test_stored_signature_is_kept_when_there_is_no_raw_tool_use() -> None:
    # text-only turns and errors carry no raw_tool_use; their stored signature stands
    cuts = [cut(i) for i in range(10)]
    replays = [rep(i, "candidate", TEXT_ONLY) for i in range(10)]
    replays += [rep(i, "noise", BASH) for i in range(10)]
    s = compute(cuts, replays)
    assert s.candidate_agreement == 0.0
    assert s.transitions[0].dst == "text"


def test_bootstrap_resamples_sessions_not_rows() -> None:
    # Two sessions of 10 turns each: session A always agrees with the record, session B
    # never does. Resampling sessions draws AA, AB or BB, so the band must reach both
    # 0.0 and 1.0. Resampling 20 rows independently would never produce either endpoint.
    def two_session_cut(session: str, i: int) -> CutPoint:
        return CutPoint(
            f"{session}:{i}",
            f"/p/{session}.jsonl",
            session,
            i,
            "p",
            RecordedAction("Bash", {"command": "ls"}),
            "old",
            "/c",
            "v",
        )

    def two_session_rep(session: str, i: int, role: str, sig: ActionSignature) -> ReplayResult:
        return ReplayResult(f"{session}:{i}", "old", role, sig, None, {}, None, 1)

    cuts = [two_session_cut("a", i) for i in range(10)]
    cuts += [two_session_cut("b", i) for i in range(10)]
    replays = []
    for i in range(10):
        replays.append(two_session_rep("a", i, "candidate", BASH))
        replays.append(two_session_rep("a", i, "noise", BASH))
        replays.append(two_session_rep("b", i, "candidate", READ))
        replays.append(two_session_rep("b", i, "noise", READ))
    s = compute(cuts, replays, seed=0)
    assert s.sessions == 2 and s.noise_agreement == 0.5
    assert s.noise_band == Interval(0.0, 1.0)


def noise_attempt(i: int, attempt: int, sig: ActionSignature | None, error: str | None = None):  # type: ignore[no-untyped-def]
    return ReplayResult(f"s{i % 5}:{i}", "old", "noise", sig, None, {}, error, 1, attempt)


def test_self_agreement_is_one_when_every_attempt_matches() -> None:
    cuts = [cut(i) for i in range(3)]
    replays = [rep(i, "candidate", BASH) for i in range(3)]
    replays += [noise_attempt(i, a, BASH) for i in range(3) for a in range(3)]
    s = compute(cuts, replays, seed=0)
    assert s.self_replays == 3
    assert s.self_agreement == 1.0
    assert s.self_band == Interval(1.0, 1.0)
    assert s.noise_agreement == 1.0  # attempt 0 vs the record, unchanged semantics


def test_self_agreement_is_zero_when_no_two_attempts_match() -> None:
    third = ActionSignature("Grep", "local-read", None, False)
    cuts = [cut(i) for i in range(3)]
    replays = [rep(i, "candidate", BASH) for i in range(3)]
    for i in range(3):
        replays += [
            noise_attempt(i, 0, BASH),
            noise_attempt(i, 1, READ),
            noise_attempt(i, 2, third),
        ]
    s = compute(cuts, replays, seed=0)
    assert s.self_replays == 3
    assert s.self_agreement == 0.0


def test_cut_with_one_successful_attempt_is_excluded_from_the_self_mean() -> None:
    cuts = [cut(i) for i in range(2)]
    replays = [rep(i, "candidate", BASH) for i in range(2)]
    # cut 0 has two matching attempts; cut 1 has one success and one error
    replays += [noise_attempt(0, 0, BASH), noise_attempt(0, 1, BASH)]
    replays += [noise_attempt(1, 0, BASH), noise_attempt(1, 1, None, error="timeout")]
    s = compute(cuts, replays, seed=0)
    assert s.cuts == 2  # both cuts still have a usable attempt-0 noise row
    assert s.self_agreement == 1.0  # the mean is over cut 0 only


def test_self_agreement_is_none_without_repeated_attempts() -> None:
    cuts = [cut(i) for i in range(5)]
    replays = [rep(i, "candidate", BASH) for i in range(5)]
    replays += [rep(i, "noise", BASH) for i in range(5)]
    s = compute(cuts, replays)
    assert s.self_replays == 1
    assert s.self_agreement is None and s.self_band is None
