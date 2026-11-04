"""D07 frozen before/after comparisons from the D06 observation boundary."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath

from easel.chat_artifacts import ArtifactError
from web.chat_artifact_compare import _MAX_TEXT_BYTES, _NON_TEXT_CONTROLS, _patch
from web.output_turn_changes import _eligible_name

_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_DIGEST = re.compile(r"[a-f0-9]{64}")
_NON_TEXT_STATES = ("binary", "oversized", "unavailable")


def valid_comparison_path(value) -> bool:
    if not isinstance(value, str) or not 0 < len(value) <= 1024:
        return False
    path = PurePosixPath(value)
    return (bool(path.parts) and not path.is_absolute() and path.as_posix() == value and all(
        part not in ("", ".", "..") and _eligible_name(part) for part in path.parts))


def _encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _evidence_directory(path: Path) -> int:
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("protected evidence directory unavailable")
    return os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _verified_side(side) -> tuple[dict | None, str]:
    if side is None:
        return None, ""
    if not isinstance(side, dict):
        raise ArtifactError("回合比较记录损坏", 409)
    text, size, digest = side.get("text"), side.get("size"), side.get("sha256")
    if (not isinstance(text, str) or type(size) is not int or not 0 <= size <= _MAX_TEXT_BYTES
            or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
            or _NON_TEXT_CONTROLS.search(text)):
        raise ArtifactError("回合比较记录损坏", 409)
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        raise ArtifactError("回合比较记录损坏", 409) from None
    if len(encoded) != size or hashlib.sha256(encoded).hexdigest() != digest:
        raise ArtifactError("回合比较内容校验失败", 409)
    return {"sha256": digest, "size": size}, text


def freeze_turn_output_comparisons(path: Path, session_id: str, turn_id: str, files: list[dict],
                                   comparisons: dict) -> set[str]:
    """Install one bounded private evidence file before its public announcement."""
    rows = {}
    available = set()
    remaining = _MAX_EVIDENCE_BYTES - 1024 * 1024  # Reserve room for path labels and status-only rows.
    for file in files:
        relative = file.get("path")
        if not valid_comparison_path(relative):
            continue
        status = file.get("diffStatus")
        row = {"diffStatus": status if status in _NON_TEXT_STATES else "unavailable"}
        pair = comparisons.get(relative)
        if status == "text" and isinstance(pair, dict):
            try:
                before, _ = _verified_side(pair.get("before"))
                after, _ = _verified_side(pair.get("after"))
            except ArtifactError:
                pass
            else:
                candidate = {"diffStatus": "text", "before": pair.get("before"), "after": pair.get("after")}
                cost = len(_encode(candidate)) + len(_encode(relative)) + 3
                if (before is not None or after is not None) and cost <= remaining:
                    row = candidate
                    available.add(relative)
                    remaining -= cost
        rows[relative] = row
    if not rows:
        return set()
    payload = _encode({"version": 1, "sessionId": session_id, "turnId": turn_id, "files": rows})
    if len(payload) > _MAX_EVIDENCE_BYTES:
        raise OSError("turn comparison exceeds evidence storage budget")
    directory = _evidence_directory(path)
    temporary = ".turn-changes-" + uuid.uuid4().hex
    staged = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        staged = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.fsync(directory)
    finally:
        try:
            if staged:
                os.unlink(temporary, dir_fd=directory)
        finally:
            os.close(directory)
    return available


def _read_evidence(path: Path) -> dict:
    try:
        directory = _evidence_directory(path)
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > _MAX_EVIDENCE_BYTES:
                raise ArtifactError("回合比较记录不可用", 409)
            encoded = stream.read(_MAX_EVIDENCE_BYTES + 1)
            if len(encoded) != info.st_size:
                raise ArtifactError("回合比较记录已变化", 409)
        data = json.loads(encoded)
    except FileNotFoundError:
        raise ArtifactError("此回合未保留文件比较", 404) from None
    except (OSError, ValueError, RecursionError):
        raise ArtifactError("回合比较记录不可用", 409) from None
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("files"), dict):
        raise ArtifactError("回合比较记录损坏", 409)
    return data


def compare_turn_output_snapshots(evidence: Path, session_id: str, turn_id: str, path: str) -> dict:
    if not valid_comparison_path(path):
        raise ArtifactError("无效的比较文件路径", 422)
    record = _read_evidence(evidence)
    if record.get("sessionId") != session_id or record.get("turnId") != turn_id:
        raise ArtifactError("回合比较记录的范围不一致", 409)
    row = record["files"].get(path)
    if row is None:
        raise ArtifactError("该文件不属于此回合的变更记录", 404)
    status = row.get("diffStatus") if isinstance(row, dict) else None
    result = {"turnId": turn_id, "sessionId": session_id, "path": path,
              "identical": False, "before": None, "after": None}
    if status in _NON_TEXT_STATES:
        return {**result, "diffStatus": status}
    if status != "text" or "before" not in row or "after" not in row:
        raise ArtifactError("回合比较记录损坏", 409)
    before, left = _verified_side(row["before"])
    after, right = _verified_side(row["after"])
    if before is None and after is None:
        raise ArtifactError("回合比较记录损坏", 409)
    identical = bool(before and after and before["sha256"] == after["sha256"])
    result.update(before=before, after=after, identical=identical)
    try:
        patch = "" if identical else _patch(left, right, path)
    except ArtifactError as error:
        if error.status_code != 413:
            raise
        return {**result, "diffStatus": "oversized"}
    return {**result, "diffStatus": "text", "diff": patch}
