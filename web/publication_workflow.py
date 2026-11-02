"""Authenticated publication subject/approval adapter for Easel's existing runners.

The subject -> explicit decision -> exact request lifecycle follows DeepSeek
Harness ui-approval/tool-approval at ce7073d (MIT). Durable operation intent and
unknown-result handling reuse Easel interaction_center; media uses D08 snapshots.
This host adapter is not a verbatim upstream Python implementation.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from easel.projects import _registry, project_id
from easel.request_identity import current_user
from easel.publication_approvals import PublicationApprovalStore, PublicationApprovalError
from easel.publication_media import capture_publication_media, stage_publication_media
from easel.publication_article import prepare_publication_article

_TASKS: set[asyncio.Task] = set()


def _error(error):
    return HTTPException(getattr(error, 'status_code', 400), str(error))


def _media_path(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise HTTPException(400, '媒体路径格式无效')
    value = value.removeprefix('outputs/')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') or part.startswith(('.', '_')) for part in value.split('/')):
        raise HTTPException(400, '媒体须来自当前 IP 的公开内容目录')
    return path.as_posix()


class PublicationWorkflow:
    def __init__(self, host):
        self.host = host

    def scope(self):
        self.host._require_operation()
        actor = current_user()
        if not actor:
            raise HTTPException(401, '请先登录')
        return {'actor_id': actor['id'], 'project_id': project_id()}

    def store(self):
        return PublicationApprovalStore(self.host._publication_dir() / 'approvals')

    def call(self, method, *args, **kwargs):
        try:
            return getattr(self.store(), method)(*args, **kwargs, **self.scope())
        except PublicationApprovalError as error:
            raise _error(error) from None

    def configuration(self, platform):
        config = self.host.LOGIN_RUNNERS.get(platform)
        if not config:
            raise HTTPException(404, '未知平台')
        if config['backend'] == 'unsupported':
            raise HTTPException(400, '该平台暂不支持发布')
        if config['backend'] == 'social-browser':
            raise HTTPException(400, '该平台当前仅支持账号登录，尚未接入发布')
        return config

    def fields(self, platform, data):
        title, body = data.get('title', ''), data.get('body', '')
        tags, persona = data.get('tags', ''), data.get('persona', '').strip()
        if not title.strip() and not body.strip():
            raise HTTPException(400, '标题/正文不能为空')
        if persona and (not self.host.valid_persona_name(persona) or not self.host.profile_exists(persona)):
            raise HTTPException(400, '选择的画像不存在或名称非法')
        for value in (title, body, tags, persona):
            if self.host._scrub_secrets(value) != value:
                raise HTTPException(400, '内容包含本机敏感配置，请移除后再准备发布')
        title = title.strip() or body.strip()[:20]
        if platform == 'bilibili':
            title, body = title[:80], body[:2000]
            tags = tags.replace('#', '').replace('，', ',').strip().strip(',') or '日常'
        return {'title': title, 'body': body, 'tags': tags, 'persona': persona}

    def validate_media(self, platform, media):
        images = [item for item in media if item['mime'].startswith('image/')]
        videos = [item for item in media if item['mime'].startswith('video/')]
        if (platform in self.host.MEDIA_REQUIRED or platform == 'wechat-oa') and not media:
            raise HTTPException(400, '此平台需要图片或视频')
        if images and videos:
            raise HTTPException(400, '同一条内容不能同时发图片和视频')
        if platform in self.host.VIDEO_ONLY_PUBLISH and (len(videos) != 1 or images):
            raise HTTPException(400, '此平台需要一个视频')
        if platform == 'wechat-oa' and (not images or videos):
            raise HTTPException(400, '公众号文章需要封面图片，不能使用视频')
        if platform not in {'xiaohongshu', 'douyin', 'wechat-oa'} and len(media) > 1:
            raise HTTPException(400, '此平台当前发布通道只支持单个媒体，不能静默丢弃已选文件')

    async def prepare(self, platform, data, *, idempotency_key=''):
        self.scope()
        request_hash = hashlib.sha256(json.dumps({'platform': platform, 'input': data},
            sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
        if idempotency_key:
            existing = self.call('lookup_call', idempotency_key, request_hash)
            if existing is not None:
                return existing
        self.host._require_automated_publishing()
        config = self.configuration(platform)
        fields = self.fields(platform, data)
        account = await self.host._publication_account(platform)
        self.scope()
        preparation = uuid.uuid4().hex
        try:
            media = await asyncio.to_thread(capture_publication_media, self.host._chat_artifact_store(),
                paths=data.get('media') or [], artifact_ids=data.get('artifactIds') or [],
                session_id='publication-preparations', turn_id=preparation, call_id=preparation)
        except ValueError as error:
            raise _error(error) from None
        self.scope()
        self.validate_media(platform, media)
        project = next((item for item in _registry(self.host.PROJECT_ROOT) if item['id'] == project_id()), None)
        if not project:
            raise HTTPException(404, '当前 IP 不存在')
        subject = {'schemaVersion': 1, 'project': {'id': project_id(), 'name': project['name']},
            'platform': {'id': platform, 'name': config['name']}, 'account': account,
            'mode': 'create_draft' if platform == 'wechat-oa' else 'publish_now',
            'scheduledAt': None, 'audience': 'platform_draft' if platform == 'wechat-oa' else 'public',
            **fields, 'media': media}
        if platform == 'wechat-oa':
            try:
                subject['article'] = await asyncio.to_thread(prepare_publication_article,
                    fields['title'], fields['body'], media, self.host._outputs_dir(),
                    self.host._publication_article_converter)
            except ValueError as error:
                raise _error(error) from None
            self.scope()
        call = {'idempotency_key': idempotency_key, 'request_hash': request_hash} if idempotency_key else {}
        return self.call('create', subject, **call)

    def get(self, identifier):
        record = self.call('get', identifier)
        if record.get('operationId') and (record.get('result') or {}).get('status') == 'executing':
            status = self.status_path(record)
            try:
                import json
                data = json.loads(status.read_text())
            except (OSError, ValueError):
                data = {}
            if data.get('state') in ('starting', 'sms_required', 'verifying'):
                record['runnerStatus'] = {key: data.get(key, '') for key in ('state', 'message')}
        return record

    def decide(self, identifier, approved, payload_hash):
        return self.call('decide', identifier, approved=approved, payload_hash=payload_hash)

    def operation_dir(self, record):
        return self.host._publication_dir() / 'operations' / record['actorId'] / record['operationId']

    def status_path(self, record):
        return self.operation_dir(record) / 'runner.json'

    async def start(self, platform, data):
        self.scope()
        self.host._require_automated_publishing()
        self.configuration(platform)
        if data.get('artifactIds') and data.get('media'):
            raise HTTPException(400, '媒体路径与固定版本 ID 只能选择一种，不能混用')
        identifier, payload_hash = data.get('approvalId'), data.get('payloadHash')
        if not identifier or not payload_hash:
            raise HTTPException(428, '请先核对具体账号和内容版本，并批准本次发布')
        record = self.call('get', identifier)
        subject = record['subject']
        if subject['platform']['id'] != platform or record['payloadHash'] != payload_hash:
            raise HTTPException(409, '批准对象不匹配，请重新审核')
        current = copy.deepcopy(subject)
        current.update(self.fields(platform, data))
        requested_ids = data.get('artifactIds') or []
        media_matches = True
        if requested_ids:
            if requested_ids != [item['artifactId'] for item in subject['media']]:
                current['media'] = [{'requestedArtifactId': value} for value in requested_ids]
                media_matches = False
        elif [_media_path(path) for path in data.get('media') or []] != [item['path'] for item in subject['media']]:
            current['media'] = [{'requestedPath': value} for value in data.get('media') or []]
            media_matches = False
        # Terminal/in-flight replays never touch the platform or restart a runner.
        # The store still verifies exact request content before recognizing replay.
        if record.get('operationId'):
            claimed = self.call('claim', identifier, current_subject=current)
            return claimed
        current['account'] = await self.host._publication_account(platform)
        self.scope()
        try:
            if subject['media'] and media_matches:
                current['media'] = await asyncio.to_thread(capture_publication_media, self.host._chat_artifact_store(),
                    artifact_ids=[item['artifactId'] for item in subject['media']], paths=[],
                    session_id='publication-preparations', turn_id=identifier, call_id=identifier)
        except ValueError as error:
            raise _error(error) from None
        self.scope()
        claimed = self.call('claim', identifier, current_subject=current)
        if not claimed.get('claimed'):
            return claimed
        task = asyncio.create_task(self._execute(claimed))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return claimed

    async def _execute(self, record):
        identifier, operation = record['id'], record['operationId']
        subject = record['subject']
        dispatched = False
        try:
            cancelled = threading.Event()
            stage = asyncio.create_task(asyncio.to_thread(stage_publication_media, self.host._chat_artifact_store(),
                subject['media'], self.operation_dir(record) / 'media', cancelled=cancelled.is_set))
            try:
                media = await asyncio.shield(stage)
            except asyncio.CancelledError:
                cancelled.set()
                while not stage.done():
                    try:
                        await asyncio.shield(stage)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if stage.done():
                    try:
                        late = stage.result()
                        if hasattr(late, 'cleanup'):
                            late.cleanup()
                    except BaseException:
                        pass
                raise
            self.scope()
            self.host._require_automated_publishing()
            # A fresh, independent account probe immediately before dispatch.
            account = await self.host._publication_account(subject['platform']['id'])
            self.scope()
            if account != subject['account']:
                raise HTTPException(409, '登录账号已变化，未执行旧批准')
            request = self.host.PublishRequest(**{key: subject[key] for key in ('title', 'body', 'tags', 'persona')})
            dispatched = True
            result = await self.host._dispatch_publish(subject['platform']['id'], request,
                media_paths=media, operation_id=operation, expected_account=subject['account'],
                approved_article=subject.get('article'), approved_media=subject['media'])
            if not isinstance(result, dict) or result.get('status') not in (
                    'submitted', 'confirmed', 'unknown', 'failed', 'draft_created', 'exported', 'scheduled'):
                result = {'status': 'unknown', 'message': '发布进程结果尚未核实，请核对平台，勿重复提交'}
        except asyncio.CancelledError:
            result = ({'status': 'unknown', 'message': '执行进程中断，结果待核实；未自动重发'} if dispatched
                else {'status': 'failed', 'message': '已停止，未启动平台提交', 'notStarted': True})
        except HTTPException as error:
            result = ({'status': 'unknown', 'message': '执行结果待核实，请核对平台，勿重复提交'} if dispatched
                else {'status': 'failed', 'message': str(error.detail), 'notStarted': True})
        except Exception:
            result = {'status': 'unknown', 'message': '未取得可核对的发布回执，请核对平台，勿重复提交'}
        # A revoked user cannot execute new work, but the host must still preserve
        # this already-owned operation's terminal receipt under its original scope.
        try:
            self.store().finish(identifier, operation_id=operation, result=result,
                actor_id=record['actorId'], project_id=record['projectId'])
        except PublicationApprovalError:
            pass

    def sms(self, platform, identifier, code):
        self.scope()
        self.host._require_automated_publishing()
        if not identifier:
            raise HTTPException(428, '验证码必须关联具体已批准操作')
        record = self.get(identifier)
        if (record['subject']['platform']['id'] != platform
                or (record.get('result') or {}).get('status') != 'executing'
                or (record.get('runnerStatus') or {}).get('state') != 'sms_required'):
            raise HTTPException(409, '该操作当前不等待短信验证')
        if not re.fullmatch(r'[0-9]{4,8}', code):
            raise HTTPException(400, '验证码应为4至8位数字')
        path = self.operation_dir(record) / 'runner.code'
        # This private directory was created by the verified media staging step.
        if path.parent.resolve() != path.parent.absolute() or path.is_symlink():
            raise HTTPException(409, '执行目录发生变化')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if hasattr(os, 'fchmod'):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                descriptor = None
                stream.write(code)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return {'ok': True, 'operationId': record['operationId']}
