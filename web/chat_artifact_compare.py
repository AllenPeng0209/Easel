"""Compare two authorized D08 snapshots without opening the mutable source."""
from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Callable

from easel.artifact_blobs import ArtifactBlobStore, SnapshotError
from easel.chat_artifacts import ArtifactError, ChatArtifactStore

_MAX_TEXT_BYTES = 256 * 1024
_MAX_DIFF_BYTES = 256 * 1024
_MAX_LINES = 4000
_MAX_DIFF_CELLS = 1_000_000
_TEXT_MIME = {
    "application/json", "application/javascript", "application/xml",
    "application/yaml", "application/x-yaml", "application/toml", "application/x-subrip",
}
_MARKUP_MIME = {"text/html", "text/xhtml", "application/xhtml+xml"}
_NON_TEXT_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def _version(store: ChatArtifactStore, artifact_id: str) -> dict:
    try:
        metadata = store.get(artifact_id)
    except ArtifactError:
        raise
    except (ValueError, TypeError):
        raise ArtifactError("交付版本记录损坏", 409) from None
    if metadata is None:
        raise ArtifactError("交付物不存在", 404)
    if metadata.get("artifactId") != artifact_id or metadata.get("projectId") != store.project_id:
        raise ArtifactError("交付版本身份不一致", 409)
    if metadata.get("storageMode") != "snapshot":
        raise ArtifactError("只有固定快照支持版本差异比较", 409)
    return metadata


def _bounded_text_metadata(metadata: dict, suffix: str) -> None:
    mime = metadata.get("mime")
    media = mime.split(";", 1)[0].strip().lower() if isinstance(mime, str) else ""
    if (metadata.get("bundleSha256") is not None or suffix in {".htm", ".html", ".xhtml"}
            or media in _MARKUP_MIME or (not media.startswith("text/") and media not in _TEXT_MIME)):
        raise ArtifactError("仅支持比较纯 UTF-8 文本快照", 415)
    if metadata["size"] > _MAX_TEXT_BYTES:
        raise ArtifactError("文本快照过大，无法比较", 413)


def _read_text(blobs: ArtifactBlobStore, metadata: dict) -> str:
    # Use the already bounded digest and size rather than re-reading a catalog
    # row inside open_content. No legacy path can become a read fallback.
    size, expected = metadata["size"], metadata["sha256"]
    try:
        with blobs.open_blob(expected, size) as stream:
            data = stream.read(size + 1)
    except FileNotFoundError:
        raise ArtifactError("固定快照文件不存在", 404) from None
    except (SnapshotError, OSError):
        raise ArtifactError("固定快照不可用或已损坏", 409) from None
    # Confirm the actual bounded bytes used by the diff, even if another local
    # writer changed the file after the blob reader's original verification.
    if len(data) != size or hashlib.sha256(data).hexdigest() != expected:
        raise ArtifactError("固定快照内容已变化", 409)
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise ArtifactError("固定快照不是有效 UTF-8 文本", 415) from None
    if _NON_TEXT_CONTROLS.search(text):
        raise ArtifactError("固定快照包含非文本内容", 415)
    return text


def _lines(value: str) -> list[str]:
    lines = value.split("\n")
    complete = [line + "\n" for line in lines[:-1]]
    return complete + [lines[-1]] if lines[-1] else complete


def _patch(before: str, after: str, path: str) -> str:
    # Normalize line endings for display only. The caller separately retains
    # verified byte identity, so a CRLF-only change still differs at byte level.
    before = before.replace("\r\n", "\n").replace("\r", "\n")
    after = after.replace("\r\n", "\n").replace("\r", "\n")
    if before == after:
        return ""
    left, right = _lines(before), _lines(after)
    if (max(len(left), len(right)) > _MAX_LINES
            or (len(left) + 1) * (len(right) + 1) > _MAX_DIFF_CELLS):
        raise ArtifactError("文本差异计算超过比较范围", 413)
    rows = []
    size = 0
    for line in difflib.unified_diff(left, right, fromfile=f"a/{path}", tofile=f"b/{path}"):
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"
        size += len(line.encode("utf-8"))
        if size > _MAX_DIFF_BYTES:
            raise ArtifactError("文本差异过大，未返回不完整补丁", 413)
        rows.append(line)
    return "".join(rows)


def compare_artifact_snapshots(store: ChatArtifactStore, before_id: str, after_id: str,
                               attachment_scope: Callable[[str], str]) -> dict:
    before, after = _version(store, before_id), _version(store, after_id)
    session_id, path = after.get("sessionId"), after.get("path")
    if (not isinstance(session_id, str) or not session_id or before.get("sessionId") != session_id
            or not isinstance(path, str) or before.get("path") != path or _NON_TEXT_CONTROLS.search(path)):
        raise ArtifactError("只能比较同一会话与文件路径的固定版本", 409)
    try:
        relative = store._relative(path, attachment_scope(session_id), canonical=True)
    except ArtifactError:
        raise ArtifactError("交付版本路径无效", 409) from None
    if relative.as_posix() != path:
        raise ArtifactError("交付版本路径无效", 409)
    for metadata in (before, after):
        _bounded_text_metadata(metadata, relative.suffix.lower())
    blobs = store._blobs()
    left, right = _read_text(blobs, before), _read_text(blobs, after)
    identical = before["sha256"] == after["sha256"]
    fields = ("artifactId", "revision", "sha256")
    return {"path": path, "before": {key: before[key] for key in fields},
            "after": {key: after[key] for key in fields}, "identical": identical,
            "diff": "" if identical else _patch(left, right, path)}
