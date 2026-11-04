"""Local immutable Office previews, adapted from Harness office-to-pdf (MIT).

The upstream authorization-before-read, bounded conversion and declared-font
warning contract is applied to Easel's verified snapshot FD. The installed
LibreOffice CLI is the engine; its diagnostics are never exposed to the user.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import posixpath
import re
import shutil
import stat
import struct
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from easel.chat_artifacts import ArtifactError
from easel.publication_process import (
    PublicationProcessCleanupError,
    PublicationProcessStartError,
    run_publication_process as _run_local_process,
)

MAX_PDF_BYTES = 32 * 1024 * 1024
_MAX_SOURCE_BYTES = 24 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 4096
_MAX_ARCHIVE_INDEX_BYTES = 2 * 1024 * 1024
_MAX_XML_BYTES = 2 * 1024 * 1024
_MAX_FONT_XML_BYTES = 8 * 1024 * 1024
_CONVERSION_TIMEOUT = 35
_SLOTS = threading.BoundedSemaphore(2)
_MAIN_PARTS = {
    ".docx": ("word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
    ".pptx": ("ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
}
_CONTENT_TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_RELATIONSHIPS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_HYPERLINKS = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/hyperlink",
}
_ACTIVE_PARTS = {"vbaproject.bin", "embeddings", "activex"}
_ACTIVE_TYPES = ("macroenabled", "vbaproject", "activex", "oleobject")
_ACTIVE_RELATIONS = {"activexcontrol", "control", "oleobject", "package", "vbaproject", "afchunk", "attachedtemplate"}


class OfficePreviewError(Exception):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status


def _invalid(message="该文档无效或含有不受支持的活动内容") -> OfficePreviewError:
    return OfficePreviewError("invalid_document", message, 422)


def _find_soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return str(Path(found).absolute())
    for path in (Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
                 Path(r"C:\Program Files\LibreOffice\program\soffice.exe")):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _find_font_inventory() -> str | None:
    found = shutil.which("fc-list")
    return str(Path(found).absolute()) if found else None


@dataclass
class PreparedOfficePreview:
    scratch: tempfile.TemporaryDirectory
    source: Path
    executable: str
    fonts: set[str] | None

    def close(self) -> None:
        try:
            self.scratch.cleanup()
        finally:
            _SLOTS.release()


def _validate_zip_index(source: Path) -> None:
    with source.open("rb") as stream:
        length = stream.seek(0, 2)
        stream.seek(max(0, length - 65557))
        tail = stream.read(65557)
    end = tail.rfind(b"PK\x05\x06")
    if end < 0 or len(tail) - end < 22:
        raise _invalid()
    disk, first_disk, disk_count, count, index_bytes, offset, comment = struct.unpack_from("<4H2LH", tail, end + 4)
    if len(tail) - end != 22 + comment or disk or first_disk or disk_count != count or offset + index_bytes > length:
        raise _invalid()
    if count > _MAX_ARCHIVE_ENTRIES or index_bytes > _MAX_ARCHIVE_INDEX_BYTES:
        raise OfficePreviewError("input_too_large", "Office 压缩包索引超过预览限制", 413)


def _xml(archive: zipfile.ZipFile, entry: zipfile.ZipInfo):
    if entry.file_size > _MAX_XML_BYTES:
        raise OfficePreviewError("input_too_large", "Office 结构文件超过预览限制", 413)
    with archive.open(entry) as stream:
        raw = stream.read(_MAX_XML_BYTES + 1)
    if len(raw) != entry.file_size:
        raise _invalid()
    try:
        source = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
        upper = source.upper()
        if "\x00" in source or "<!DOCTYPE" in upper or "<!ENTITY" in upper:
            raise _invalid()
        return ElementTree.fromstring(source)
    except (UnicodeError, ElementTree.ParseError):
        raise _invalid() from None


def _check_relationships(root, relationship_path: str, parts: set[str]) -> None:
    if root.tag != _RELATIONSHIPS + "Relationships":
        raise _invalid()
    owner_directory = posixpath.dirname(posixpath.dirname(relationship_path))
    for child in root:
        target = unquote(child.get("Target", ""), errors="strict")
        relation_type = child.get("Type", "")
        if (child.tag != _RELATIONSHIPS + "Relationship" or not target or "\\" in target
                or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in target)):
            raise _invalid()
        if relation_type.rsplit("/", 1)[-1].casefold() in _ACTIVE_RELATIONS:
            raise _invalid()
        parsed = urlsplit(target)
        mode = child.get("TargetMode", "Internal")
        if mode == "External":
            # Hyperlinks are passive PDF annotations, not fetched resources.
            if relation_type not in _HYPERLINKS or parsed.scheme not in ("http", "https", "mailto"):
                raise _invalid("文档包含无法安全加载的外部资源")
        elif mode != "Internal" or parsed.scheme or parsed.netloc or target.startswith("//"):
            raise _invalid("文档包含无法安全加载的外部资源")
        elif parsed.path.startswith("/"):
            if parsed.path.lstrip("/").casefold() not in parts:
                raise _invalid("文档引用了压缩包外部路径")
        else:
            resolved = posixpath.normpath(posixpath.join(owner_directory, parsed.path))
            if resolved == ".." or resolved.startswith("../"):
                raise _invalid("文档引用了压缩包外部路径")


def _check_svg(root) -> None:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].casefold() in ("script", "foreignobject"):
            raise _invalid()
        for key, value in element.attrib.items():
            attribute = key.rsplit("}", 1)[-1].casefold()
            if attribute.startswith("on"):
                raise _invalid()
            if attribute in ("href", "src") and not value.strip().startswith((
                    "#", "data:image/png;base64,", "data:image/jpeg;base64,", "data:image/gif;base64,")):
                raise _invalid("文档包含无法安全加载的外部资源")
        values = [*element.attrib.values(), element.text or ""]
        for value in values:
            if "@import" in value.casefold() or any(
                not target.strip(" \t\r\n'\"").startswith("#")
                for target in re.findall(r"url\s*\(([^)]*)\)", value, flags=re.IGNORECASE)
            ):
                raise _invalid("文档包含无法安全加载的外部资源")


def _font_name(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    if (not value or value.startswith(("+", "-")) or len(value) > 100
            or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)):
        return None
    return value


def _font_declarations(root) -> set[str]:
    result = set()
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "rFonts":
            values = [value for key, value in element.attrib.items()
                      if key.rsplit("}", 1)[-1] in {"ascii", "hAnsi", "eastAsia", "cs"}]
        elif tag in ("latin", "ea", "cs"):
            values = [element.get("typeface")]
        else:
            continue
        result.update(name for value in values if (name := _font_name(value)))
    return result


def _inspect_package(source: Path) -> set[str] | None:
    _validate_zip_index(source)
    main, main_type = _MAIN_PARTS[source.suffix]
    fonts: set[str] | None = set()
    font_budget = _MAX_FONT_XML_BYTES
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_ARCHIVE_ENTRIES or sum(entry.file_size for entry in entries) > _MAX_ARCHIVE_BYTES:
                raise OfficePreviewError("input_too_large", "Office 解压内容超过预览限制", 413)
            seen = set()
            for entry in entries:
                name = entry.filename.rstrip("/")
                parts = name.split("/")
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (not name or entry.orig_filename != entry.filename or name.startswith("/") or "\\" in name
                        or any(part in ("", ".", "..") or ":" in part for part in parts)
                        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in name)
                        or name.casefold() in seen or entry.flag_bits & 1
                        or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or any(part.casefold() in _ACTIVE_PARTS for part in parts)):
                    raise _invalid()
                seen.add(name.casefold())
            if main not in archive.namelist():
                raise _invalid()
            types = _xml(archive, archive.getinfo("[Content_Types].xml"))
            if types.tag != _CONTENT_TYPES + "Types":
                raise _invalid()
            if any(any(active in child.get("ContentType", "").casefold() for active in _ACTIVE_TYPES) for child in types):
                raise _invalid()
            if not any(child.get("PartName") == "/" + main and child.get("ContentType") == main_type for child in types):
                raise _invalid()
            for entry in entries:
                name = entry.filename
                if name.casefold().endswith(".rels"):
                    _check_relationships(_xml(archive, entry), name, seen)
                elif name.casefold().endswith(".svg"):
                    _check_svg(_xml(archive, entry))
                elif fonts is not None and name.endswith(".xml") and name.startswith(("word/", "ppt/")):
                    # Fonts are advisory. A bounded incomplete inventory is
                    # unknown; it must never be represented as no missing fonts.
                    if entry.file_size > min(font_budget, _MAX_XML_BYTES):
                        fonts = None
                        continue
                    font_budget -= entry.file_size
                    try:
                        fonts.update(_font_declarations(_xml(archive, entry)))
                    except OfficePreviewError:
                        fonts = None
                    if fonts is not None and len(fonts) > 128:
                        fonts = None
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, EOFError, NotImplementedError):
        raise _invalid() from None
    return fonts


def prepare_office_preview(store, metadata: dict, inbox_scope: str) -> PreparedOfficePreview:
    extension = PurePosixPath(metadata["name"]).suffix.lower()
    if metadata.get("storageMode") != "snapshot" or extension not in _MAIN_PARTS:
        raise OfficePreviewError("unsupported_format", "仅支持固定 DOCX 或 PPTX 版本预览", 415)
    size = metadata["size"]
    if size > _MAX_SOURCE_BYTES:
        raise OfficePreviewError("input_too_large", "Office 文件超过预览大小限制", 413)
    executable = _find_soffice()
    if executable is None:
        raise OfficePreviewError("converter_unavailable", "本机未安装可用的 LibreOffice，请下载原文件", 503)
    if not _SLOTS.acquire(blocking=False):
        raise OfficePreviewError("busy", "文档预览正在转换其他文件，请稍后重试", 503)
    scratch = None
    try:
        scratch = tempfile.TemporaryDirectory(prefix="easel-office-", ignore_cleanup_errors=True)
        source = Path(scratch.name) / ("source" + extension)
        digest = hashlib.sha256()
        total = 0
        try:
            with store.open_content(metadata["artifactId"], inbox_scope=inbox_scope) as incoming, source.open("xb") as target:
                while chunk := incoming.read(min(1024 * 1024, size - total + 1)):
                    total += len(chunk)
                    if total > size:
                        break
                    digest.update(chunk)
                    target.write(chunk)
        except ArtifactError:
            raise OfficePreviewError("source_unavailable", "固定文件版本不可用", 409) from None
        if total != size or digest.hexdigest() != metadata["sha256"]:
            raise OfficePreviewError("source_unavailable", "固定文件版本校验失败", 409)
        fonts = _inspect_package(source)
        return PreparedOfficePreview(scratch, source, executable, fonts)
    except BaseException as error:
        try:
            if scratch is not None:
                scratch.cleanup()
        finally:
            _SLOTS.release()
        if isinstance(error, OSError):
            raise OfficePreviewError("conversion_failed", "无法准备本机文档转换", 422) from None
        raise


def _environment(directory: Path, *, inventory=False) -> dict[str, str]:
    home = directory / "home"
    home.mkdir(exist_ok=True, mode=0o700)
    env = {"PATH": os.defpath, "HOME": str(Path.home() if inventory else home),
           "TMPDIR": str(directory), "TMP": str(directory), "TEMP": str(directory), "LANG": "en_US.UTF-8"}
    if os.name == "nt" and os.environ.get("SystemRoot"):
        env["SystemRoot"] = os.environ["SystemRoot"]
        env["USERPROFILE"] = env["HOME"]
    return env


async def _missing_fonts(prepared: PreparedOfficePreview) -> list[str] | None:
    executable = _find_font_inventory()
    if executable is None or prepared.fonts is None:
        return None
    if not prepared.fonts:
        return []
    directory = prepared.source.parent
    try:
        result = await _run_local_process([executable, "--format=%{family}\n"], cwd=directory,
                                          env=_environment(directory, inventory=True), timeout=4, output_limit=1024 * 1024)
    except (PublicationProcessStartError, PublicationProcessCleanupError, OSError):
        return None
    if result.returncode or result.timed_out or result.output_truncated:
        return None
    inventory = {name.casefold() for line in result.stdout.splitlines() for item in line.split(",")
                 if (name := _font_name(item))}
    if not inventory:
        return None
    return sorted((name for name in prepared.fonts if name.casefold() not in inventory), key=str.casefold)


def _read_pdf(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink():
                raise OfficePreviewError("invalid_output", "转换器没有返回可用 PDF", 422)
            if info.st_size > MAX_PDF_BYTES:
                raise OfficePreviewError("output_too_large", "转换后的 PDF 超过预览大小限制", 413)
            pdf = stream.read(MAX_PDF_BYTES + 1)
    except OSError:
        raise OfficePreviewError("conversion_failed", "LibreOffice 未能生成预览", 422) from None
    if len(pdf) > MAX_PDF_BYTES:
        raise OfficePreviewError("output_too_large", "转换后的 PDF 超过预览大小限制", 413)
    if not pdf.startswith(b"%PDF-") or b"%%EOF" not in pdf[-1024:]:
        raise OfficePreviewError("invalid_output", "转换器没有返回可用 PDF", 422)
    return pdf


async def _convert_process(args, directory: Path, output: Path, disconnected):
    if disconnected is not None and await disconnected():
        raise asyncio.CancelledError
    task = asyncio.create_task(_run_local_process(args, cwd=directory, env=_environment(directory),
                                                   timeout=_CONVERSION_TIMEOUT, output_limit=256 * 1024))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=.15)
            if task.done():
                break
            if disconnected is not None and await disconnected():
                raise asyncio.CancelledError
            try:
                size = output.lstat().st_size
            except FileNotFoundError:
                continue
            if size > MAX_PDF_BYTES:
                raise OfficePreviewError("output_too_large", "转换后的 PDF 超过预览大小限制", 413)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            # The shared process runner settles its owned subprocess group
            # before acknowledging cancellation; only then may scratch close.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()


async def convert_office_preview(prepared: PreparedOfficePreview, *, disconnected=None) -> tuple[bytes, list[str] | None]:
    directory = prepared.source.parent
    profile = directory / "profile"
    user = profile / "user"
    user.mkdir(parents=True, mode=0o700)
    (user / "registrymodifications.xcu").write_text(
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
        '</oor:items>', encoding="utf-8")
    args = [prepared.executable, "-env:UserInstallation=" + profile.as_uri(), "--headless", "--invisible",
            "--nologo", "--nodefault", "--nofirststartwizard", "--norestore",
            "--convert-to", "pdf", "--outdir", str(directory), str(prepared.source)]
    output = directory / "source.pdf"
    try:
        result = await _convert_process(args, directory, output, disconnected)
    except PublicationProcessStartError:
        raise OfficePreviewError("converter_unavailable", "无法启动本机 LibreOffice，请下载原文件", 503) from None
    except (PublicationProcessCleanupError, OSError):
        raise OfficePreviewError("conversion_failed", "本机文档转换未能完成", 422) from None
    if result.timed_out:
        raise OfficePreviewError("conversion_timeout", "Office 预览转换超时，请重试或下载原文件", 504)
    if result.returncode:
        raise OfficePreviewError("conversion_failed", "LibreOffice 未能生成预览", 422)
    pdf = _read_pdf(output)
    if disconnected is not None and await disconnected():
        raise asyncio.CancelledError
    return pdf, await _missing_fonts(prepared)
