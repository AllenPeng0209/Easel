"""D25 read-only domain DTO adapter; never creates a parallel business store.

Ideas reuse the IP idea catalog, draft files reuse D08's private immutable
deliveries, and workshop projects reuse WorkshopStore. Query snapshot hashes
identify observations, not content revisions or authority to publish.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from easel.chat_artifacts import ArtifactError
from easel.projects import project_id
from easel.request_identity import current_user
from easel.video_workshop import WorkshopError, WorkshopStore, public_render_submission_attempts


class IdeaProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    platform: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    url: str = Field(default="", max_length=4000)
    collectedAt: int | None = Field(default=None, gt=0)

    @field_validator("url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        if not value:
            return value
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
            raise ValueError("来源链接格式无效")
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None:
            raise ValueError("来源链接必须是无凭据的 HTTP 或 HTTPS 地址")
        # Accessing port also validates malformed and out-of-range ports.
        _ = parts.port
        return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def _text(value, fallback=''):
    return value if isinstance(value, str) else fallback


def _scalars(value, keys):
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if key in keys
            and (item is None or type(item) in (str, int, bool) or type(item) is float and math.isfinite(item))}


def _status(code, label, source='computed'):
    return {'code': code, 'label': label, 'source': source}


def _identifier(value, kind):
    pattern = r'[A-Za-z0-9_-]{1,160}' if kind == 'idea' else r'[a-f0-9]{32}'
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _text_snapshot(value, scope):
    mime = _text(value.get('mime')).lower().split(';')[0]
    return (value.get('storageMode') == 'snapshot' and value.get('projectId') == scope['projectId']
            and (mime.startswith('text/') or mime in {'application/json', 'application/xml', 'application/xhtml+xml'}))


class BusinessObjectService:
    def __init__(self, host):
        self.host = host

    def _scope(self):
        self.host._require_operation()
        actor = current_user()
        if not isinstance(actor, dict) or not isinstance(actor.get('id'), str) or not actor['id']:
            raise HTTPException(401, '请先登录')
        return {'actorId': actor['id'], 'projectId': project_id()}

    def _request(self, payload, scope):
        if not isinstance(payload, dict):
            raise HTTPException(400, '业务查询参数无效')
        action, kind = payload.get('action'), payload.get('kind')
        if (not isinstance(action, str) or action not in {'list', 'inspect'}
                or not isinstance(kind, str) or kind not in {'idea', 'draft', 'workshop'}):
            raise HTTPException(400, '仅支持选题、草稿文件和工坊项目的只读查询')
        allowed = {'action', 'kind', 'id'} if action == 'inspect' else {'action', 'kind', 'cursor', 'query'}
        if set(payload) - allowed or action == 'inspect' and not _identifier(payload.get('id'), kind):
            raise HTTPException(400, '业务查询字段或对象编号无效')
        query, cursor = payload.get('query', ''), payload.get('cursor', '')
        if any(not isinstance(value, str) or len(value) > maximum
               or any(ord(char) < 32 or ord(char) == 127 for char in value)
               for value, maximum in ((query, 500), (cursor, 2048))):
            raise HTTPException(400, '查询文字或分页游标无效')
        query = query.strip().casefold()
        binding = {'v': 1, **scope, 'kind': kind, 'queryHash': _digest(query)}
        after = ''
        if cursor:
            try:
                if not re.fullmatch(r'[A-Za-z0-9_-]+', cursor):
                    raise ValueError
                decoded = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                if (not isinstance(decoded, dict) or set(decoded) != {*binding, 'after'}
                        or any(type(decoded.get(key)) is not type(value) or decoded[key] != value
                               for key, value in binding.items())
                        or not _identifier(decoded.get('after'), kind)):
                    raise ValueError
                after = decoded['after']
            except (ValueError, TypeError, UnicodeError):
                raise HTTPException(400, '分页游标不属于当前用户、IP 或查询') from None
        return action, kind, query, after, binding

    @staticmethod
    def _object(scope, kind, identifier, title, summary, status, version, details):
        target, label = {'idea': ('ideas', '查看选题'), 'draft': ('artifact', '查看草稿文件'),
                         'workshop': ('workshop', '查看工坊项目')}[kind]
        value = {'schemaVersion': 1, **scope, 'kind': kind, 'id': identifier, 'title': title,
                 'summary': summary, 'status': status, 'version': version, 'details': details,
                 'actions': [{'kind': 'open', 'target': target, 'entityId': identifier, 'label': label}]}
        return {**value, 'snapshotHash': _digest(value), 'observedAt': int(time.time() * 1000)}

    def _idea(self, value, scope):
        statuses = {'pending': _status('idea_pending', '待处理选题', 'stored'),
                    'doing': _status('idea_doing', '选题进行中', 'stored'),
                    'done': _status('idea_done', '选题已完成', 'stored')}
        stored = _scalars(value, {'id', 'title', 'note', 'source', 'status', 'created'})
        if value.get('provenance') is not None:
            try:
                provenance = IdeaProvenance.model_validate(value['provenance'])
            except ValidationError:
                pass  # Old or damaged provenance must not hide the idea.
            else:
                stored.update(provenance.model_dump(exclude_none=True))
        state = statuses.get(_text(stored.get('status')), _status('unknown', '选题状态待核对', 'unknown'))
        return self._object(scope, 'idea', stored['id'], _text(stored.get('title'), '未命名选题'),
            _text(stored.get('note')), state, {'revision': None, 'sha256': None}, {'idea': stored})

    def _draft(self, value, store, scope):
        metadata = _scalars(value, {'artifactId', 'projectId', 'sessionId', 'turnId', 'path', 'workspacePath',
            'name', 'mime', 'size', 'description', 'status', 'createdAt', 'storageMode', 'revision', 'sha256',
            'bundleSha256', 'reviewStatus'})
        identifier = metadata['artifactId']
        path = metadata.get('path')
        if (not isinstance(path, str) or not path or '\\' in path
                or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', path)
                or PurePosixPath(path).is_absolute()
                or any(part in {'', '.', '..'} or part.startswith('.') for part in path.split('/'))
                or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise HTTPException(409, '草稿快照路径记录无效，未回退当前文件')
        metadata['workspacePath'] = 'outputs/' + path
        metadata['name'] = PurePosixPath(path).name
        metadata['url'] = f'/api/chat/artifacts/{identifier}/content'
        available = True
        try:
            store.verify_available(identifier)
        except (ArtifactError, OSError):
            available = False
        metadata['status'] = 'available' if available else 'unavailable'
        state = _status('draft_file', '草稿文件（固定版本）') if available else _status('draft_unavailable', '草稿文件暂不可用')
        return self._object(scope, 'draft', identifier, _text(metadata.get('name'), '草稿文件'),
            _text(metadata.get('description')), state,
            {'revision': metadata.get('revision'), 'sha256': metadata.get('sha256')}, {'artifact': metadata})

    def _workshop(self, project, store, scope):
        safe = _scalars(project, {'id', 'title', 'brief', 'stage', 'revision', 'aspect_ratio',
                                  'created_at', 'updated_at', 'archived_at'})
        nested = {
            'deliverables': {'id', 'sha256', 'revision', 'engine_revision', 'render_id', 'created_at'},
            'reviews': {'id', 'revision', 'artifact_id', 'artifact_sha256', 'kind', 'decision', 'notes',
                        'created_at', 'actor_id', 'actorId', 'reviewer_id'},
            'renders': {'id', 'operation_id', 'task_id', 'status', 'content_revision', 'engine_revision',
                        'submission_state', 'progress', 'artifact_id', 'collected_revision',
                        'created_at', 'updated_at', 'collected_at'},
            'publications': {'id', 'revision', 'artifact_id', 'artifact_sha256', 'platform', 'published_at', 'created_at'},
        }
        for key, fields in nested.items():
            values = project.get(key, [])
            if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
                raise HTTPException(503, '工坊项目的嵌套记录损坏，请保留源记录核对')
            safe[key] = [_scalars(value, fields) for value in values]
            if key == 'renders':
                for source, projected in zip(values, safe[key]):
                    attempts = public_render_submission_attempts(source)
                    if attempts is not None:
                        projected['submission_attempts'] = attempts
        details = {'project': safe, 'engineVerification': 'not_checked', 'publishEligibility': 'unverified',
                   'deliveryVerification': 'not_available', 'publicationEvidence': 'stored_records_only'}
        try:
            store._assert_canvas_binding(project)
            details['canvasBinding'] = 'verified' if project.get('beeftv_canvas_id') else 'not_linked'
        except WorkshopError:
            details['canvasBinding'] = 'unverified'
        state = _status('unknown', '工坊状态待核对', 'unknown')
        latest = safe['deliverables'][-1] if safe['deliverables'] else None
        sha = latest.get('sha256') if latest else None
        if not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha):
            sha = None
        if latest:
            if latest.get('revision') != project['revision']:
                details['deliveryVerification'] = 'stale_revision'
                state = _status('stale_delivery', '成片属于历史工程版本')
            else:
                try:
                    store._reviewable_artifact(project, latest.get('id'))
                    details['deliveryVerification'] = 'current_file_verified'
                    state = _status('rendered', '成片已收取，待审阅')
                    review = next((value for value in reversed(safe['reviews'])
                        if value.get('revision') == project['revision'] and value.get('artifact_id') == latest.get('id')
                        and value.get('artifact_sha256') == sha), None)
                    if review and review.get('decision') == 'changes_requested':
                        state = _status('changes_requested', '已记录修改意见', 'stored')
                    elif review and review.get('decision') == 'approved':
                        if review.get('kind') == 'human':
                            state = _status('human_review_recorded', '已记录人工审阅，当前发布条件待核对', 'stored')
                        elif review.get('kind') == 'simulated':
                            state = _status('simulated_reviewed', '已记录模拟预审', 'stored')
                except (WorkshopError, OSError, KeyError, TypeError):
                    details['deliveryVerification'] = 'unavailable_or_changed'
                    state = _status('delivery_unavailable', '成片缺失或文件摘要已变化')
        elif safe['renders']:
            receipt = safe['renders'][-1]
            if receipt.get('status') not in {'queued', 'running', 'succeeded', 'failed', 'cancelled'}:
                state = _status('unknown', '渲染状态待核对', 'unknown')
            elif not store._submission_confirmed(receipt):
                state = _status('render_submission_unconfirmed', '渲染提交结果待核对')
            elif receipt.get('status') == 'succeeded' and not receipt.get('artifact_id'):
                state = _status('render_uncollected', '渲染成功，尚未收取成片')
            elif receipt.get('status') in {'queued', 'running', 'failed', 'cancelled'}:
                code = receipt['status']
                label = {'queued': '渲染已排队', 'running': '渲染进行中', 'failed': '渲染失败', 'cancelled': '渲染已取消'}[code]
                state = _status('render_' + code, label, 'stored')
        elif safe.get('stage') in {'draft', 'editing'}:
            state = _status('workshop_' + safe['stage'], '工坊草案' if safe['stage'] == 'draft' else '工程编辑中', 'stored')
        if safe.get('archived_at'):
            state = _status('archived', '工坊项目已归档', 'stored')
        return self._object(scope, 'workshop', project['id'], _text(safe.get('title'), '未命名工坊项目'),
            _text(safe.get('brief')), state, {'revision': project['revision'], 'sha256': sha}, details)

    @staticmethod
    def _matches(value, query):
        return not query or query in ' '.join(_text(value.get(key)) for key in
            ('title', 'note', 'brief', 'name', 'description', 'path')).casefold()

    def query(self, payload):
        scope = self._scope()
        action, kind, query, after, binding = self._request(payload, scope)
        try:
            if kind == 'idea':
                source = [value for value in self.host._read_ideas()
                          if isinstance(value, dict) and _identifier(value.get('id'), kind)]
                transform, identifier = lambda value: self._idea(value, scope), 'id'
            elif kind == 'workshop':
                store = WorkshopStore(self.host._outputs_dir(), strict_canvas_bindings=True)
                source = [store.get(payload['id'])] if action == 'inspect' else store.list()
                transform, identifier = lambda value: self._workshop(value, store, scope), 'id'
            else:
                store = self.host._chat_artifact_store()
                transform, identifier = lambda value: self._draft(value, store, scope), 'artifactId'
                if action == 'inspect':
                    metadata = store.get(payload['id'])
                    source = [metadata] if metadata and _text_snapshot(metadata, scope) else []
                else:
                    source, position = [], after
                    while True:
                        page = store.list_metadata(after_id=position, limit=64)
                        source.extend(value for value in page if _text_snapshot(value, scope) and self._matches(value, query))
                        if len(source) > 20 or len(page) < 64:
                            break
                        position = page[-1]['artifactId']
            if action == 'inspect':
                source = [value for value in source if value.get(identifier) == payload['id']]
                if not source:
                    raise HTTPException(404, '业务对象不存在或没有访问权限')
                page, next_cursor = source[:1], None
            else:
                source = sorted((value for value in source if value.get(identifier, '') > after
                                 and self._matches(value, query)), key=lambda value: value[identifier])
                page, next_cursor = source[:20], None
                if len(source) > 20:
                    raw = json.dumps({**binding, 'after': page[-1][identifier]}, sort_keys=True, separators=(',', ':')).encode()
                    next_cursor = base64.urlsafe_b64encode(raw).decode().rstrip('=')
            items = [transform(value) for value in page]
        except WorkshopError as error:
            raise HTTPException(error.status, str(error)) from None
        except ArtifactError as error:
            raise HTTPException(error.status_code, str(error)) from None
        fresh = self._scope()
        if fresh != scope:
            raise HTTPException(403, '查询期间用户或 IP 已变化，请重新查询')
        return {'schemaVersion': 1, **scope, 'kind': kind, 'items': items, 'nextCursor': next_cursor}
