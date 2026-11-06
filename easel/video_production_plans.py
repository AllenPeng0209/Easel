"""Frozen human-approved video scopes and one durable intent per planned clip.

This module never calls a model. The authenticated Host collects the manual
decision and claims an intent before making a paid request. An uncertain request
keeps its intent forever; there is deliberately no release/reset operation.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import time
import uuid


class VideoProductionPlanError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _text(value, maximum, label):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any((ord(char) < 32 and char not in '\n\t') or 127 <= ord(char) < 160 for char in value)):
        raise VideoProductionPlanError(f'{label}无效')
    return value


def _token(value, pattern, label):
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise VideoProductionPlanError(f'{label}无效')
    return value


def _reference(raw):
    fields = {'kind', 'path', 'sha256', 'bytes', 'mimeType', 'durationMs', 'width', 'height', 'hasAudio'}
    if not isinstance(raw, dict) or set(raw) - fields or raw.get('kind') not in {'image', 'video', 'audio'}:
        raise VideoProductionPlanError('参考素材描述无效')
    path = _text(raw.get('path'), 1024, '参考素材路径')
    relative = PurePosixPath(path)
    if ('\\' in path or ':' in path or relative.is_absolute() or str(relative) != path
            or any(part in {'.', '..'} or part.startswith('.') for part in relative.parts)):
        raise VideoProductionPlanError('参考素材须为当前 IP 内容目录内的相对路径')
    _token(raw.get('sha256'), r'[a-f0-9]{64}', '参考素材摘要')
    limit = {'image': 10, 'video': 50, 'audio': 15}[raw['kind']] * 1024 * 1024
    if type(raw.get('bytes')) is not int or not 0 < raw['bytes'] <= limit:
        raise VideoProductionPlanError('参考素材大小无效或超过允许范围')
    if 'mimeType' in raw and (not isinstance(raw['mimeType'], str)
            or not re.fullmatch(raw['kind'] + r'/[A-Za-z0-9.+-]{1,100}', raw['mimeType'])):
        raise VideoProductionPlanError('参考素材格式无效')
    for name in ('width', 'height'):
        if name in raw and (type(raw[name]) is not int or not 0 < raw[name] <= 32768):
            raise VideoProductionPlanError('参考素材尺寸无效')
    if 'durationMs' in raw and (type(raw['durationMs']) is not int or not 0 < raw['durationMs'] <= 15000):
        raise VideoProductionPlanError('参考素材时长无效')
    if 'hasAudio' in raw and type(raw['hasAudio']) is not bool:
        raise VideoProductionPlanError('参考素材音轨描述无效')
    return dict(raw)


def normalize_production_plan(raw):
    """Validate the bounded, already media-verified Host representation."""
    if not isinstance(raw, dict) or set(raw) - {'version', 'title', 'modelId', 'aspectRatio', 'resolution', 'clips'}:
        raise VideoProductionPlanError('制作计划字段无效')
    if type(raw.get('version', 1)) is not int or raw.get('version', 1) != 1:
        raise VideoProductionPlanError('制作计划版本无效')
    title = _text(raw.get('title'), 200, '制作标题')
    model = _token(raw.get('modelId'), r'[A-Za-z0-9:_-]{1,200}', '制作模型')
    if raw.get('aspectRatio') not in {'9:16', '16:9', '1:1', '4:3', '3:4', '21:9'}:
        raise VideoProductionPlanError('制作画幅无效')
    _token(raw.get('resolution'), r'(?:480|720|1080|1440|2160)p', '交付规格')
    clips = raw.get('clips')
    if not isinstance(clips, list) or not 1 <= len(clips) <= 8:
        raise VideoProductionPlanError('单计划必须有 1 至 8 个明确片段')
    normalized, ids = [], set()
    for clip in clips:
        if not isinstance(clip, dict) or set(clip) - {'clipId', 'shotId', 'seconds', 'prompt', 'references'}:
            raise VideoProductionPlanError('制作片段字段无效')
        identifier = _token(clip.get('clipId'), r'[A-Za-z0-9_-]{1,80}', '固定片段编号')
        if identifier in ids:
            raise VideoProductionPlanError('制作计划片段编号不能重复')
        ids.add(identifier)
        seconds = clip.get('seconds')
        if type(seconds) is not int or not 1 <= seconds <= 15:
            raise VideoProductionPlanError('每个计划片段须为 1 至 15 整数秒')
        references = clip.get('references', [])
        if not isinstance(references, list) or len(references) > 12:
            raise VideoProductionPlanError('参考素材数量无效')
        refs = [_reference(item) for item in references]
        normalized.append({'clipId': identifier, 'seconds': seconds,
            'prompt': _text(clip.get('prompt'), 12000, '制作提示词'), 'references': refs,
            **({'shotId': _token(clip['shotId'], r'[A-Za-z0-9_-]{1,80}', '原分镜编号')} if 'shotId' in clip else {})})
    if sum(item['seconds'] for item in normalized) > 120:
        raise VideoProductionPlanError('单制作计划总请求时长不得超过 120 秒')
    value = {'version': 1, 'title': title, 'modelId': model, 'aspectRatio': raw['aspectRatio'],
             'resolution': raw['resolution'], 'clips': normalized}
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 256 * 1024:
        raise VideoProductionPlanError('制作计划过大')
    return value


def _collected_claim(raw, *, timestamp=False):
    fields = {'clipId', 'operationId', 'taskId', 'artifactSha256'}
    if timestamp:
        fields.add('claimedAt')
    if not isinstance(raw, dict) or set(raw) != fields:
        raise VideoProductionPlanError('替换计划须由 Host 提供原片已收取证据', 409)
    _token(raw['clipId'], r'[A-Za-z0-9_-]{1,80}', '原固定片段编号')
    _text(raw['operationId'], 256, '原生成操作编号')
    _token(raw['taskId'], r'(?:video_)?[a-f0-9]{32}', '原生成任务编号')
    _token(raw['artifactSha256'], r'[a-f0-9]{64}', '原片摘要')
    if timestamp and (type(raw['claimedAt']) is not int or raw['claimedAt'] < 0):
        raise VideoProductionPlanError('原片提交时钟无效', 409)
    return dict(raw)


def _supersession(raw):
    fields = {'planId', 'scopeHash', 'collectedClaims', 'unsubmittedClipIds'}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise VideoProductionPlanError('原制作计划替换范围无效', 409)
    _token(raw['planId'], r'[a-f0-9]{32}', '原制作计划编号')
    _token(raw['scopeHash'], r'[a-f0-9]{64}', '原制作计划摘要')
    claims, remaining = raw['collectedClaims'], raw['unsubmittedClipIds']
    if (not isinstance(claims, list) or not isinstance(remaining, list)
            or not 1 <= len(claims) + len(remaining) <= 8):
        raise VideoProductionPlanError('原制作计划替换片段无效', 409)
    claims = [_collected_claim(claim, timestamp=True) for claim in claims]
    for field in ('clipId', 'operationId', 'taskId'):
        if len({claim[field] for claim in claims}) != len(claims):
            raise VideoProductionPlanError('原片已收取证据不能重复', 409)
    for clip in remaining:
        _token(clip, r'[A-Za-z0-9_-]{1,80}', '原未提交片段编号')
    ids = [claim['clipId'] for claim in claims] + remaining
    if len(set(ids)) != len(ids):
        raise VideoProductionPlanError('原制作计划替换片段不能重复', 409)
    return {**raw, 'collectedClaims': claims, 'unsubmittedClipIds': list(remaining)}


def _scope_fingerprint(actor_id, ip, session_id, workshop_id, value):
    scope = {'actorId': actor_id, 'projectId': ip, 'sessionId': session_id,
             'workshopId': workshop_id, 'plan': value}
    canonical = json.dumps(scope, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _reconfirmation_ttl(record):
    """Validate the append-only decision history and retain the original TTL."""
    history = record.get('reconfirmations', [])
    if not isinstance(history, list) or 'reconfirmations' in record and not history:
        raise ValueError('invalid reconfirmation history')
    original_expiry = history[0]['previousExpiresAt'] if history else record['expiresAt']
    ttl = original_expiry - record['createdAt']
    if type(original_expiry) is not int or not 1000 <= ttl <= 86400000:
        raise ValueError('invalid original approval lifetime')
    previous_expiry, previous_decision = original_expiry, None
    for index, item in enumerate(history):
        if (set(item) != {'scopeHash', 'previousExpiresAt', 'previousDecidedAt', 'decidedAt', 'decision', 'expiresAt'}
                or item['scopeHash'] != record['scopeHash'] or item['previousExpiresAt'] != previous_expiry
                or item['decision'] not in {'approved', 'declined'}
                or type(item['decidedAt']) is not int or item['decidedAt'] < previous_expiry
                or item['previousDecidedAt'] is not None and (type(item['previousDecidedAt']) is not int
                    or not record['createdAt'] <= item['previousDecidedAt'] < previous_expiry)
                or index and item['previousDecidedAt'] != previous_decision
                or type(item['expiresAt']) is not int
                or item['expiresAt'] != (item['decidedAt'] + ttl if item['decision'] == 'approved' else previous_expiry)
                or item['decision'] == 'declined' and index != len(history) - 1):
            raise ValueError('invalid reconfirmation decision')
        previous_expiry, previous_decision = item['expiresAt'], item['decidedAt']
    if history and (record['expiresAt'] != previous_expiry or record.get('decidedAt') != previous_decision
            or record['state'] not in ({'declined'} if history[-1]['decision'] == 'declined'
                                      else {'approved', 'expired', 'superseded'})):
        raise ValueError('reconfirmation outcome changed')
    return ttl


class VideoProductionPlanStore:
    def __init__(self, state_directory: Path, *, workspace: Path, actor_id: str, ip: str, session_id: str,
                 repository: Path, authorize=lambda: None, now_ms=None,
                 approval_ttl_ms: int = 900000):
        self.actor_id = _text(actor_id, 512, '认证用户')
        self.ip = _text(ip, 256, 'IP 标识')
        self.session_id = _text(session_id, 512, 'Host 会话')
        self.authorize = authorize
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        if type(approval_ttl_ms) is not int or not 1000 <= approval_ttl_ms <= 86400000:
            raise ValueError('制作批准有效期无效')
        self.approval_ttl_ms = approval_ttl_ms
        if repository is None:
            raise ValueError('制作批准必须提供可信 Host 仓库根目录')
        raw = Path(state_directory)
        self.directory = raw.resolve()
        writable = [Path(workspace).resolve(), Path(repository).resolve()]
        if raw.is_symlink() or any(self.directory.is_relative_to(root) or root.is_relative_to(self.directory)
                                   for root in writable):
            raise ValueError('制作批准与额度必须保存在 Native 可写工作区之外')
        self.authorize()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'video_production_plans.sqlite3'
        with self._database(write=True) as database:
            database.execute('''CREATE TABLE IF NOT EXISTS video_production_plans (
                id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, project_id TEXT NOT NULL,
                session_id TEXT NOT NULL, workshop_id TEXT NOT NULL, scope_hash TEXT NOT NULL,
                data TEXT NOT NULL, UNIQUE(actor_id,project_id,session_id,workshop_id,scope_hash))''')
            database.execute('''CREATE TABLE IF NOT EXISTS video_production_clip_intents (
                plan_id TEXT NOT NULL, clip_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
                claimed_at INTEGER NOT NULL, PRIMARY KEY(plan_id,clip_id))''')

    @contextmanager
    def _database(self, *, write=False):
        if (self.directory.is_symlink() or self.directory.resolve() != self.directory
                or self.path.is_symlink()):
            raise VideoProductionPlanError('Host 制作批准目录不可用', 503)
        database = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            os.chmod(self.path, 0o600)
            if write:
                database.execute('BEGIN IMMEDIATE')
            self.authorize()
            yield database
            if write:
                self.authorize()
                database.commit()
        except BaseException:
            if write:
                database.rollback()
            raise
        finally:
            database.close()

    def _load(self, database, plan_id, workshop_id=None):
        if not isinstance(plan_id, str) or re.fullmatch(r'[a-f0-9]{32}', plan_id) is None:
            raise VideoProductionPlanError('制作计划不存在', 404)
        row = database.execute('SELECT scope_hash,data,session_id,workshop_id FROM video_production_plans WHERE id=? '
            'AND actor_id=? AND project_id=? AND session_id=?',
            (plan_id, self.actor_id, self.ip, self.session_id)).fetchone()
        if row is None:
            raise VideoProductionPlanError('制作计划不存在', 404)
        record = self._decode_private_row(plan_id, row)
        if workshop_id is not None and record['workshopId'] != workshop_id:
            raise VideoProductionPlanError('制作计划不属于当前工坊', 404)
        return record

    def _decode_private_row(self, plan_id, row):
        # Both callers select only this authenticated actor/IP's private rows.
        # _load additionally selects the current session; the global admission
        # guard uses each row's stored session binding solely to verify its
        # fingerprint, never to grant access or return another chat's inputs.
        try:
            bound_session = _text(row[2], 512, 'Host 会话')
            bound_workshop = _token(row[3], r'[a-f0-9]{32}', '工坊编号')
            record = json.loads(row[1])
            value = normalize_production_plan({key: record[key] for key in
                ('version', 'title', 'modelId', 'aspectRatio', 'resolution', 'clips')})
            if 'supersession' in record:
                value['supersession'] = _supersession(record['supersession'])
                if value['supersession']['planId'] == plan_id:
                    raise ValueError
            if record['state'] == 'superseded':
                _token(record.get('supersededBy'), r'[a-f0-9]{32}', '后继制作计划编号')
                if (record['supersededBy'] == plan_id or type(record.get('supersededAt')) is not int
                        or record['supersededAt'] < 0):
                    raise ValueError
            elif 'supersededBy' in record or 'supersededAt' in record:
                raise ValueError
            if (record['planId'] != plan_id or record['actorId'] != self.actor_id
                    or record['projectId'] != self.ip or record['sessionId'] != bound_session
                    or record['workshopId'] != bound_workshop
                    or record['scopeHash'] != row[0]
                    or _scope_fingerprint(self.actor_id, self.ip, bound_session, bound_workshop, value) != row[0]
                    or record['maxGenerations'] != len(value['clips'])
                    or record['totalSeconds'] != sum(item['seconds'] for item in value['clips'])
                    or record['estimatedCost'] != 'unknown'
                    or type(record['createdAt']) is not int or record['createdAt'] < 0
                    or type(record['expiresAt']) is not int or record['expiresAt'] <= record['createdAt']
                    or record['state'] not in {'pending', 'approved', 'declined', 'expired', 'superseded'}):
                raise ValueError
            _reconfirmation_ttl(record)
        except (IndexError, KeyError, TypeError, ValueError):
            raise VideoProductionPlanError('Host 制作批准记录完整性未通过，未准入生成', 503) from None
        return record

    def _scope_hash(self, workshop_id, value):
        return _scope_fingerprint(self.actor_id, self.ip, self.session_id, workshop_id, value)

    @staticmethod
    def _save(database, record):
        database.execute('UPDATE video_production_plans SET data=? WHERE id=?',
            (json.dumps(record, ensure_ascii=False, allow_nan=False), record['planId']))

    def _record(self, database, record):
        rows = self._claims(database, record['planId'])
        if (record['state'] in {'pending', 'approved'} and not rows
                and self.now_ms() >= record['expiresAt']):
            record['state'] = 'expired'
            self._save(database, record)
        record['clipClaims'] = [{'clipId': row[0], 'operationId': row[1], 'claimedAt': row[2],
                                 'state': 'submission_intent'} for row in rows]
        return record

    @staticmethod
    def _claims(database, plan_id):
        return database.execute('SELECT clip_id,operation_id,claimed_at FROM video_production_clip_intents '
                                'WHERE plan_id=? ORDER BY rowid', (plan_id,)).fetchall()

    def _supersession_scope(self, database, parent, collected_claims):
        # Evidence is supplied only by the authenticated Host after reading its
        # private collected records and verifying the original media bytes.
        # Native manifests cannot supply these fields.
        if not isinstance(collected_claims, list) or len(collected_claims) > 8:
            raise VideoProductionPlanError('替换计划缺少 Host 原片已收取证据', 409)
        proofs = [_collected_claim(claim) for claim in collected_claims]
        by_clip = {claim['clipId']: claim for claim in proofs}
        if len(by_clip) != len(proofs):
            raise VideoProductionPlanError('原片已收取证据不能重复', 409)
        rows = self._claims(database, parent['planId'])
        if set(by_clip) != {row[0] for row in rows} or any(
                by_clip[row[0]]['operationId'] != row[1] for row in rows):
            raise VideoProductionPlanError('原片提交意图与 Host 已收取证据不一致', 409)
        return _supersession({'planId': parent['planId'], 'scopeHash': parent['scopeHash'],
            'collectedClaims': [{**by_clip[row[0]], 'claimedAt': row[2]} for row in rows],
            'unsubmittedClipIds': [clip['clipId'] for clip in parent['clips'] if clip['clipId'] not in by_clip]})

    def _reserved_ancestor_clips(self, database, parent, workshop_id):
        clips, visited = set(), set()
        while parent['planId'] not in visited:
            visited.add(parent['planId'])
            clips.update(clip['clipId'] for clip in parent['clips'])
            previous = parent.get('supersession')
            if previous is None:
                return clips
            parent = self._load(database, previous['planId'], workshop_id)
            if parent['scopeHash'] != previous['scopeHash']:
                raise VideoProductionPlanError('原制作计划替换链摘要已变化', 409)
        raise VideoProductionPlanError('原制作计划替换链不可用', 409)

    def prepare(self, workshop_id, normalized_plan, *, supersedes_plan_id=None, collected_claims=None):
        self.authorize()
        _token(workshop_id, r'[a-f0-9]{32}', '工坊编号')
        value = normalize_production_plan(normalized_plan)
        if supersedes_plan_id is None and collected_claims is not None:
            raise VideoProductionPlanError('普通制作计划不能携带原片收取证明', 409)
        with self._database(write=True) as database:
            parent = None
            if supersedes_plan_id is not None:
                parent = self._record(database, self._load(database, supersedes_plan_id, workshop_id))
                value['supersession'] = self._supersession_scope(database, parent, collected_claims)
                if {clip['clipId'] for clip in value['clips']} & self._reserved_ancestor_clips(database, parent, workshop_id):
                    raise VideoProductionPlanError('替换计划须使用全新片段编号，不能覆盖原任务', 409)
            scope_hash = self._scope_hash(workshop_id, value)
            prior = database.execute('SELECT id FROM video_production_plans WHERE actor_id=? AND project_id=? '
                'AND session_id=? AND workshop_id=? AND scope_hash=?',
                (self.actor_id, self.ip, self.session_id, workshop_id, scope_hash)).fetchone()
            if prior:
                record = self._record(database, self._load(database, prior[0]))
                if parent is not None and not (parent['state'] == 'approved' or
                        parent['state'] == 'superseded' and parent.get('supersededBy') == record['planId']):
                    raise VideoProductionPlanError('原制作计划已由另一计划取代', 409)
                return record
            if parent is not None and parent['state'] != 'approved':
                raise VideoProductionPlanError('只有已批准原制作计划可以受控替换', 409)
            now = self.now_ms()
            if type(now) is not int:
                raise VideoProductionPlanError('Host 制作时钟无效', 503)
            record = {'planId': uuid.uuid4().hex, 'scopeHash': scope_hash,
                'actorId': self.actor_id, 'projectId': self.ip, 'sessionId': self.session_id,
                'workshopId': workshop_id, 'state': 'pending', 'createdAt': now,
                'expiresAt': now + self.approval_ttl_ms, 'maxGenerations': len(value['clips']),
                'totalSeconds': sum(item['seconds'] for item in value['clips']),
                'estimatedCost': 'unknown', **value}
            database.execute('INSERT INTO video_production_plans VALUES (?,?,?,?,?,?,?)',
                (record['planId'], self.actor_id, self.ip, self.session_id, workshop_id, scope_hash,
                 json.dumps(record, ensure_ascii=False, allow_nan=False)))
            return self._record(database, record)

    def get(self, plan_id, *, workshop_id=None):
        self.authorize()
        with self._database(write=True) as database:
            return self._record(database, self._load(database, plan_id, workshop_id))

    def has_plan(self):
        """A declined/pending/expired plan cannot fall back to legacy admission."""
        self.authorize()
        with self._database() as database:
            return database.execute('SELECT 1 FROM video_production_plans WHERE actor_id=? AND project_id=? '
                'AND session_id=? LIMIT 1', (self.actor_id, self.ip, self.session_id)).fetchone() is not None

    def has_approved_plan(self):
        self.authorize()
        with self._database(write=True) as database:
            rows = database.execute('SELECT id FROM video_production_plans WHERE actor_id=? AND project_id=? '
                'AND session_id=?', (self.actor_id, self.ip, self.session_id)).fetchall()
            return any(self._record(database, self._load(database, row[0]))['state'] == 'approved' for row in rows)

    def _active_workshop_plans(self, database, workshop_id, *, now):
        rows = database.execute('SELECT id,scope_hash,data,session_id,workshop_id FROM video_production_plans '
            'WHERE actor_id=? AND project_id=? AND workshop_id=?',
            (self.actor_id, self.ip, workshop_id)).fetchall()
        active = set()
        for row in rows:
            record = self._decode_private_row(row[0], row[1:])
            if record['state'] != 'approved':
                continue
            started = database.execute('SELECT 1 FROM video_production_clip_intents WHERE plan_id=? LIMIT 1',
                                       (row[0],)).fetchone() is not None
            if started or now < record['expiresAt']:
                active.add(row[0])
        return active

    def decide(self, plan_id, expected_hash, approved):
        """Called only by the Host after its real approval question settles."""
        self.authorize()
        if type(approved) is not bool:
            raise VideoProductionPlanError('制作批准必须是真实布尔决定')
        with self._database(write=True) as database:
            record = self._record(database, self._load(database, plan_id))
            if expected_hash != record['scopeHash']:
                raise VideoProductionPlanError('制作计划摘要已变化，未批准或开始生成', 409)
            desired = 'approved' if approved else 'declined'
            if record['state'] == desired:
                return record
            if record['state'] != 'pending':
                raise VideoProductionPlanError('制作计划决定已结算，不能重新授权', 409)
            now = self.now_ms()
            if type(now) is not int or now < 0:
                raise VideoProductionPlanError('Host 制作时钟无效', 503)
            parent = None
            if approved:
                lineage = record.get('supersession')
                if lineage is not None:
                    parent = self._record(database, self._load(database, lineage['planId'], record['workshopId']))
                    if parent['state'] != 'approved' or parent['scopeHash'] != lineage['scopeHash']:
                        raise VideoProductionPlanError('原制作计划已变化或由其他计划取代，未批准新范围', 409)
                    proofs = [{key: value for key, value in claim.items() if key != 'claimedAt'}
                              for claim in lineage['collectedClaims']]
                    if self._supersession_scope(database, parent, proofs) != lineage:
                        raise VideoProductionPlanError('原制作计划的提交意图已变化，未批准新范围', 409)
                allowed = {parent['planId']} if parent is not None else set()
                if self._active_workshop_plans(database, record['workshopId'], now=now) - allowed:
                    raise VideoProductionPlanError('同一工坊已有已批准制作范围，请明确替换原计划', 409)
                if parent is not None:
                    parent.update(state='superseded', supersededBy=record['planId'], supersededAt=now)
                    self._save(database, parent)
            record.update(state=desired, decidedAt=now)
            self._save(database, record)
            return record

    def _reconfirmable(self, database, record):
        now = self.now_ms()
        if (type(now) is not int or now < record['expiresAt'] or record['state'] != 'expired'
                or self._claims(database, record['planId']) or record.get('supersession')):
            raise VideoProductionPlanError('仅能重新确认已过期、零提交的普通原制作计划', 409)
        if self._active_workshop_plans(database, record['workshopId'], now=now):
            raise VideoProductionPlanError('同一工坊已有已批准制作范围，不能恢复旧范围', 409)
        return now

    def reconfirmation(self, plan_id, *, workshop_id):
        """Host-only preflight; does not grant approval or extend a deadline."""
        self.authorize()
        with self._database(write=True) as database:
            record = self._record(database, self._load(database, plan_id, workshop_id))
            self._reconfirmable(database, record)
            return record

    def reconfirm(self, plan_id, expected_hash, expired_at, approved, *, workshop_id):
        """Settle one expired epoch only after a fresh Host human question."""
        self.authorize()
        if type(approved) is not bool or type(expired_at) is not int:
            raise VideoProductionPlanError('原范围重新确认须有真实决定及原到期版本', 409)
        with self._database(write=True) as database:
            record = self._record(database, self._load(database, plan_id, workshop_id))
            if expected_hash != record['scopeHash'] or expired_at != record['expiresAt']:
                raise VideoProductionPlanError('原范围或确认版本已变化，旧确认不能恢复批准', 409)
            now = self._reconfirmable(database, record)
            expiry = now + _reconfirmation_ttl(record) if approved else expired_at
            decision = 'approved' if approved else 'declined'
            record.setdefault('reconfirmations', []).append({'scopeHash': record['scopeHash'],
                'previousExpiresAt': expired_at, 'previousDecidedAt': record.get('decidedAt'),
                'decidedAt': now, 'decision': decision, 'expiresAt': expiry})
            record.update(state=decision, decidedAt=now, expiresAt=expiry)
            self._save(database, record)
            return record

    @staticmethod
    def _clip(record, clip_id):
        if record['state'] != 'approved':
            raise VideoProductionPlanError('制作计划尚未得到真实人工批准', 409)
        clip = next((item for item in record['clips'] if item['clipId'] == clip_id), None)
        if clip is None:
            raise VideoProductionPlanError('片段不在已批准制作计划中', 404)
        return {**clip, 'modelId': record['modelId'], 'aspectRatio': record['aspectRatio'],
                'resolution': record['resolution']}

    def approved_clip(self, plan_id, clip_id, *, workshop_id=None):
        return self._clip(self.get(plan_id, workshop_id=workshop_id), clip_id)

    def claim_clip(self, plan_id, clip_id, operation_id, *, workshop_id=None):
        self.authorize()
        _text(operation_id, 256, '生成操作编号')
        with self._database(write=True) as database:
            record = self._record(database, self._load(database, plan_id, workshop_id))
            self._clip(record, clip_id)
            previous = database.execute('SELECT operation_id,claimed_at FROM video_production_clip_intents '
                                        'WHERE plan_id=? AND clip_id=?', (plan_id, clip_id)).fetchone()
            if previous:
                if previous[0] != operation_id:
                    raise VideoProductionPlanError('原片段已有提交意图，不能另购', 409)
                return {'claimed': False, 'planId': plan_id, 'clipId': clip_id,
                        'operationId': previous[0], 'claimedAt': previous[1]}
            now = self.now_ms()
            try:
                database.execute('INSERT INTO video_production_clip_intents VALUES (?,?,?,?)',
                                 (plan_id, clip_id, operation_id, now))
            except sqlite3.IntegrityError:
                raise VideoProductionPlanError('原生成操作已准入其他计划片段，不能重复购买', 409) from None
            return {'claimed': True, 'planId': plan_id, 'clipId': clip_id,
                    'operationId': operation_id, 'claimedAt': now}
