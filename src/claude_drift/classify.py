from __future__ import annotations

import os
import re
import shlex
from typing import Any

from claude_drift.models import ActionSignature, RecordedAction

TEXT_ONLY = ActionSignature(tool="", target="text", path_scope=None, text_only=True)

REMOTE_CMDS = {
    "curl", "wget", "ssh", "scp", "rsync", "pip", "pip3", "uv", "npm", "npx", "pnpm",
    "yarn", "brew", "docker", "gcloud", "aws", "az",
}
REMOTE_GIT = {"push", "pull", "fetch", "clone"}
REMOTE_GH = {"api", "pr", "issue", "repo", "release", "run"}
WRITE_CMDS = {
    "rm", "mv", "cp", "mkdir", "touch", "chmod", "chown", "ln", "tee", "truncate", "dd",
}
WRITE_GIT = {
    "commit", "add", "rm", "mv", "checkout", "switch", "reset", "rebase", "merge", "stash", "tag",
}
READ_CMDS = {
    "ls", "cat", "head", "tail", "less", "more", "grep", "rg", "ag", "find", "fd", "wc",
    "stat", "file", "du", "df", "tree", "pwd", "which", "echo", "printf", "env", "printenv",
    "diff", "sort", "uniq", "cut", "awk", "sed", "jq", "yq", "pytest", "ruff", "mypy", "tsc",
    "eslint", "cargo", "go",
}
READ_GIT = {
    "log", "status", "diff", "show", "branch", "blame", "rev-parse", "rev-list", "remote",
    "ls-files",
}
REDIRECT = re.compile(r"(?<![<>])>{1,2}(?!&)")


def _segments(command: str) -> list[list[str]]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    segs: list[list[str]] = [[]]
    for t in tokens:
        if t in {"|", "||", "&&", ";", "|&"}:
            segs.append([])
        else:
            segs[-1].append(t)
    return [s for s in segs if s]


def _segment_class(seg: list[str]) -> str:
    head = os.path.basename(seg[0])
    args = seg[1:]
    if head == "sudo" and args:
        return _segment_class(args)
    if head in REMOTE_CMDS:
        return "remote"
    if head == "git":
        sub = next((a for a in args if not a.startswith("-")), "")
        if sub in REMOTE_GIT:
            return "remote"
        if sub in WRITE_GIT:
            return "local-write"
        if sub in READ_GIT:
            return "local-read"
        return "other"
    if head == "gh":
        sub = next((a for a in args if not a.startswith("-")), "")
        return "remote" if sub in REMOTE_GH else "other"
    if head == "sed" and any(a == "-i" or a.startswith("-i") for a in args):
        return "local-write"
    if head in WRITE_CMDS:
        return "local-write"
    if head in READ_CMDS:
        return "local-read"
    return "other"


def classify_bash(command: str) -> str:
    classes = {_segment_class(s) for s in _segments(command)}
    if REDIRECT.search(command):
        classes.add("local-write")
    if "remote" in classes:
        return "remote"
    if "local-write" in classes:
        return "local-write"
    if "local-read" in classes and "other" not in classes:
        return "local-read"
    if "local-read" in classes:
        return "local-read"
    return "other"


def _path_scope(path: str | None, cwd: str, is_dir: bool) -> str | None:
    if not path:
        return None
    rel = os.path.relpath(path, cwd) if os.path.isabs(path) else path
    if rel.startswith(".."):
        return os.path.dirname(path) or "/"
    parts = rel.split(os.sep)
    if len(parts) > 1:
        return parts[0]
    return parts[0] if is_dir else "."  # dir keeps its name; a root-level file collapses to "."


def signature(tool: str, tool_input: dict[str, Any], cwd: str) -> ActionSignature:
    if tool == "Bash":
        return ActionSignature(tool, classify_bash(str(tool_input.get("command", ""))), None, False)
    if tool in {"Read", "NotebookRead"}:
        p = tool_input.get("file_path")
        return ActionSignature(tool, "local-read", _path_scope(p, cwd, is_dir=False), False)
    if tool in {"Glob", "Grep"}:
        p = tool_input.get("path")
        return ActionSignature(tool, "local-read", _path_scope(p, cwd, is_dir=True), False)
    if tool in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        p = tool_input.get("file_path") or tool_input.get("notebook_path")
        return ActionSignature(tool, "local-write", _path_scope(p, cwd, is_dir=False), False)
    if tool == "AskUserQuestion":
        return ActionSignature(tool, "ask-user", None, False)
    if tool in {"Agent", "Task"}:
        subagent = str(tool_input.get("subagent_type") or "") or None
        return ActionSignature(tool, "delegate", subagent, False)
    if tool == "Skill":
        return ActionSignature(tool, "skill", str(tool_input.get("skill") or "") or None, False)
    if tool in {"WebFetch", "WebSearch"} or tool.startswith("mcp__"):
        return ActionSignature(tool, "remote", None, False)
    return ActionSignature(tool, "other", None, False)


def signature_of_recorded(rec: RecordedAction, cwd: str) -> ActionSignature:
    return signature(rec.tool, rec.input, cwd)


def agree(a: ActionSignature, b: ActionSignature, level: str = "target") -> bool:
    if level == "tool":
        return a.tool == b.tool and a.text_only == b.text_only
    if level == "target":
        return a.key == b.key
    return a == b
