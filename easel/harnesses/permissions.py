"""Conservative automatic approval of ordinary Codex requests."""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from .base import RunRequest


def _inside_workspace(value: str, root: Path) -> bool:
    try:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _ordinary_read_path(value: str, cwd: Path, root: Path) -> bool:
    """A read approval must identify an in-workspace, non-credential target."""
    if not value or value == "-" or any(character in value for character in "*?[]{}"):
        return False
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = cwd / path
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    # Check the original alias too: a sensitive name may be a symlink to an
    # ordinary file, and an ordinary name may resolve into credential storage.
    for candidate in (path, resolved):
        for part in candidate.parts:
            name = part.lower()
            if name in {".ssh", ".aws", ".codex", ".easel", ".openclaw", ".gnupg", ".git",
                        ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".pgpass", "auth.json",
                        "cookies.json", "cookies.txt", "credentials", "credentials.json",
                        "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}:
                return False
            if name.startswith(".env") and name not in {".env.example", ".env.sample", ".env.template"}:
                return False
            if name.endswith((".pem", ".key", ".p12", ".pfx")):
                return False
    return True


def _ordinary_read_arguments(tokens: list[str], cwd: Path, root: Path) -> bool:
    program = tokens[0]
    if program == "pwd":
        return all(token in ("-L", "-P") for token in tokens[1:])
    # Options with other filesystem inputs, preprocessors, symlink-following,
    # ignore bypasses or output targets need explicit confirmation. This is a
    # conservative argv grammar, not an attempt to sandbox arbitrary commands.
    flag_letters = {
        "ls": "aAdFhlprstU1", "cat": "AbEe nstTuv".replace(" ", ""),
        "head": "qv", "tail": "qv", "wc": "clmwL", "stat": "Ltx",
        "grep": "ivnHhclqoswExF", "rg": "nivHhclqoswExFSl",
    }
    switches = {"--help", "--version", "--number", "--line-number", "--count", "--ignore-case",
                "--invert-match", "--fixed-strings", "--word-regexp", "--line-regexp", "--files"}
    patterns = program in ("grep", "rg")
    pattern_seen = "--files" in tokens if program == "rg" else False
    after_separator = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if not after_separator and token == "--":
            after_separator = True
            index += 1
            continue
        if not after_separator and token.startswith("-"):
            if token in ("-e", "--regexp") and patterns:
                if index + 1 >= len(tokens):
                    return False
                pattern_seen = True
                index += 2
                continue
            if token in ("-f", "--file") and patterns:
                if index + 1 >= len(tokens) or not _ordinary_read_path(tokens[index + 1], cwd, root):
                    return False
                pattern_seen = True
                index += 2
                continue
            if token in ("-n", "-c", "--lines", "--bytes") and program in ("head", "tail"):
                if index + 1 >= len(tokens) or not re.fullmatch(r"[+-]?\d+[bBkKmMgG]?", tokens[index + 1]):
                    return False
                index += 2
                continue
            if token in switches:
                index += 1
                continue
            if token.startswith("--") or not token[1:] or any(letter not in flag_letters.get(program, "") for letter in token[1:]):
                return False
            index += 1
            continue
        if patterns and not pattern_seen:
            pattern_seen = True
        elif not _ordinary_read_path(token, cwd, root):
            return False
        index += 1
    return True


def can_auto_approve(method: str, params: dict, request: RunRequest) -> bool:
    if not request.codex_auto_approve:
        return False
    if method == "item/fileChange/requestApproval":
        root = params.get("grantRoot")
        return (request.sandbox == "workspace-write" and isinstance(root, str)
                and bool(root) and _inside_workspace(root, request.cwd))
    if method != "item/commandExecution/requestApproval" or params.get("additionalPermissions"):
        return False
    decisions = params.get("availableDecisions")
    if decisions is not None and "accept" not in decisions:
        return False
    if params.get("kind", "command") != "command":
        return False
    command = params.get("command")
    if not isinstance(command, str) or not command.strip():
        return False
    cwd = params.get("cwd", str(request.cwd))
    if not isinstance(cwd, str) or not _inside_workspace(cwd, request.cwd):
        return False
    current = Path(cwd)
    if not current.is_absolute():
        current = request.cwd / current
    for depth in range(3):
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        if len(tokens) == 3 and tokens[0] in (
                "bash", "sh", "zsh", "/bin/bash", "/bin/sh", "/bin/zsh",
                "/usr/bin/bash", "/usr/bin/sh", "/usr/bin/zsh") and tokens[1] in ("-c", "-lc"):
            command = tokens[2]
            continue
        break
    else:
        return False
    if not tokens or re.search(r"[;&|<>`$\n\r]", command):
        return False
    if tokens[0] in ("pwd", "ls", "cat", "head", "tail", "wc", "stat", "grep"):
        return _ordinary_read_arguments(tokens, current, request.cwd)
    if tokens[0] == "rg":
        return _ordinary_read_arguments(tokens, current, request.cwd)
    if tokens[0] == "git":
        if len(tokens) > 1 and tokens[1] == "fetch":
            return (len(tokens) <= 4 and (len(tokens) < 3 or bool(re.fullmatch(r"[\w-]+", tokens[2])))
                    and all(re.fullmatch(r"[\w/-]+", token) and not token.startswith(("-", "/"))
                            and ".." not in token for token in tokens[2:]))
        if not (len(tokens) > 1 and tokens[1] in ("status", "diff", "log", "show")):
            return False
        if any(
            token in ("--ext-diff", "--textconv", "--output", "--exec", "--upload-pack", "--receive-pack")
            or token.startswith(("--output=", "--exec=", "--upload-pack=", "--receive-pack="))
            or token in ("--no-index", "--follow") for token in tokens[2:]):
            return False
        return all(token.startswith("-") or _ordinary_read_path(token.split(":", 1)[-1], current, request.cwd)
                   for token in tokens[2:])
    return False
