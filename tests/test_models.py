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
