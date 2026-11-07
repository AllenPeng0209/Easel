"""Project-owned browser-session snapshots shared by independent account tools.

The login coordinator owns writes. Consumers restore a complete snapshot into
their own context; they never share a physical browser profile or write back a
possibly stale refresh. A generation fences asynchronous login completions.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit


class SessionValidationError(ValueError):
    """A path, snapshot or metadata record cannot be trusted."""


class SessionConflictError(RuntimeError):
    """This login was replaced or revoked before it could commit."""


class SessionApplyError(RuntimeError):
    """The browser cannot safely restore the unified session."""


_HOSTS = {"douyin": "douyin.com", "xhs": "xiaohongshu.com"}
_SECRET_KEY = re.compile(r"password|secret|token|cookie|storage.?state|authorization|credential", re.I)
_MAX_BYTES = 8 * 1024 * 1024


def _platform(platform: str) -> str:
    value = "xhs" if platform == "xiaohongshu" else platform
    if value not in _HOSTS:
        raise SessionValidationError("该平台暂不支持统一登录会话")
    return value


def _directory(workspace: Path, *, create: bool) -> Path | None:
    root = Path(workspace).expanduser().absolute()
    if ".." in root.parts or root.is_symlink() or root.resolve() != root:
        raise SessionValidationError("账号会话目录不能使用符号链接或越界路径")
    if not root.exists():
        if not create:
            return None
        raise SessionValidationError("当前 IP 工作目录不存在")
    if not root.is_dir():
        raise SessionValidationError("当前 IP 工作目录无效")
    path = root
    for name in (".easel", "account-sessions"):
        path = path / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise SessionValidationError("账号会话目录不能使用符号链接或越界路径")
        if not path.exists():
            if not create:
                return None
            path.mkdir(mode=0o700, exist_ok=True)
        if not path.is_dir():
            raise SessionValidationError("账号会话目录无效")
        if create:
            path.chmod(0o700)
    return path


def _regular(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise SessionValidationError("账号会话文件不能使用符号链接或特殊文件")


@contextmanager
def _locked(directory: Path, platform: str):
    path = directory / f"{platform}.lock"
    _regular(path)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        raise SessionValidationError("无法安全打开账号会话锁") from None
    os.fchmod(descriptor, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _json_copy(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > _MAX_BYTES:
            raise ValueError
        return json.loads(encoded)
    except (TypeError, ValueError, UnicodeError):
        raise SessionValidationError("账号会话数据格式无效") from None


def _metadata(value: dict) -> dict:
    if not isinstance(value, dict):
        raise SessionValidationError("账号资料格式无效")
    def check(item, depth=0):
        if depth > 8:
            raise SessionValidationError("账号资料结构过深")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or _SECRET_KEY.search(key):
                    raise SessionValidationError("账号资料不能包含登录凭据字段")
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise SessionValidationError("账号资料格式无效")
    check(value)
    return _json_copy(value)


def _state(platform: str, state: dict) -> dict:
    if not isinstance(state, dict) or set(state) - {"cookies", "origins"}:
        raise SessionValidationError("浏览器登录状态格式无效")
    copied = _json_copy(state)
    cookies, origins = copied.get("cookies", []), copied.get("origins", [])
    if not isinstance(cookies, list) or not isinstance(origins, list) or not (cookies or origins):
        raise SessionValidationError("浏览器登录状态为空或格式无效")
    allowed = _HOSTS[platform]
    def valid_host(value):
        return isinstance(value, str) and (value == allowed or value.endswith("." + allowed))
    for cookie in cookies:
        if (not isinstance(cookie, dict) or not isinstance(cookie.get("domain"), str)
                or not valid_host(cookie["domain"].lstrip("."))
                or not isinstance(cookie.get("name"), str)
                or not isinstance(cookie.get("value"), str)
                or not isinstance(cookie.get("path"), str) or not cookie["path"].startswith("/")):
            raise SessionValidationError("Cookie 不属于当前平台或格式无效")
        if any(type(cookie[key]) is not bool for key in ("httpOnly", "secure") if key in cookie):
            raise SessionValidationError("Cookie 格式无效")
        expiry = cookie.get("expires", -1)
        if type(expiry) not in (float, int) or not math.isfinite(expiry):
            raise SessionValidationError("Cookie 失效时间无效")
        if "sameSite" in cookie and cookie["sameSite"] not in ("Lax", "Strict", "None"):
            raise SessionValidationError("Cookie 格式无效")
    for origin in origins:
        if not isinstance(origin, dict) or not isinstance(origin.get("origin"), str):
            raise SessionValidationError("浏览器来源格式无效")
        try:
            parsed = urlsplit(origin["origin"])
            valid = (parsed.scheme == "https" and valid_host(parsed.hostname)
                     and not parsed.username and not parsed.password and parsed.port in (None, 443)
                     and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        storage = origin.get("localStorage", [])
        if (not valid or not isinstance(storage, list)
                or any(not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
                       or not isinstance(entry.get("value"), str) for entry in storage)
                or ("indexedDB" in origin and not isinstance(origin["indexedDB"], list))):
            raise SessionValidationError("浏览器来源不属于当前平台或格式无效")
    return {"cookies": cookies, "origins": origins}


def _read(path: Path, platform: str) -> dict | None:
    _regular(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError:
        raise SessionValidationError("无法安全读取账号会话文件") from None
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            value = json.loads(handle.read(_MAX_BYTES + 1))
        if (not isinstance(value, dict) or value.get("schema") != 1 or value.get("platform") != platform
                or type(value.get("generation")) is not int or value["generation"] < 1
                or type(value.get("revoked")) is not bool):
            raise ValueError
        if not value["revoked"]:
            if type(value.get("account_id")) is not int or value["account_id"] < 1:
                raise ValueError
            value["storage_state"] = _state(platform, value["storage_state"])
            value["identity"] = _metadata(value["identity"])
            value["capabilities"] = _metadata(value["capabilities"])
        return value
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise SessionValidationError("账号会话文件无效，请重新连接账号") from None


def _write(path: Path, value: dict) -> None:
    _regular(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        _regular(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def begin_generation(workspace: Path, platform: str) -> int:
    """Invalidate prior login intents and snapshots before starting a new login."""
    platform = _platform(platform)
    directory = _directory(workspace, create=True)
    with _locked(directory, platform):
        path = directory / f"{platform}.json"
        previous = _read(path, platform)
        generation = (previous["generation"] if previous else 0) + 1
        _write(path, {"schema": 1, "platform": platform, "generation": generation, "revoked": True})
    return generation


def save_session(workspace: Path, platform: str, account_id: int, state: dict, *,
                 identity: dict, capabilities: dict, expected_generation: int | None = None) -> dict:
    """Commit a verified login; async writers must supply their intent generation."""
    platform = _platform(platform)
    if type(account_id) is not int or account_id < 1:
        raise SessionValidationError("账号编号无效")
    if expected_generation is not None and (type(expected_generation) is not int or expected_generation < 1):
        raise SessionValidationError("账号会话代次无效")
    snapshot = _state(platform, state)
    safe_identity, safe_capabilities = _metadata(identity), _metadata(capabilities)
    directory = _directory(workspace, create=True)
    with _locked(directory, platform):
        path = directory / f"{platform}.json"
        previous = _read(path, platform)
        generation = previous["generation"] if previous else 1
        if expected_generation is not None and (previous is None or generation != expected_generation):
            raise SessionConflictError("本次登录已被替换或退出，请重新连接账号")
        record = {"schema": 1, "platform": platform, "generation": generation, "revoked": False,
                  "account_id": account_id, "storage_state": snapshot,
                  "identity": safe_identity, "capabilities": safe_capabilities}
        _write(path, record)
    return {key: record[key] for key in ("platform", "account_id", "generation", "identity", "capabilities")}


def load_session(workspace: Path, platform: str) -> dict | None:
    """Return a private browser snapshot, never suitable for an HTTP response."""
    platform = _platform(platform)
    directory = _directory(workspace, create=False)
    if directory is None:
        return None
    with _locked(directory, platform):
        value = _read(directory / f"{platform}.json", platform)
    return value if value and not value["revoked"] else None


def session_generation(workspace: Path, platform: str) -> int:
    """Return zero only when this platform has never entered unified login."""
    platform = _platform(platform)
    directory = _directory(workspace, create=False)
    if directory is None:
        return 0
    with _locked(directory, platform):
        value = _read(directory / f"{platform}.json", platform)
    return value["generation"] if value else 0


def revoke_session(workspace: Path, platform: str, *, expected_generation: int | None = None) -> bool | None:
    """Fence delayed login completions and remove persisted browser secrets."""
    if expected_generation is None:
        begin_generation(workspace, platform)
        return None
    if type(expected_generation) is not int or expected_generation < 1:
        raise SessionValidationError("账号会话代次无效")
    platform = _platform(platform)
    directory = _directory(workspace, create=False)
    if directory is None:
        return False
    with _locked(directory, platform):
        path = directory / f"{platform}.json"
        previous = _read(path, platform)
        if previous is None or previous["generation"] != expected_generation:
            return False
        _write(path, {"schema": 1, "platform": platform,
                      "generation": expected_generation + 1, "revoked": True})
    return True


def apply_session(context, platform: str) -> bool:
    """Restore the explicit current IP's complete snapshot before any navigation."""
    workspace = os.environ.get("EASEL_ROOT", "").strip()
    if not workspace:
        return False
    record = load_session(Path(workspace), platform)
    if record is None:
        if session_generation(Path(workspace), platform):
            raise SessionApplyError("统一登录尚未完成或已退出，请重新连接账号")
        return False
    restore = getattr(context, "set_storage_state", None)
    if not callable(restore):
        raise SessionApplyError("当前浏览器组件不支持完整恢复统一登录，请更新 Playwright 后重试")
    try:
        restore(record["storage_state"])
    except Exception:
        raise SessionApplyError("统一登录会话恢复失败，请重新登录或更新浏览器组件") from None
    return True
