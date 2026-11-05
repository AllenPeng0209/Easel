"""D06 bounded before/after output observations for a native chat turn.

Adapted from DeepSeek Harness workspace-changes' net-change model (MIT).
Xiangya observes only its current IP's public output tree. Shared-workspace
observations are not proof that a specific agent was the writer.
"""
from __future__ import annotations

import difflib
import errno
import hashlib
import os
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path

from easel.chat_artifacts import is_private_output_part

_MAX_ENTRIES = 10_000
_MAX_DEPTH = 32
_MAX_HASH_FILE_BYTES = 16 * 1024 * 1024
_MAX_HASH_BYTES = 64 * 1024 * 1024
_MAX_TEXT_BYTES = 256 * 1024
_MAX_TEXT_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_DIFF_LINES = 2_000
_MAX_DIFF_PAIRS = 1_000_000
_MAX_REPORT_DIFF_PAIRS = 4_000_000
_MAX_SUMMARY_FILES = 200
_READ_CHUNK = 1024 * 1024

_OBSERVATIONS: dict[Path, set[OutputTurnRecorder]] = {}
_OBSERVATION_LOCK = threading.Lock()


@dataclass(frozen=True)
class _File:
    size: int
    digest: bytes | None = None
    status: str = "unavailable"
    text: str | None = None


@dataclass
class _Snapshot:
    files: dict[str, _File] = field(default_factory=dict)
    excluded: set[str] = field(default_factory=set)
    warnings: set[str] = field(default_factory=set)
    tree_complete: bool = True


def _eligible_name(name: str) -> bool:
    return (not name.startswith("_") and not is_private_output_part(name)
            and "\\" not in name and ":" not in name
            and not any(ord(character) < 32 or 127 <= ord(character) <= 159 or 0xD800 <= ord(character) <= 0xDFFF
                        for character in name))


def _file_signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _snapshot(outputs: Path) -> _Snapshot:
    snapshot = _Snapshot()
    if (os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd
            or not hasattr(os, "O_NOFOLLOW")):
        return _Snapshot(warnings={"unsupported"}, tree_complete=False)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        root = os.open(outputs, directory_flags)
    except OSError as error:
        if error.errno == errno.ENOENT and not outputs.is_symlink():
            return snapshot
        return _Snapshot(warnings={"unreadable"}, tree_complete=False)

    remaining_entries = _MAX_ENTRIES
    remaining_hash_bytes = _MAX_HASH_BYTES
    remaining_text_bytes = _MAX_TEXT_CAPTURE_BYTES

    def capture(directory: int, name: str, info) -> _File:
        nonlocal remaining_hash_bytes, remaining_text_bytes
        size = info.st_size
        if size > _MAX_HASH_FILE_BYTES or size > remaining_hash_bytes:
            snapshot.warnings.add("scan_limit")
            return _File(size, status="oversized")
        remaining_hash_bytes -= size
        keep_text = size <= _MAX_TEXT_BYTES and size <= remaining_text_bytes
        chunks = bytearray() if keep_text else None
        digest = hashlib.sha256()
        binary = False
        try:
            descriptor = os.open(name, file_flags, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                expected = _file_signature(info)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                        or _file_signature(opened) != expected):
                    raise OSError("output changed before observation")
                remaining = size
                while remaining:
                    chunk = stream.read(min(_READ_CHUNK, remaining))
                    if not chunk:
                        raise OSError("output changed during observation")
                    remaining -= len(chunk)
                    digest.update(chunk)
                    binary = binary or b"\x00" in chunk
                    if chunks is not None:
                        chunks.extend(chunk)
                if _file_signature(os.fstat(stream.fileno())) != expected:
                    raise OSError("output changed during observation")
        except OSError:
            snapshot.warnings.add("unreadable")
            return _File(size)
        if binary:
            return _File(size, digest.digest(), "binary")
        if chunks is None:
            return _File(size, digest.digest(), "oversized")
        try:
            text = chunks.decode("utf-8")
        except UnicodeError:
            return _File(size, digest.digest(), "binary")
        remaining_text_bytes -= size
        return _File(size, digest.digest(), "text", text)

    def incomplete(reason: str):
        snapshot.tree_complete = False
        snapshot.warnings.add(reason)

    def visit(directory: int, prefix: str, depth: int):
        nonlocal remaining_entries
        try:
            started = _file_signature(os.fstat(directory))
            with os.scandir(directory) as entries:
                for entry in entries:
                    if remaining_entries <= 0:
                        incomplete("scan_limit")
                        return
                    remaining_entries -= 1
                    name = entry.name
                    if not _eligible_name(name) or not prefix and name.lower() in {"analytics", "wechat"}:
                        continue
                    relative = f"{prefix}/{name}" if prefix else name
                    if len(relative) > 1024:
                        incomplete("scan_limit")
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            if depth >= _MAX_DEPTH:
                                incomplete("scan_limit")
                                continue
                            child = os.open(name, directory_flags, dir_fd=directory)
                            try:
                                if _file_signature(os.fstat(child))[:2] != _file_signature(info)[:2]:
                                    incomplete("unreadable")
                                    continue
                                visit(child, relative, depth + 1)
                            finally:
                                os.close(child)
                        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                            snapshot.files[relative] = capture(directory, name, info)
                        else:
                            snapshot.excluded.add(relative)
                    except OSError:
                        incomplete("unreadable")
            if _file_signature(os.fstat(directory)) != started:
                incomplete("unreadable")
        except OSError:
            incomplete("unreadable")

    try:
        visit(root, "", 0)
        try:
            if _file_signature(os.stat(outputs, follow_symlinks=False))[:2] != _file_signature(os.fstat(root))[:2]:
                return _Snapshot(warnings={"unreadable"}, tree_complete=False)
        except OSError:
            return _Snapshot(warnings={"unreadable"}, tree_complete=False)
        return snapshot
    finally:
        os.close(root)


def _outside_observed_scope(snapshot: _Snapshot, path: str) -> bool:
    while path:
        if path in snapshot.excluded:
            return True
        path, _, _ = path.rpartition("/")
    return False


def _diff_counts(before: _File | None, after: _File | None, budget: int) -> tuple[dict, int]:
    sides = [side for side in (before, after) if side is not None]
    for status in ("binary", "oversized", "unavailable"):
        if any(side.status == status for side in sides):
            return {"diffStatus": status}, 0
    old = before.text.splitlines(keepends=True) if before else []
    new = after.text.splitlines(keepends=True) if after else []
    pairs = len(old) * len(new)
    if max(len(old), len(new)) > _MAX_DIFF_LINES or pairs > min(_MAX_DIFF_PAIRS, budget):
        return {"diffStatus": "oversized"}, 0
    added = deleted = 0
    for action, old_start, old_end, new_start, new_end in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if action != "equal":
            deleted += old_end - old_start
            added += new_end - new_start
    return {"diffStatus": "text", "added": added, "deleted": deleted}, pairs


class OutputTurnRecorder:
    """Observe public outputs, flagging overlapping native turns in this Host."""

    def __init__(self, outputs: Path):
        self.outputs = Path(outputs).absolute()
        self.before: _Snapshot | None = None
        self.overlap = False
        self.closed = False

    def begin(self) -> None:
        with _OBSERVATION_LOCK:
            if self.closed:
                return
            peers = _OBSERVATIONS.setdefault(self.outputs, set())
            if peers:
                self.overlap = True
                for peer in peers:
                    peer.overlap = True
            peers.add(self)
        self.before = _snapshot(self.outputs)

    def close(self) -> None:
        with _OBSERVATION_LOCK:
            self.closed = True
            peers = _OBSERVATIONS.get(self.outputs)
            if peers is not None:
                peers.discard(self)
                if not peers:
                    _OBSERVATIONS.pop(self.outputs, None)

    def finish(self) -> dict | None:
        summary, _ = self.finish_with_comparisons()
        return summary

    def finish_with_comparisons(self) -> tuple[dict | None, dict]:
        """Return the summary plus private bytes from the same observation cut."""
        before = self.before
        try:
            if before is None:
                return {"files": [], "total": 0, "coverage": "partial", "warnings": ["shared_workspace", "unreadable"]}, {}
            after = _snapshot(self.outputs)
        finally:
            self.close()
        files = []
        comparisons = {}
        total = 0
        diff_budget = _MAX_REPORT_DIFF_PAIRS
        warnings = before.warnings | after.warnings
        if self.overlap:
            warnings.add("concurrent_observation")
        for path in sorted(before.files.keys() | after.files.keys()):
            old, new = before.files.get(path), after.files.get(path)
            if old is None:
                if _outside_observed_scope(before, path):
                    warnings.add("unreadable")
                    continue
                if not before.tree_complete:
                    continue
                kind = "added"
            elif new is None:
                if _outside_observed_scope(after, path):
                    warnings.add("unreadable")
                    continue
                if not after.tree_complete:
                    continue
                kind = "deleted"
            elif old.size != new.size or old.digest is not None and new.digest is not None and old.digest != new.digest:
                kind = "modified"
            else:
                continue
            total += 1
            if len(files) < _MAX_SUMMARY_FILES:
                details, pairs = _diff_counts(old, new, diff_budget)
                diff_budget -= pairs
                files.append({"path": path, "kind": kind, **details})
                if details["diffStatus"] == "text":
                    comparisons[path] = {"before": _comparison_side(old), "after": _comparison_side(new)}
        if total > _MAX_SUMMARY_FILES:
            warnings.add("summary_limit")
        if not files and not warnings:
            return None, {}
        return {"files": files, "total": total,
                "coverage": "partial" if warnings else "complete",
                "warnings": ["shared_workspace", *sorted(warnings)]}, comparisons


def _comparison_side(file: _File | None) -> dict | None:
    if file is None:
        return None
    return {"sha256": file.digest.hex(), "size": file.size, "text": file.text}
