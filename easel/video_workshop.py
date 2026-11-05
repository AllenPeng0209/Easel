"""Native video production, immutable deliveries and exact review records."""
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
import time
import uuid


class WorkshopError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class WorkshopCancelAuthorizationError(WorkshopError):
    """A Host authorization check failed before the provider mutation."""


_PROJECT_ID = re.compile(r"[a-f0-9]{32}")
_TASK_ID = re.compile(r"[A-Za-z0-9:_-]{1,160}")
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
_IMAGE_ASSET_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp'}
_VIDEO_ASSET_SUFFIXES = {'.mp4', '.mov', '.webm'}
_AUDIO_ASSET_SUFFIXES = {'.wav', '.mp3', '.m4a'}
_SUBTITLE_ASSET_SUFFIXES = {'.srt', '.ass', '.vtt'}
_DOCUMENT_ASSET_SUFFIXES = {'.md', '.txt', '.json', '.csv'}
ASSET_SUFFIXES = (_IMAGE_ASSET_SUFFIXES | _VIDEO_ASSET_SUFFIXES | _AUDIO_ASSET_SUFFIXES
                  | _SUBTITLE_ASSET_SUFFIXES | _DOCUMENT_ASSET_SUFFIXES)
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_RENDER_TERMINAL = {"succeeded", "failed", "cancelled"}
_RENDER_ACTIVE = {"queued", "running"}
_CANCEL_STATES = ("pending", "unconfirmed", "cancelled", "already_finished")
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
        if status == "confirmed" and isinstance(task_id, str) and _TASK_ID.fullmatch(task_id):
            observation["task_id"] = task_id
        if status == "unconfirmed" and isinstance(code, str) and code in _RENDER_SUBMISSION_ERRORS:
            observation["error_code"] = code
        safe.append(observation)
    return safe


def public_render_cancellation(receipt: dict) -> dict | None:
    value = receipt.get("cancellation")
    if not isinstance(value, dict):
        return None
    state, job_state = value.get("status"), value.get("jobStatus")
    task, render = value.get("taskId"), value.get("renderId")
    requested, updated = value.get("requestedAt"), value.get("updatedAt")
    if (state not in _CANCEL_STATES or job_state not in ("queued", "running", "succeeded", "failed", "cancelled")
            or job_state != receipt.get("status")
            or state in ("pending", "unconfirmed") and (job_state not in _RENDER_ACTIVE or requested is None)
            or state == "cancelled" and job_state != "cancelled"
            or state == "already_finished" and job_state not in ("succeeded", "failed")
            or not isinstance(task, str) or not _TASK_ID.fullmatch(task) or task != receipt.get("task_id")
            or not isinstance(render, str) or not _PROJECT_ID.fullmatch(render) or render != receipt.get("id")
            or type(updated) is not int or not 0 <= updated < 2**53
            or requested is not None and (type(requested) is not int or not 0 <= requested <= updated)):
        return None
    return {"status": state, "taskId": task, "renderId": render, "jobStatus": job_state,
            "requestedAt": requested, "updatedAt": updated}


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
    """Native assets, stable shot IDs, immutable media and version-bound review."""
    def __init__(self, outputs: Path):
        self.outputs = Path(outputs).resolve()
        self.directory = self.outputs / '_workshop' / 'projects'

    @contextmanager
    def _locked(self):
        if self.directory.is_symlink() or self.directory.parent.is_symlink():
            raise WorkshopError('视频工坊目录不能是符号链接', 403)
        self.directory.mkdir(parents=True, exist_ok=True)
        with _file_lock(self.directory.parent / 'writer.lock'):
            yield

    def _path(self, identifier):
        if not isinstance(identifier, str) or not _PROJECT_ID.fullmatch(identifier):
            raise WorkshopError('视频项目编号无效')
        return self.directory / f'{identifier}.json'

    def _read(self, identifier):
        path = self._path(identifier)
        if path.is_symlink():
            raise WorkshopError('视频项目记录不能是符号链接', 403)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise WorkshopError('视频项目不存在', 404) from None
        except (OSError, ValueError):
            raise WorkshopError('视频项目记录无法读取，请保留文件排查', 503) from None
        if not isinstance(data, dict) or data.get('id') != identifier or type(data.get('revision')) is not int:
            raise WorkshopError('视频项目记录格式损坏', 503)
        # Retire editor metadata in the read adapter. Original on-disk history
        # remains intact until the next explicit write; media is never deleted.
        for key in ('beeftv_canvas_id', 'runtime_message', 'engine_current_revision', 'engine_dirty', 'engine_message'):
            data.pop(key, None)
        data.setdefault('script', '')
        data.setdefault('raw_content', '')
        data.setdefault('assets', [])
        data.setdefault('shots', [])
        for key in ('deliverables', 'reviews', 'comments', 'renders', 'publications', 'publication_runs'):
            data.setdefault(key, [])
        data.setdefault('publication_preparation', None)
        data.setdefault('archived_at', None)
        return data

    def _write(self, project):
        project['updated_at'] = _now()
        descriptor, name = tempfile.mkstemp(dir=self.directory, prefix='.workshop-', suffix='.tmp')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(project, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._path(project['id']))
        finally:
            Path(name).unlink(missing_ok=True)
        return project

    @staticmethod
    def _expect(project, revision):
        if type(revision) is not int or project['revision'] != revision:
            raise WorkshopError('项目已被修改，请重新读取当前版本；不会覆盖人工修改', 409)

    @staticmethod
    def _changed(project, stage='editing'):
        project['revision'] += 1
        project['stage'] = stage
        project['publication_preparation'] = None

    def list(self):
        with self._locked():
            return sorted([self._read(path.stem) for path in self.directory.glob('*.json')],
                          key=lambda item: item['updated_at'], reverse=True)

    def get(self, identifier):
        with self._locked():
            return self._read(identifier)

    def create(self, values):
        project = {'id': uuid.uuid4().hex, 'title': values['title'], 'brief': values.get('brief', ''),
            'reference_url': values.get('reference_url', ''), 'aspect_ratio': values.get('aspect_ratio', '9:16'),
            'revision': 1, 'stage': 'draft', 'script': '', 'assets': [], 'shots': [],
            'deliverables': [], 'reviews': [], 'comments': [], 'renders': [], 'publications': [],
            'publication_runs': [], 'publication_preparation': None, 'archived_at': None, 'created_at': _now()}
        with self._locked():
            return self._write(project)

    def update(self, identifier, expected_revision, values):
        if set(values) - {'title', 'brief', 'reference_url', 'aspect_ratio'}:
            raise WorkshopError('不能通过内容编辑修改审核或发布记录', 422)
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            if any(project.get(key) != value for key, value in values.items()):
                project.update(values)
                self._changed(project)
                self._write(project)
            return project

    def _content_path(self, relative):
        if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative
                or any(ord(char) < 32 for char in relative)):
            raise WorkshopError('素材必须来自当前 IP 内容库中的普通项目目录', 422)
        path = Path(relative)
        if path.is_absolute() or any(part.startswith(('.', '_')) for part in relative.split('/')) or '' in relative.split('/'):
            raise WorkshopError('素材路径不能越界或引用隐藏目录', 422)
        candidate = self.outputs
        for part in path.parts:
            candidate /= part
            if candidate.is_symlink():
                raise WorkshopError('素材路径不能经过符号链接', 403)
        if self.outputs not in candidate.resolve().parents or not candidate.is_file():
            raise WorkshopError('当前 IP 的素材文件不存在', 422)
        return candidate

    def _media_path(self, relative):
        source = self._content_path(relative)
        if source.suffix.lower() not in _VIDEO_SUFFIXES:
            raise WorkshopError('交付文件必须是实际视频', 422)
        return source

    @staticmethod
    def _validate_video(path):
        try:
            result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                'stream=width,height:format=duration', '-of', 'json', str(path)],
                capture_output=True, text=True, timeout=20, check=True)
            data = json.loads(result.stdout)
            if data['streams'][0]['width'] <= 0 or data['streams'][0]['height'] <= 0 or float(data['format']['duration']) <= 0:
                raise ValueError
        except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
            raise WorkshopError('视频无法通过媒体校验，请先完成制作再交付', 422) from None

    def _snapshot(self, identifier, source, category):
        directory = self.outputs / ('视频工坊-' + identifier[:8]) / category
        if directory.is_symlink() or directory.parent.is_symlink():
            raise WorkshopError('素材保存目录不能是符号链接', 403)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / (uuid.uuid4().hex + source.suffix.lower())
        try:
            before = source.stat()
            with source.open('rb') as incoming, destination.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise WorkshopError('素材在登记期间发生变化，请重新读取', 409)
            return {'path': destination.relative_to(self.outputs).as_posix(), 'sha256': _digest(destination), 'created_at': _now()}
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_asset(source, kind):
        try:
            suffix = source.suffix.lower()
            if source.stat().st_size > 50 * 1024 * 1024:
                raise ValueError
            if kind in {'character', 'scene', 'prop', 'image'}:
                from PIL import Image
                if suffix not in _IMAGE_ASSET_SUFFIXES or source.stat().st_size > 10 * 1024 * 1024:
                    raise ValueError
                with Image.open(source) as image:
                    if image.width * image.height > 32 * 1024 * 1024:
                        raise ValueError
                    image.verify()
            elif kind in {'video', 'audio'}:
                result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type',
                    '-of', 'json', str(source)], check=True, capture_output=True, timeout=20)
                if not any(row['codec_type'] == kind for row in json.loads(result.stdout)['streams']):
                    raise ValueError
            elif kind in {'subtitle', 'document'}:
                allowed = _SUBTITLE_ASSET_SUFFIXES if kind == 'subtitle' else _DOCUMENT_ASSET_SUFFIXES
                if suffix not in allowed or source.stat().st_size > 5 * 1024 * 1024:
                    raise ValueError
                content = source.read_text(encoding='utf-8-sig')
                if not content.strip() or '\x00' in content:
                    raise ValueError
                if suffix == '.json':
                    json.loads(content)
            else:
                raise ValueError
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            raise WorkshopError('素材未通过实际媒体类型或大小校验', 422) from None

    def production(self, identifier, expected_revision, values):
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            old_assets = {item['id']: item for item in project['assets']}
            old_shots = {item['id']: item for item in project['shots']}
            ids = [item['id'] for item in values['assets']]
            shot_ids = [item['id'] for item in values['shots']]
            if len(set(ids)) != len(ids) or len(set(shot_ids)) != len(shot_ids):
                raise WorkshopError('素材和分镜编号不能重复', 422)
            if any(set(shot['asset_ids']) - set(ids) for shot in values['shots']):
                raise WorkshopError('分镜只能绑定本项目的素材', 422)
            assets = []
            for raw in values['assets']:
                previous = old_assets.get(raw['id'], {})
                asset = {**{key: previous[key] for key in ('image_prompt','appearance','styling','lighting') if key in previous}, **raw}
                versions = list(previous.get('versions', []))
                if raw.get('path'):
                    source = self._content_path(raw['path'])
                    if source.suffix.lower() not in ASSET_SUFFIXES:
                        raise WorkshopError('素材须为图片、音视频、字幕或制作文档', 422)
                    self._validate_asset(source, raw['kind'])
                    digest = _digest(source)
                    if previous.get('sha256') == digest:
                        asset.update({key: previous[key] for key in ('path', 'sha256', 'created_at') if key in previous})
                    else:
                        version = self._snapshot(identifier, source, 'assets')
                        versions.append(version)
                        asset.update(version)
                asset['versions'] = versions
                assets.append(asset)
            shots = [{**{key: old_shots.get(raw['id'], {}).get(key, '') for key in ('description', 'atmosphere')}, **raw, 'candidates': old_shots.get(raw['id'], {}).get('candidates', []),
                      'selected_candidate_id': old_shots.get(raw['id'], {}).get('selected_candidate_id')}
                     for raw in values['shots']]
            raw_content = values.get('raw_content', project['raw_content'])
            if (project['raw_content'], project['script'], project['assets'], project['shots']) != (raw_content, values['script'], assets, shots):
                project.update(raw_content=raw_content, script=values['script'], assets=assets, shots=shots)
                self._changed(project, 'planning')
                self._write(project)
            return project

    @staticmethod
    def _shot(project, shot_id):
        shot = next((row for row in project['shots'] if row['id'] == shot_id), None)
        if shot is None:
            raise WorkshopError('分镜不存在', 404)
        return shot

    def add_candidate(self, identifier, expected_revision, shot_id, relative, *, candidate_id=None, task_id=None, source_prompt=None, auto_select=True):
        with self._locked():
            project = self._read(identifier)
            shot = self._shot(project, shot_id)
            existing = next((row for row in shot['candidates'] if row['id'] == candidate_id), None)
            if existing:
                return project
            self._expect(project, expected_revision)
            source = self._media_path(relative)
            self._validate_video(source)
            snapshot = self._snapshot(identifier, source, 'candidates')
            snapshot.update(id=candidate_id or uuid.uuid4().hex, task_id=task_id, prompt=source_prompt if source_prompt is not None else shot['prompt'])
            shot['candidates'].append(snapshot)
            if auto_select and not shot['selected_candidate_id']:
                shot['selected_candidate_id'] = snapshot['id']
                self._changed(project)
            return self._write(project)

    def select_candidate(self, identifier, expected_revision, shot_id, candidate_id):
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            shot = self._shot(project, shot_id)
            candidate = next((row for row in shot['candidates'] if row['id'] == candidate_id), None)
            if candidate is None:
                raise WorkshopError('镜头候选不存在', 404)
            if _digest(self._media_path(candidate['path'])) != candidate['sha256']:
                raise WorkshopError('镜头候选内容已变化，不能采用', 409)
            if shot['selected_candidate_id'] != candidate_id:
                shot['selected_candidate_id'] = candidate_id
                self._changed(project)
                self._write(project)
            return project

    def comment(self, identifier, expected_revision, values):
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            if values.get('shot_id'):
                self._shot(project, values['shot_id'])
            project['comments'].append({'id': uuid.uuid4().hex, 'revision': project['revision'], **values, 'created_at': _now()})
            if values.get('shot_id'):
                project['stage'] = 'changes_requested'
                latest = project['deliverables'][-1] if project['deliverables'] else None
                if latest and latest['revision'] == project['revision']:
                    project['reviews'].append({'id': uuid.uuid4().hex, 'revision': project['revision'],
                        'artifact_id': latest['id'], 'artifact_sha256': latest['sha256'],
                        'decision': 'changes_requested', 'kind': values['kind'], 'notes': values['notes'], 'created_at': _now()})
            return self._write(project)

    def archive(self, identifier, expected_revision, archived):
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            project['archived_at'] = _now() if archived else None
            return self._write(project)

    def _deliver_locked(self, project, relative, render_id=None):
        source = self._media_path(relative)
        self._validate_video(source)
        snapshot = self._snapshot(project['id'], source, 'deliveries')
        self._validate_video(self._media_path(snapshot['path']))
        self._changed(project, 'rendered')
        artifact = {'id': uuid.uuid4().hex, **snapshot, 'revision': project['revision']}
        if render_id:
            artifact['render_id'] = render_id
        project['deliverables'].append(artifact)
        return artifact

    def deliver(self, identifier, expected_revision, relative):
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            self._deliver_locked(project, relative)
            return self._write(project)

    def _reviewable_artifact(self, project, artifact_id):
        artifact = next((item for item in project['deliverables'] if item['id'] == artifact_id), None)
        if artifact is None:
            raise WorkshopError('交付版本不存在', 404)
        if artifact['revision'] != project['revision']:
            raise WorkshopError('该成片不是当前内容版本，请重新交付后审核', 409)
        try:
            valid = _digest(self._media_path(artifact['path'])) == artifact['sha256']
        except (OSError, WorkshopError):
            valid = False
        if not valid:
            raise WorkshopError('交付文件内容已变化，不能沿用旧版本审核', 409)
        return artifact

    def review(self, identifier, expected_revision, artifact_id, values):
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            artifact = self._reviewable_artifact(project, artifact_id)
            if not any(row.get('artifact_id') == artifact_id and row.get('revision') == expected_revision
                       and all(row.get(key) == value for key, value in values.items()) for row in project['reviews'][-1:]):
                project['reviews'].append({'id': uuid.uuid4().hex, 'revision': project['revision'],
                    'artifact_id': artifact_id, 'artifact_sha256': artifact['sha256'], **values, 'created_at': _now()})
            project['stage'] = ('changes_requested' if values['decision'] == 'changes_requested' else
                                'approved' if values['kind'] == 'human' else 'simulated_reviewed')
            return self._write(project)

    def publication(self, identifier, expected_revision, artifact_id, values):
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            artifact = self._reviewable_artifact(project, artifact_id)
            review = next((row for row in reversed(project['reviews']) if row['artifact_id'] == artifact_id
                           and row['revision'] == expected_revision and row['artifact_sha256'] == artifact['sha256']), None)
            if not review or review['kind'] != 'human' or review['decision'] != 'approved':
                raise WorkshopError('当前版本尚未通过人工审核', 409)
            project['publications'].append({'id': uuid.uuid4().hex, 'revision': expected_revision,
                'artifact_id': artifact_id, 'artifact_sha256': artifact['sha256'], **values, 'created_at': _now()})
            return self._write(project)

    @staticmethod
    def _submission_confirmed(receipt):
        return receipt.get('submission_state') == 'confirmed'

    @staticmethod
    def _render_entry(project, render_id):
        receipt = next((row for row in project['renders'] if row['id'] == render_id), None)
        if receipt is None:
            raise WorkshopError('合成任务不存在', 404)
        return receipt

    def start_render(self, identifier, expected_revision):
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            existing = next((row for row in reversed(project['renders']) if row.get('backend') == 'native'
                and row['content_revision'] == expected_revision and row['status'] in {'queued', 'running', 'succeeded'}), None)
            if existing:
                return existing
            selected = []
            for shot in project['shots']:
                candidate = next((item for item in shot['candidates'] if item['id'] == shot['selected_candidate_id']), None)
                if candidate is None:
                    raise WorkshopError('请先为每个分镜选择已完成的视频', 422)
                if _digest(self._media_path(candidate['path'])) != candidate['sha256']:
                    raise WorkshopError('镜头素材已变化，请重新选择', 409)
                selected.append({**candidate, 'shot_id': shot['id']})
            if not selected:
                raise WorkshopError('尚无可合成的分镜视频', 422)
            render_id = uuid.uuid4().hex
            receipt = {'id': render_id, 'task_id': render_id, 'backend': 'native',
                'operation_id': 'native-render-' + render_id, 'content_revision': expected_revision,
                'submission_state': 'confirmed', 'status': 'queued', 'progress': 0, 'progress_known': False,
                'created_at': _now(), 'updated_at': _now(), 'inputs': selected, 'aspect_ratio': project['aspect_ratio']}
            project['renders'].append(receipt)
            project['stage'] = 'editing'
            self._write(project)
        from easel.workshop_render import submit_render
        submit_render(self.outputs, identifier, render_id)
        return receipt

    def render_status(self, identifier, render_id):
        with self._locked():
            return self._render_entry(self._read(identifier), render_id)

    def collect_render(self, identifier, render_id, expected_revision):
        with self._locked():
            project = self._read(identifier)
            receipt = self._render_entry(project, render_id)
            if receipt.get('artifact_id'):
                return project
            self._expect(project, expected_revision)
            if receipt['content_revision'] != expected_revision:
                raise WorkshopError('合成任务不属于当前内容版本', 409)
            if receipt['status'] != 'succeeded' or not receipt.get('output_path'):
                raise WorkshopError('合成任务尚未成功', 409)
            artifact = self._deliver_locked(project, receipt['output_path'], render_id)
            receipt.update(artifact_id=artifact['id'], collected_revision=project['revision'], message='成片已交付')
            return self._write(project)

    def bind_publication(self, identifier, expected_revision, artifact_id, approval):
        with self._locked():
            project = self._read(identifier)
            self._expect(project, expected_revision)
            artifact = self._reviewable_artifact(project, artifact_id)
            media = approval['subject']['media']
            if len(media) != 1 or media[0]['sha256'] != artifact['sha256']:
                raise WorkshopError('发布媒体与审核成片不一致', 409)
            project['publication_preparation'] = {
                'approval_id': approval['id'], 'payload_hash': approval['payloadHash'],
                'actor_id': approval['actorId'], 'revision': expected_revision,
                'artifact_id': artifact_id, 'artifact_sha256': artifact['sha256'],
                'subject': approval['subject'], 'state': approval['state']}
            return self._write(project)

    def publication_result(self, identifier, binding, record):
        with self._locked():
            project = self._read(identifier)
            if (binding['approval_id'] != record['id'] or binding['payload_hash'] != record['payloadHash']
                    or binding['actor_id'] != record['actorId']):
                raise WorkshopError('发布回执不属于已审核版本', 409)
            run = {**binding, 'operation_id': record.get('operationId'), 'state': record['state'],
                   'result': record.get('result')}
            index = next((i for i, row in enumerate(project['publication_runs'])
                          if row['approval_id'] == record['id']), None)
            if index is not None and project['publication_runs'][index] == run:
                return project
            if index is None:
                project['publication_runs'].append(run)
            else:
                project['publication_runs'][index] = run
            if project['revision'] == binding['revision'] and (record.get('result') or {}).get('status'):
                project['stage'] = 'publishing'
            result = record.get('result') or {}
            url = result.get('url')
            if (result.get('status') == 'confirmed' and isinstance(url, str) and url.startswith(('https://', 'http://'))
                    and not any(row.get('approval_id') == record['id'] for row in project['publications'])):
                project['publications'].append({'id': uuid.uuid4().hex, 'approval_id': record['id'],
                    'revision': binding['revision'], 'artifact_id': binding['artifact_id'],
                    'artifact_sha256': binding['artifact_sha256'], 'platform': record['subject']['platform']['id'],
                    'url': url, 'published_at': _now(), 'created_at': _now()})
            return self._write(project)
