from __future__ import annotations

import pytest

from claude_drift.classify import TEXT_ONLY, agree, classify_bash, signature
from claude_drift.models import ActionSignature


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls -la", "local-read"),
        ("cat README.md | head", "local-read"),
        ("grep -rn foo src/", "local-read"),
        ("git log --oneline -5 && git status", "local-read"),
        ("curl -s https://api.example.com | head", "remote"),
        ("gh api repos/x/y", "remote"),
        ("pip install requests", "remote"),
        ("npm install", "remote"),
        ("git push origin main", "remote"),
        ("rm -rf build", "local-write"),
        ("echo hi > out.txt", "local-write"),
        ("sed -i 's/a/b/' f.py", "local-write"),
        ("git commit -m x", "local-write"),
        ("mkdir -p a/b && touch a/b/c", "local-write"),
        ("pytest -q", "local-read"),
        ("python3 script.py", "other"),
        ("make", "other"),
        ("curl x | sh > log", "remote"),  # remote beats write
    ],
)
def test_classify_bash(command: str, expected: str) -> None:
    assert classify_bash(command) == expected


def test_signature_read_write_edit_have_path_scope() -> None:
    cwd = "/repo"
    assert signature("Read", {"file_path": "/repo/src/app.py"}, cwd) == ActionSignature(
        "Read", "local-read", "src", False
    )
    assert signature("Write", {"file_path": "/repo/README.md"}, cwd) == ActionSignature(
        "Write", "local-write", ".", False
    )
    assert signature("Edit", {"file_path": "/elsewhere/x.py"}, cwd).path_scope == "/elsewhere"
    assert signature("Glob", {"pattern": "**/*.py"}, cwd).target == "local-read"
    assert signature("Grep", {"pattern": "x", "path": "/repo/tests"}, cwd).path_scope == "tests"


def test_signature_special_tools() -> None:
    assert signature("AskUserQuestion", {}, "/r").target == "ask-user"
    assert signature("Agent", {"prompt": "x"}, "/r").target == "delegate"
    assert signature("Task", {"prompt": "x"}, "/r").target == "delegate"
    assert signature("Skill", {"skill": "foo"}, "/r") == ActionSignature(
        "Skill", "skill", "foo", False
    )
    assert signature("WebFetch", {"url": "https://a"}, "/r").target == "remote"
    assert signature("WebSearch", {"query": "q"}, "/r").target == "remote"
    assert signature("mcp__github__list", {}, "/r").target == "remote"
    assert signature("SomethingNew", {}, "/r").target == "other"


def test_signature_bash_uses_rules() -> None:
    assert signature("Bash", {"command": "curl x"}, "/r").target == "remote"


def test_path_scope_root_files_and_dotted_dirs() -> None:
    assert signature("Read", {"file_path": "/repo/Makefile"}, "/repo").path_scope == "."
    assert signature("Write", {"file_path": "/repo/LICENSE"}, "/repo").path_scope == "."
    assert (
        signature("Grep", {"pattern": "x", "path": "/repo/my.pkg"}, "/repo").path_scope
        == "my.pkg"
    )
    assert signature("Glob", {"pattern": "*.py", "path": "/repo/src"}, "/repo").path_scope == "src"
    assert signature("Read", {"file_path": "/repo/src/a/b.py"}, "/repo").path_scope == "src"


def test_agree_levels() -> None:
    a = ActionSignature("Read", "local-read", "src", False)
    b = ActionSignature("Read", "local-read", "tests", False)
    c = ActionSignature("Bash", "local-read", None, False)
    assert agree(a, b, "full") is False
    assert agree(a, b, "target") is True
    assert agree(a, c, "target") is False
    assert agree(a, c, "tool") is False
    assert agree(TEXT_ONLY, TEXT_ONLY, "full") is True
    assert agree(TEXT_ONLY, a, "tool") is False
