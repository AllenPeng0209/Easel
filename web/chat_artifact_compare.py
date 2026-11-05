"""Compare two authorized D08 snapshots without opening the mutable source."""
from __future__ import annotations

import difflib
import hashlib
import re
from collections.abc import Callable
from xml.etree import ElementTree

from PIL import Image

from easel.artifact_blobs import ArtifactBlobStore, SnapshotError
from easel.chat_artifacts import ArtifactError, ChatArtifactStore
from easel.video_workshop import WorkshopError, WorkshopStore

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
_MEDIA_MIME = re.compile(r"(?:image|video)/[a-z0-9.+-]{1,64}")
_WORKSHOP_ID = re.compile(r"[a-f0-9]{32}")


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


def _media_mime(metadata: dict) -> str | None:
    value = metadata.get("mime")
    return value if isinstance(value, str) and _MEDIA_MIME.fullmatch(value) else None


def _verify_media_type(stream, mime: str, size: int) -> None:
    valid = False
    if mime == "image/svg+xml" and size <= _MAX_TEXT_BYTES:
        try:
            source = stream.read(size + 1).decode("utf-8")
            uppercase = source.upper()
            if not _NON_TEXT_CONTROLS.search(source) and "<!DOCTYPE" not in uppercase and "<!ENTITY" not in uppercase:
                valid = ElementTree.fromstring(source).tag in ("svg", "{http://www.w3.org/2000/svg}svg")
        except (ElementTree.ParseError, UnicodeError):
            pass
    elif mime.startswith("image/"):
        try:
            with Image.open(stream) as image:
                detected = Image.MIME.get(image.format)
                valid = detected == mime or {detected, mime} == {"image/x-icon", "image/vnd.microsoft.icon"}
                image.verify()
        except (OSError, ValueError, SyntaxError, OverflowError, Image.DecompressionBombError):
            valid = False
    else:
        header = stream.read(32)
        if mime in ("video/mp4", "video/quicktime", "video/x-m4v"):
            # Check the actual immutable container family without decoding the
            # video or letting a file extension stand in for media evidence.
            box = header[4:8]
            valid = 8 <= int.from_bytes(header[:4], "big") <= size and (
                box == b"ftyp" or mime == "video/quicktime" and box in (b"moov", b"mdat", b"wide", b"free"))
        elif mime in ("video/webm", "video/x-matroska"):
            valid = header.startswith(b"\x1a\x45\xdf\xa3")
        elif mime in ("video/x-msvideo", "video/avi"):
            valid = header.startswith(b"RIFF") and header[8:12] == b"AVI "
        elif mime == "video/mpeg":
            valid = header.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"))
        elif mime == "video/ogg":
            valid = header.startswith(b"OggS")
    if not valid:
        raise ArtifactError("固定文件无法核实为所标注的媒体格式", 415)


def _workshop_generation(workshop: WorkshopStore, projects: list[dict], metadata: dict) -> dict | None:
    matches = []
    for project in projects:
        project_id = project.get("id")
        if not isinstance(project_id, str) or not _WORKSHOP_ID.fullmatch(project_id):
            continue
        artifacts, renders = project.get("deliverables"), project.get("renders")
        if not isinstance(artifacts, list) or not isinstance(renders, list):
            continue
        for artifact in artifacts:
            if (not isinstance(artifact, dict) or artifact.get("path") != metadata["path"]
                    or artifact.get("sha256") != metadata["sha256"]):
                continue
            artifact_id, render_id = artifact.get("id"), artifact.get("render_id")
            revision = artifact.get("revision")
            if (not all(isinstance(value, str) and _WORKSHOP_ID.fullmatch(value) for value in (artifact_id, render_id))
                    or type(revision) is not int or revision < 1):
                continue
            for receipt in renders:
                if (not isinstance(receipt, dict) or receipt.get("id") != render_id
                        or receipt.get("artifact_id") != artifact_id or receipt.get("status") != "succeeded"
                        or type(receipt.get("collected_revision")) is not int or receipt["collected_revision"] != revision
                        or not WorkshopStore._submission_confirmed(receipt)):
                    continue
                task, content_revision = receipt.get("task_id"), receipt.get("content_revision")
                if (not isinstance(task, str) or not _WORKSHOP_ID.fullmatch(task)
                        or type(content_revision) is not int or content_revision < 1):
                    continue
                matches.append({"source": "native_workshop", "workshopProjectId": project_id,
                                "renderId": render_id, "taskId": task, "contentRevision": content_revision})
    return matches[0] if len(matches) == 1 else None


def _compare_media(store: ChatArtifactStore, before: dict, after: dict, left_mime: str, right_mime: str) -> dict:
    family = left_mime.split("/", 1)[0]
    if right_mime.split("/", 1)[0] != family or any(item.get("bundleSha256") is not None for item in (before, after)):
        raise ArtifactError("只能比较同类固定媒体版本", 415)
    blobs = store._blobs()
    for metadata, mime in ((before, left_mime), (after, right_mime)):
        try:
            with blobs.open_blob(metadata["sha256"], metadata["size"]) as stream:
                _verify_media_type(stream, mime, metadata["size"])
        except (SnapshotError, OSError) as error:
            raise ArtifactError("固定媒体版本不可用或已损坏", getattr(error, "status_code", 409)) from None
    workshop = WorkshopStore(store.outputs) if family == "video" else None
    projects = []
    if workshop is not None:
        try:
            projects = workshop.list()
        except (WorkshopError, OSError, KeyError, TypeError):
            pass  # Media bytes remain comparable even if provenance is unavailable.

    def side(metadata, mime):
        artifact_id = metadata["artifactId"]
        return {**{key: metadata[key] for key in ("artifactId", "revision", "sha256", "size")},
                "mime": mime, "url": f"/api/chat/artifacts/{artifact_id}/content",
                "generation": _workshop_generation(workshop, projects, metadata) if workshop is not None else None}

    return {"kind": "media", "path": after["path"], "identical": before["sha256"] == after["sha256"],
            "before": side(before, left_mime), "after": side(after, right_mime)}


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
    left_mime, right_mime = _media_mime(before), _media_mime(after)
    if left_mime is not None and right_mime is not None:
        return _compare_media(store, before, after, left_mime, right_mime)
    for metadata in (before, after):
        _bounded_text_metadata(metadata, relative.suffix.lower())
    blobs = store._blobs()
    left, right = _read_text(blobs, before), _read_text(blobs, after)
    identical = before["sha256"] == after["sha256"]
    fields = ("artifactId", "revision", "sha256")
    return {"path": path, "before": {key: before[key] for key in fields},
            "after": {key: after[key] for key in fields}, "identical": identical,
            "diff": "" if identical else _patch(left, right, path)}
