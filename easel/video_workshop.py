"""Local video-project versions, immutable deliveries and explicit review records."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import uuid


class WorkshopError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


_PROJECT_ID = re.compile(r"[a-f0-9]{32}")
_CANVAS_ID = re.compile(r"[A-Za-z0-9:_-]{1,160}")
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_RENDER_TERMINAL = {"succeeded", "failed", "cancelled"}
_RENDER_SUBMISSION_ERRORS = {
    "submission_unconfirmed", "submission_timeout", "invalid_receipt",
    "source_revision_mismatch", "submission_interrupted",
}
_ATTEMPT_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:\+00:00|Z)")


def public_render_submission_attempts(receipt: dict) -> list[dict] | None:
    attempts = receipt.get("submission_attempts")
    if not isinstance(attempts, list):
        return None
    safe = []
    for entry in attempts:
        if not isinstance(entry, dict):
            continue
        number, status, started = entry.get("number"), entry.get("status"), entry.get("started_at")
        if (type(number) is not int or number < 1 or status not in ("submitting", "unconfirmed", "confirmed")
                or not isinstance(started, str) or not _ATTEMPT_TIME.fullmatch(started)):
            continue
        observation = {"number": number, "status": status, "started_at": started}
        ended, task_id, code = entry.get("settled_at"), entry.get("task_id"), entry.get("error_code")
        if status != "submitting" and isinstance(ended, str) and _ATTEMPT_TIME.fullmatch(ended):
            observation["settled_at"] = ended
        if status == "confirmed" and isinstance(task_id, str) and _CANVAS_ID.fullmatch(task_id):
            observation["task_id"] = task_id
        if status == "unconfirmed" and isinstance(code, str) and code in _RENDER_SUBMISSION_ERRORS:
            observation["error_code"] = code
        safe.append(observation)
    return safe



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _file_lock(path: Path, *, blocking: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path), threading.RLock())
    if not lock.acquire(blocking=blocking):
        yield False
        return
    try:
        with path.open("a+b") as handle:
            try:
                if os.name == "nt":
                    import msvcrt
                    if handle.seek(0, os.SEEK_END) == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except OSError as error:
                if not blocking and error.errno in (errno.EAGAIN, errno.EACCES, errno.EDEADLK):
                    yield False
                    return
                raise
            try:
                yield True
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock.release()


class WorkshopStore:
    def __init__(self, outputs: Path, *, strict_canvas_bindings: bool = False):
        self.outputs = Path(outputs).resolve()
        self.directory = self.outputs / "_workshop" / "projects"
        self.strict_canvas_bindings = strict_canvas_bindings

    def _canvas_bindings(self) -> dict[str, str]:
        path = self.directory.parent / "canvas_bindings.json"
        if path.is_symlink():
            raise WorkshopError("画布归属记录不能是符号链接", 503)
        try:
            bindings = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise WorkshopError("画布归属记录无法读取，请保留文件排查", 503) from None
        if not isinstance(bindings, dict) or any(
                not isinstance(key, str) or not _PROJECT_ID.fullmatch(key)
                or not isinstance(value, str) or not _CANVAS_ID.fullmatch(value)
                for key, value in bindings.items()):
            raise WorkshopError("画布归属记录格式损坏", 503)
        return bindings

    def _register_canvas(self, project: dict, canvas_id: str):
        bindings = self._canvas_bindings()
        if any(value == canvas_id and identifier != project["id"] for identifier, value in bindings.items()):
            raise WorkshopError("该画布已归属于其他视频项目", 403)
        bindings[project["id"]] = canvas_id
        descriptor, name = tempfile.mkstemp(dir=self.directory.parent, prefix=".canvas-bindings-", suffix=".tmp")
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(bindings, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory.parent / "canvas_bindings.json")
        finally:
            temporary.unlink(missing_ok=True)

    def _assert_canvas_binding(self, project: dict, receipt: dict | None = None):
        if not self.strict_canvas_bindings:
            return
        canvas_id = project.get("beeftv_canvas_id")
        if canvas_id and self._canvas_bindings().get(project["id"]) != canvas_id:
            raise WorkshopError("该画布尚未确认属于当前 IP，请通过关联画布创建独立连接", 403)
        if receipt is not None and (not canvas_id or receipt.get("canvas_id") != canvas_id):
            raise WorkshopError("渲染回执尚未确认属于当前 IP 的画布，请重新导出", 403)

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with _file_lock(self.directory.parent / "writer.lock"):
            yield

    def _path(self, identifier: str) -> Path:
        if not _PROJECT_ID.fullmatch(identifier):
            raise WorkshopError("视频项目编号无效")
        return self.directory / f"{identifier}.json"

    def _read(self, identifier: str) -> dict:
        path = self._path(identifier)
        if path.is_symlink():
            raise WorkshopError("视频项目记录不能是符号链接")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise WorkshopError("视频项目不存在", 404) from None
        except (ValueError, OSError):
            raise WorkshopError("视频项目记录无法读取，请保留文件排查", 503) from None
        if not isinstance(data, dict) or data.get("id") != identifier or type(data.get("revision")) is not int:
            raise WorkshopError("视频项目记录格式损坏", 503)
        data.setdefault("renders", [])
        data.setdefault("publications", [])
        data.setdefault("archived_at", None)
        return data

    def _write(self, project: dict) -> dict:
        project["updated_at"] = _now()
        path = self._path(project["id"])
        descriptor, name = tempfile.mkstemp(dir=self.directory, prefix=".workshop-", suffix=".tmp")
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(project, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return project

    @staticmethod
    def _expect(project: dict, revision: int):
        if project["revision"] != revision:
            raise WorkshopError("项目已被修改，请重新读取当前版本；不会覆盖人工修改", 409)

    def list(self, render_status=None) -> list[dict]:
        with self._locked():
            projects = [self._read(path.stem) for path in self.directory.glob("*.json")]
        if render_status is not None:
            for index, project in enumerate(projects):
                receipt = project["renders"][-1] if project["renders"] else None
                if not receipt or receipt.get("artifact_id") or receipt.get("status") not in {"queued", "running"}:
                    continue
                try:
                    self.render_status(project["id"], receipt["id"], render_status=render_status)
                    projects[index] = self.get(project["id"])
                except WorkshopError:
                    # A list remains useful during a transient runtime failure.
                    # No submission, collection or engine scan is performed here.
                    pass
        return sorted(projects, key=lambda value: value["updated_at"], reverse=True)

    def get(self, identifier: str, canvas_state=None) -> dict:
        with self._locked():
            project = self._read(identifier)
            if canvas_state is not None and project.get("beeftv_canvas_id"):
                try:
                    revision = self._engine_revision(project, canvas_state)
                    latest = project["deliverables"][-1] if project["deliverables"] else None
                    project["engine_current_revision"] = revision
                    project["engine_dirty"] = bool(latest and latest.get("engine_revision") != revision)
                    project["engine_message"] = "画布已有新修改，请重新导出" if project["engine_dirty"] else ""
                except WorkshopError as error:
                    project["engine_current_revision"] = None
                    project["engine_dirty"] = True
                    project["engine_message"] = ("该画布尚未确认属于当前 IP，请重新关联独立画布"
                                                 if self.strict_canvas_bindings and error.status == 403
                                                 else "暂时无法核对画布版本，恢复连接后再审核")
            return project

    def _engine_revision(self, project: dict, canvas_state) -> int | None:
        if canvas_state is None or not project.get("beeftv_canvas_id"):
            return None
        self._assert_canvas_binding(project)
        try:
            snapshot = canvas_state(dict(project))
            revision = snapshot.get("revision") if isinstance(snapshot, dict) else None
            if type(revision) is not int or revision < 0:
                raise ValueError("Invalid upstream revision")
            return revision
        except Exception:
            raise WorkshopError("暂时无法核对 BeefTV 画布版本，请恢复连接后重试", 503) from None

    def _check_engine(self, project: dict, expected_engine_revision: int | None, canvas_state):
        if canvas_state is None or not project.get("beeftv_canvas_id"):
            return
        if expected_engine_revision is None:
            raise WorkshopError("请提供真实渲染回执的 engine_revision，不能用当前版本冒充来源", 422)
        if self._engine_revision(project, canvas_state) != expected_engine_revision:
            raise WorkshopError("画布已有新修改，该成片不对应当前画布；请重新导出", 409)

    def create(self, values: dict, canvas_creator=None) -> dict:
        identifier = uuid.uuid4().hex
        project = {
            "id": identifier, "title": values["title"], "brief": values.get("brief", ""),
            "reference_url": values.get("reference_url", ""), "aspect_ratio": values.get("aspect_ratio", "9:16"),
            "revision": 1, "stage": "draft", "beeftv_canvas_id": "", "runtime_message": "",
            "deliverables": [], "reviews": [], "comments": [], "renders": [],
            "publications": [], "archived_at": None, "created_at": _now(),
        }
        with self._locked():
            self._write(project)
            if canvas_creator is not None:
                self._request_canvas(project, canvas_creator)
                self._write(project)
        return project

    def _request_canvas(self, project: dict, canvas_creator) -> bool:
        try:
            if canvas_creator is None:
                raise ValueError("Canvas bridge unavailable")
            result = canvas_creator(dict(project))
            canvas_id = result.get("beeftv_canvas_id", result.get("id", "")) if isinstance(result, dict) else result
            if not isinstance(canvas_id, str) or not _CANVAS_ID.fullmatch(canvas_id):
                raise ValueError("Invalid canvas identifier")
            self._register_canvas(project, canvas_id)
            project["beeftv_canvas_id"] = canvas_id
            project["stage"] = "editing"
            project["runtime_message"] = ""
            return True
        except Exception:
            # Upstream bodies and credentials must never reach the browser.
            project["runtime_message"] = "BeefTV 画布暂未关联；项目已保存，可在服务恢复后关联。"
            return False

    def attach_canvas(self, identifier: str, expected_revision: int, canvas_creator=None) -> dict:
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            if project["beeftv_canvas_id"] and (not self.strict_canvas_bindings
                    or self._canvas_bindings().get(project["id"]) == project["beeftv_canvas_id"]):
                return project
            if self._request_canvas(project, canvas_creator):
                project["revision"] += 1
            return self._write(project)

    def update(self, identifier: str, expected_revision: int, values: dict) -> dict:
        if self.strict_canvas_bindings and "beeftv_canvas_id" in values:
            raise WorkshopError("画布归属由服务端创建，不能通过内容编辑指定画布编号", 403)
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            if "beeftv_canvas_id" in values and values["beeftv_canvas_id"] and not _CANVAS_ID.fullmatch(values["beeftv_canvas_id"]):
                raise WorkshopError("BeefTV 画布编号无效")
            changed = any(project.get(key) != value for key, value in values.items())
            if changed:
                project.update(values)
                project["revision"] += 1
                project["stage"] = "editing"
                project["runtime_message"] = ""
                self._write(project)
            return project

    def comment(self, identifier: str, expected_revision: int, values: dict) -> dict:
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            project["comments"].append({
                "id": uuid.uuid4().hex, "revision": project["revision"], **values, "created_at": _now(),
            })
            return self._write(project)

    def archive(self, identifier: str, expected_revision: int, archived: bool) -> dict:
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            if bool(project["archived_at"]) == archived:
                return project
            project["archived_at"] = _now() if archived else None
            return self._write(project)

    def publication(self, identifier: str, expected_revision: int, artifact_id: str,
                    values: dict, canvas_state=None) -> dict:
        """Record an already completed external publication; never publish media."""
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            artifact = self._reviewable_artifact(project, artifact_id, canvas_state)
            latest_review = next((review for review in reversed(project["reviews"])
                if review.get("revision") == project["revision"]
                and review.get("artifact_id") == artifact_id
                and review.get("artifact_sha256") == artifact["sha256"]), None)
            if not latest_review or latest_review.get("kind") != "human" or latest_review.get("decision") != "approved":
                raise WorkshopError("请先完成人工审阅，模拟预审不能作为发布依据", 409)
            project["publications"].append({
                "id": uuid.uuid4().hex, "revision": project["revision"], "artifact_id": artifact_id,
                "artifact_sha256": artifact["sha256"], "platform": values["platform"], "url": values["url"],
                "published_at": values.get("published_at") or _now(), "created_at": _now(),
            })
            return self._write(project)

    def _media_path(self, relative: str) -> Path:
        path = Path(relative)
        if not relative or path.is_absolute() or "\\" in relative or any(part.startswith((".", "_")) for part in path.parts):
            raise WorkshopError("交付文件必须来自内容库中的普通项目目录")
        candidate = self.outputs
        for part in path.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise WorkshopError("交付文件不能经过符号链接")
        resolved = candidate.resolve()
        if self.outputs not in resolved.parents or not resolved.is_file() or resolved.suffix.lower() not in _VIDEO_SUFFIXES:
            raise WorkshopError("交付文件必须是内容库内已存在的视频")
        return resolved

    @staticmethod
    def _validate_video(path: Path):
        try:
            result = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=width,height:format=duration", "-of", "json", str(path),
            ], capture_output=True, text=True, timeout=20, check=True)
            probe = json.loads(result.stdout)
            stream = probe["streams"][0]
            if stream["width"] <= 0 or stream["height"] <= 0 or float(probe["format"]["duration"]) <= 0:
                raise ValueError
        except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
            raise WorkshopError("视频无法通过媒体校验，请先完成渲染再登记交付") from None

    def deliver(self, identifier: str, expected_revision: int, relative: str,
                engine_revision: int | None = None, canvas_state=None, render_id: str | None = None) -> dict:
        with self._locked():
            project = self._read(identifier)
            receipt = self._render_entry(project, render_id) if render_id is not None else None
            if receipt is not None:
                if receipt.get("artifact_id"):
                    return project
                if receipt["content_revision"] != expected_revision or receipt["engine_revision"] != engine_revision:
                    raise WorkshopError("渲染回执不属于本次待交付版本", 409)
            self._expect(project, expected_revision)
            self._check_engine(project, engine_revision, canvas_state)
            source = self._media_path(relative)
            self._validate_video(source)
            title = re.sub(r"[^\w\u4e00-\u9fff -]", "-", project["title"]).strip(" -")[:64] or "视频工坊"
            directory = self.outputs / f"{title}-{identifier[:8]}"
            if directory.is_symlink():
                raise WorkshopError("交付目录不能是符号链接")
            directory.mkdir(exist_ok=True)
            revision = project["revision"] + 1
            artifact_id = uuid.uuid4().hex
            destination = directory / f"final-r{revision}-{artifact_id[:12]}{source.suffix.lower()}"
            try:
                # Copy rather than retain a mutable source path. Exclusive creation
                # prevents replacing another accepted delivery.
                before = source.stat()
                with source.open("rb") as incoming, destination.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise WorkshopError("源视频在登记期间发生变化，请重新登记", 409)
                self._validate_video(destination)
                digest = _digest(destination)
                self._check_engine(project, engine_revision, canvas_state)
                project["revision"] = revision
                project["stage"] = "rendered"
                artifact = {
                    "id": artifact_id, "path": destination.relative_to(self.outputs).as_posix(),
                    "sha256": digest, "revision": revision, "engine_revision": engine_revision, "created_at": _now(),
                }
                if receipt is not None:
                    artifact["render_id"] = render_id
                    receipt.update(artifact_id=artifact_id, collected_revision=revision,
                                   collected_at=_now(), status="succeeded", progress=1.0, message="成片已收取")
                project["deliverables"].append(artifact)
                return self._write(project)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise

    @staticmethod
    def _render_entry(project: dict, render_id: str) -> dict:
        if not _PROJECT_ID.fullmatch(render_id):
            raise WorkshopError("渲染回执编号无效")
        receipt = next((item for item in project.get("renders", []) if item["id"] == render_id), None)
        if receipt is None:
            raise WorkshopError("渲染回执不存在", 404)
        return receipt

    @staticmethod
    def _render_result(result: dict) -> dict:
        if not isinstance(result, dict):
            raise WorkshopError("BeefTV 未返回有效渲染状态", 503)
        aliases = {"pending": "queued", "processing": "running", "success": "succeeded",
                   "completed": "succeeded", "error": "failed", "canceled": "cancelled"}
        status = str(result.get("status", "")).lower()
        status = aliases.get(status, status)
        if status not in {"queued", "running", *_RENDER_TERMINAL}:
            raise WorkshopError("BeefTV 返回未知渲染状态，未当成成功", 503)
        progress = result.get("progress")
        known = type(progress) in (int, float) and 0 <= progress <= 1
        if not known:
            progress = 1.0 if status == "succeeded" else 0.0
        message = {
            "queued": "渲染已排队", "running": "渲染进行中", "succeeded": "渲染成功",
            "failed": "渲染失败", "cancelled": "渲染已取消",
        }[status]
        return {"status": status, "progress": progress, "progress_known": known, "message": message}

    @staticmethod
    def _observe_confirmed_render(receipt: dict, result: dict, **identity) -> None:
        observed_at = _now()
        if result["status"] in _RENDER_TERMINAL:
            # The engine supplied a terminal status; record when it was first
            # confirmed locally, independent of subsequent artifact collection.
            receipt.setdefault("completed_at", observed_at)
        elif not result["progress_known"] and receipt.get("progress_known") is True:
            result = {**result, "progress": receipt["progress"], "progress_known": True}
        receipt.update(result, **identity, submission_state="confirmed", updated_at=observed_at)

    def start_render(self, identifier: str, expected_revision: int, canvas_state=None, render_start=None) -> dict:
        if canvas_state is None or render_start is None:
            raise WorkshopError("BeefTV 导出服务尚未接通", 503)
        self._path(identifier)
        # A live submission owns this lock, including while the project writer
        # lock is released for the external call. A process exit releases it,
        # allowing an explicit POST to reconcile the saved operation safely.
        with _file_lock(self.directory.parent / "renders" / f"{identifier}-submit.lock", blocking=False) as acquired:
            if acquired:
                return self._submit_render(identifier, expected_revision, canvas_state, render_start)
            with self._locked():
                project = self._read(identifier)
                self._expect(project, expected_revision)
                engine_revision = self._engine_revision(project, canvas_state)
                existing = self._matching_render(project, engine_revision)
                if existing is None:
                    raise WorkshopError("另一导出操作正在提交，请稍后核对回执", 503)
                self._assert_canvas_binding(project, existing)
                return existing

    @staticmethod
    def _matching_render(project: dict, engine_revision: int) -> dict | None:
        return next((receipt for receipt in reversed(project["renders"])
                     if receipt["engine_revision"] == engine_revision
                     and (receipt["content_revision"] == project["revision"]
                          or receipt.get("collected_revision") == project["revision"])), None)

    @staticmethod
    def _submission_confirmed(receipt: dict) -> bool:
        if "submission_state" in receipt:
            return receipt["submission_state"] == "confirmed"
        # Old terminal failures did not distinguish a rejected task from a lost
        # response. Reconcile their original operation before allowing a new one.
        return bool(receipt.get("task_id")) and receipt["status"] in {"queued", "running", "succeeded"}

    @staticmethod
    def _settle_render_submission(receipt: dict, status: str, **evidence) -> None:
        attempts = receipt.get("submission_attempts")
        if attempts and attempts[-1]["status"] == "submitting":
            attempts[-1].update(status=status, settled_at=_now(), **evidence)

    def _submit_render(self, identifier: str, expected_revision: int, canvas_state, render_start) -> dict:
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            if not project.get("beeftv_canvas_id"):
                raise WorkshopError("请先关联真实 BeefTV 画布")
            engine_revision = self._engine_revision(project, canvas_state)
            existing = self._matching_render(project, engine_revision)
            if existing is not None:
                self._assert_canvas_binding(project, existing)
            confirmed = existing is not None and self._submission_confirmed(existing)
            if existing is not None and (existing.get("artifact_id") or confirmed and existing["status"] not in {"failed", "cancelled"}):
                return existing
            if existing is not None and not confirmed:
                receipt = existing
                receipt.update(status="queued", progress=0.0, progress_known=False, submission_state="unconfirmed",
                               message="正在核对原渲染操作，未创建另一任务", updated_at=_now())
            else:
                render_id = uuid.uuid4().hex
                receipt = {
                    "id": render_id,
                    "operation_id": f"easel-render-{identifier}-r{project['revision']}-e{engine_revision}-{render_id}",
                    "content_revision": project["revision"], "engine_revision": engine_revision,
                    "canvas_id": project["beeftv_canvas_id"],
                    "task_id": "", "status": "queued", "submission_state": "unconfirmed",
                    "progress": 0.0, "progress_known": False, "message": "正在提交本地渲染任务",
                    "created_at": _now(), "updated_at": _now(),
                }
                project["renders"].append(receipt)
            self._settle_render_submission(receipt, "unconfirmed", error_code="submission_interrupted")
            attempts = receipt.setdefault("submission_attempts", [])
            attempts.append({"number": len(attempts) + 1, "status": "submitting", "started_at": _now()})
            self._write(project)
            snapshot = {**project, "engine_current_revision": engine_revision}
        # Never keep the project writer lock over an external operation.
        result = None
        error_code = "submission_unconfirmed"
        try:
            result = render_start(snapshot, receipt["operation_id"])
            if not isinstance(result, dict) or not isinstance(result.get("task_id"), str) or not _CANVAS_ID.fullmatch(result["task_id"]):
                error_code = "invalid_receipt"
                raise WorkshopError("BeefTV 未提供有效任务回执，提交结果待核对", 503)
            if type(result.get("engine_revision")) is not int or result["engine_revision"] != engine_revision:
                error_code = "source_revision_mismatch"
                raise WorkshopError("渲染来源版本与请求不一致，未采用结果", 409)
            error_code = "invalid_receipt"
            update = self._render_result(result)
        except Exception as error:
            if isinstance(error, TimeoutError):
                error_code = "submission_timeout"
            with self._locked():
                project = self._read(identifier)
                saved = self._render_entry(project, receipt["id"])
                saved.update(status="failed", progress=0.0, progress_known=False, submission_state="unconfirmed",
                             message="渲染提交结果待核对；可点击重试核对原操作，尚未确认任务失败。", updated_at=_now())
                if isinstance(result, dict):
                    if isinstance(result.get("task_id"), str) and _CANVAS_ID.fullmatch(result["task_id"]):
                        saved["task_id"] = result["task_id"]
                    if type(result.get("engine_revision")) is int:
                        saved["reported_engine_revision"] = result["engine_revision"]
                self._settle_render_submission(saved, "unconfirmed", error_code=error_code)
                self._write(project)
            if error_code == "source_revision_mismatch":
                raise WorkshopError("渲染来源版本与请求不一致，未采用结果", 409) from None
            raise WorkshopError("BeefTV 渲染提交结果待核对，已保留原操作回执", 503) from None
        with self._locked():
            project = self._read(identifier)
            saved = self._render_entry(project, receipt["id"])
            self._observe_confirmed_render(saved, update, task_id=result["task_id"])
            self._settle_render_submission(saved, "confirmed", task_id=result["task_id"])
            self._write(project)
            return saved

    def render_status(self, identifier: str, render_id: str, render_status=None) -> dict:
        with self._locked():
            project = self._read(identifier)
            receipt = dict(self._render_entry(project, render_id))
            self._assert_canvas_binding(project, receipt)
        if not self._submission_confirmed(receipt):
            # A lost process can leave a queued intent with no response. Report
            # it as retryable only when no live submitter owns the lock. This
            # observation never calls render_start or creates an upstream task.
            with _file_lock(self.directory.parent / "renders" / f"{identifier}-submit.lock", blocking=False) as acquired:
                if acquired:
                    with self._locked():
                        project = self._read(identifier)
                        saved = self._render_entry(project, render_id)
                        if not self._submission_confirmed(saved) and saved["status"] not in _RENDER_TERMINAL:
                            saved.update(status="failed", progress_known=False, submission_state="unconfirmed", updated_at=_now(),
                                         message="原渲染提交已中断，结果待核对；请点击重试核对原操作。")
                            self._settle_render_submission(saved, "unconfirmed", error_code="submission_interrupted")
                            self._write(project)
                        return saved
            return receipt
        if receipt["status"] in _RENDER_TERMINAL or not receipt["task_id"]:
            return receipt
        if render_status is None:
            raise WorkshopError("BeefTV 渲染查询尚未接通", 503)
        try:
            result = render_status(project, receipt)
            update = self._render_result(result)
        except WorkshopError:
            raise
        except Exception:
            raise WorkshopError("暂时无法查询渲染，原任务已保留，未自动重发", 503) from None
        with self._locked():
            project = self._read(identifier)
            saved = self._render_entry(project, render_id)
            if saved["status"] not in _RENDER_TERMINAL:
                self._observe_confirmed_render(saved, update)
                self._write(project)
            return saved

    def collect_render(self, identifier: str, render_id: str, expected_revision: int,
                       canvas_state=None, render_status=None, render_collect=None) -> dict:
        if not _PROJECT_ID.fullmatch(render_id):
            raise WorkshopError("渲染回执编号无效")
        # A separate per-receipt lock serializes downloads/adoption without
        # recursively acquiring the project lock used by deliver().
        with _file_lock(self.directory.parent / "renders" / f"{render_id}.lock"):
            with self._locked():
                project = self._read(identifier)
                receipt = dict(self._render_entry(project, render_id))
                self._assert_canvas_binding(project, receipt)
                if receipt.get("artifact_id"):
                    return project
                self._expect(project, expected_revision)
                if expected_revision != receipt["content_revision"]:
                    raise WorkshopError("工程已修改，不能采用旧内容版本的导出", 409)
                if canvas_state is None or render_collect is None:
                    raise WorkshopError("BeefTV 成片收取服务尚未接通", 503)
                self._check_engine(project, receipt["engine_revision"], canvas_state)
            receipt = self.render_status(identifier, render_id, render_status)
            if receipt["status"] != "succeeded":
                raise WorkshopError("渲染尚未成功，不能登记为成片", 409)
            try:
                result = render_collect(project, receipt)
            except Exception:
                raise WorkshopError("成片收取暂未完成，已保留原渲染任务，可再次收取", 503) from None
            if not isinstance(result, dict) or not isinstance(result.get("path"), str):
                raise WorkshopError("BeefTV 未返回可登记的成片文件", 503)
            if type(result.get("engine_revision")) is not int or result["engine_revision"] != receipt["engine_revision"]:
                raise WorkshopError("成片回执的来源版本不一致，未采用结果", 409)
            return self.deliver(identifier, expected_revision, result["path"], engine_revision=result["engine_revision"],
                                canvas_state=canvas_state, render_id=render_id)

    def _reviewable_artifact(self, project: dict, artifact_id: str, canvas_state=None) -> dict:
        artifact = next((item for item in project["deliverables"] if item["id"] == artifact_id), None)
        if artifact is None:
            raise WorkshopError("交付版本不存在", 404)
        if artifact["revision"] != project["revision"]:
            raise WorkshopError("该成片已不是当前工程版本，请重新渲染后审核", 409)
        try:
            unchanged = _digest(self._media_path(artifact["path"])) == artifact["sha256"]
        except (OSError, WorkshopError):
            unchanged = False
        if not unchanged:
            raise WorkshopError("交付文件内容已变化，不能沿用旧摘要审核", 409)
        self._check_engine(project, artifact.get("engine_revision"), canvas_state)
        return artifact

    def review(self, identifier: str, expected_revision: int, artifact_id: str, values: dict, canvas_state=None) -> dict:
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            artifact = self._reviewable_artifact(project, artifact_id, canvas_state)
            project["reviews"].append({
                "id": uuid.uuid4().hex, "revision": project["revision"], "artifact_id": artifact_id,
                "artifact_sha256": artifact["sha256"], **values, "created_at": _now(),
            })
            project["stage"] = ("changes_requested" if values["decision"] == "changes_requested"
                                else "approved" if values["kind"] == "human" else "simulated_reviewed")
            return self._write(project)
