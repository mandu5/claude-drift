from claude_drift.models import (
    ActionSignature,
    CutPoint,
    RecordedAction,
    ReplayResult,
    cut_from_dict,
    replay_from_dict,
    to_dict,
)


def make_cut() -> CutPoint:
    return CutPoint(
        cut_id="s1:14",
        session_path="/tmp/p/s1.jsonl",
        session_id="s1",
        line_index=14,
        prompt="fix the test",
        recorded=RecordedAction(tool="Bash", input={"command": "pytest -q"}),
        model="claude-opus-5",
        cwd="/tmp/repo",
        version="2.1.261",
    )


def test_cut_roundtrip() -> None:
    cut = make_cut()
    assert cut_from_dict(to_dict(cut)) == cut


def test_cut_roundtrip_with_effort() -> None:
    from dataclasses import replace

    cut = replace(make_cut(), effort="high")
    assert cut_from_dict(to_dict(cut)) == cut


def test_cut_from_dict_defaults_effort_to_none_for_old_cuts_jsonl() -> None:
    d = to_dict(make_cut())
    del d["effort"]
    assert cut_from_dict(d).effort is None


def test_replay_roundtrip_with_signature() -> None:
    r = ReplayResult(
        cut_id="s1:14",
        model="claude-sonnet-5",
        role="candidate",
        signature=ActionSignature(
            tool="Bash", target="local-read", path_scope=None, text_only=False
        ),
        raw_tool_use={"name": "Bash", "input": {"command": "ls"}},
        usage={"input_tokens": 2, "output_tokens": 10},
        error=None,
        duration_ms=1234,
    )
    assert replay_from_dict(to_dict(r)) == r


def test_replay_roundtrip_with_error_and_no_signature() -> None:
    r = ReplayResult(
        cut_id="s1:14", model="claude-sonnet-5", role="noise", signature=None,
        raw_tool_use=None, usage={}, error="timeout", duration_ms=180000,
    )
    assert replay_from_dict(to_dict(r)) == r


def test_signature_key() -> None:
    sig = ActionSignature(tool="Read", target="local-read", path_scope="src", text_only=False)
    assert sig.key == "Read/local-read"
    assert ActionSignature(tool="", target="text", path_scope=None, text_only=True).key == "text"


def test_replay_roundtrip_keeps_the_attempt_index() -> None:
    r = ReplayResult(
        cut_id="s1:14", model="old", role="noise", signature=None,
        raw_tool_use=None, usage={}, error=None, duration_ms=1, attempt=2,
    )
    assert replay_from_dict(to_dict(r)) == r


def test_attempt_defaults_to_zero_for_rows_written_before_self_replays() -> None:
    d = {
        "cut_id": "s1:14", "model": "old", "role": "noise", "signature": None,
        "raw_tool_use": None, "usage": {}, "error": None, "duration_ms": 1,
    }
    assert replay_from_dict(d).attempt == 0
