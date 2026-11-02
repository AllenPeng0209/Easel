"""B30 exact publication subjects and durable one-shot execution authority.

The subject/decision boundary follows DeepSeek Harness ui-approval/tool-approval
(ce7073d, MIT): the Host owns identity, scope and saved grants; the card only
collects a decision. Durable intent/unknown-result handling reuses Easel's
interaction_center pattern. No platform is contacted by this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid


class PublicationApprovalError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _now_ms() -> int:
    return int(time.time() * 1000)


def _canonical(value):
    def check(item, active):
        if isinstance(item, (dict, list)):
            if id(item) in active:
                raise ValueError
            active.add(id(item))
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError
                for child in item.values():
                    check(child, active)
            else:
                for child in item:
                    check(child, active)
            active.remove(id(item))
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError
    try:
        check(value, set())
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        return json.loads(text), text, digest
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise PublicationApprovalError('发布对象必须是合法、有限且键为字符串的 JSON') from None


def _identity(value):
    if (not isinstance(value, str) or not value or len(value) > 256
            or any(ord(character) < 32 for character in value)):
        raise PublicationApprovalError('无效的认证主体或 IP 标识')


_RESULT_STATES = {'executing', 'submitted', 'confirmed', 'unknown', 'failed',
                  'draft_created', 'exported', 'scheduled'}


class PublicationApprovalStore:
    def __init__(self, directory: Path):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / 'publication_approvals.sqlite3'
        with self._database() as database:
            names = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                "('publication_approvals','publication_approval_calls')")}
            if names == {'publication_approvals', 'publication_approval_calls'}:
                return
        with self._database(write=True) as database:
            database.execute('CREATE TABLE IF NOT EXISTS publication_approvals ('
                'id TEXT PRIMARY KEY,actor_id TEXT NOT NULL,project_id TEXT NOT NULL,platform_id TEXT NOT NULL,'
                'payload_hash TEXT NOT NULL,state TEXT NOT NULL,operation_id TEXT UNIQUE,execution_status TEXT,data TEXT NOT NULL)')
            database.execute('CREATE INDEX IF NOT EXISTS publication_profile_execution '
                             'ON publication_approvals(project_id,platform_id,execution_status)')
            database.execute('CREATE TABLE IF NOT EXISTS publication_approval_calls ('
                'actor_id TEXT NOT NULL,project_id TEXT NOT NULL,call_key TEXT NOT NULL,request_hash TEXT NOT NULL,'
                'approval_id TEXT NOT NULL,PRIMARY KEY(actor_id,project_id,call_key))')

    @contextmanager
    def _database(self, *, write=False):
        database = sqlite3.connect(self.path, timeout=10)
        try:
            if write:
                database.execute('BEGIN IMMEDIATE')
            yield database
            if write:
                database.commit()
        except BaseException:
            if write:
                database.rollback()
            raise
        finally:
            database.close()

    @staticmethod
    def _load(database, identifier, actor_id, project_id):
        _identity(actor_id)
        _identity(project_id)
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise PublicationApprovalError('发布批准请求不存在', 404)
        row = database.execute('SELECT data FROM publication_approvals WHERE id=? AND actor_id=? AND project_id=?',
                               (identifier, actor_id, project_id)).fetchone()
        if row is None:
            raise PublicationApprovalError('发布批准请求不存在', 404)
        return json.loads(row[0])

    @staticmethod
    def _save(database, record):
        result = record['result']
        database.execute('UPDATE publication_approvals SET state=?,operation_id=?,execution_status=?,data=? WHERE id=?',
            (record['state'], record['operationId'], result.get('status') if result else None,
             json.dumps(record, ensure_ascii=False, allow_nan=False), record['id']))

    @staticmethod
    def _expire(database, record):
        if (record['state'] in {'pending', 'approved'} and record['operationId'] is None
                and _now_ms() >= record['expiresAt']):
            record['state'] = 'expired'
            PublicationApprovalStore._save(database, record)
        return record

    @staticmethod
    def _call_args(key, request_hash):
        if key == '' and request_hash == '':
            return False
        if (not isinstance(key, str) or not key or len(key) > 2048
                or any(ord(character) < 32 for character in key)
                or not isinstance(request_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', request_hash)):
            raise PublicationApprovalError('原生调用幂等标识或参数摘要无效')
        return True

    def _lookup_call(self, database, key, request_hash, actor_id, project_id):
        row = database.execute('SELECT request_hash,approval_id FROM publication_approval_calls '
            'WHERE actor_id=? AND project_id=? AND call_key=?', (actor_id, project_id, key)).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            raise PublicationApprovalError('同一原生调用不能绑定不同参数', 409)
        return self._load(database, row[1], actor_id, project_id)

    def lookup_call(self, idempotency_key, request_hash, *, actor_id, project_id):
        _identity(actor_id)
        _identity(project_id)
        if not self._call_args(idempotency_key, request_hash):
            return None
        with self._database() as database:
            record = self._lookup_call(database, idempotency_key, request_hash, actor_id, project_id)
        return self.get(record['id'], actor_id=actor_id, project_id=project_id) if record else None

    def create(self, subject, *, actor_id, project_id, ttl_seconds=900, idempotency_key='', request_hash=''):
        _identity(actor_id)
        _identity(project_id)
        keyed = self._call_args(idempotency_key, request_hash)
        if keyed:
            existing = self.lookup_call(idempotency_key, request_hash, actor_id=actor_id, project_id=project_id)
            if existing is not None:
                return existing
        if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float))
                or not math.isfinite(ttl_seconds) or ttl_seconds <= 0):
            raise PublicationApprovalError('批准有效期无效')
        value, text, digest = _canonical(subject)
        if not isinstance(value, dict) or not value:
            raise PublicationApprovalError('发布对象不能为空')
        platform = value.get('platform')
        if not isinstance(platform, dict) or not isinstance(platform.get('id'), str) or not platform['id'].strip():
            raise PublicationApprovalError('发布对象缺少明确平台')
        _identity(platform['id'])
        project = value.get('project')
        if (value.get('actorId', actor_id) != actor_id or value.get('actor_id', actor_id) != actor_id
                or value.get('projectId', project_id) != project_id or value.get('project_id', project_id) != project_id
                or project is not None and (not isinstance(project, dict) or project.get('id') != project_id)):
            raise PublicationApprovalError('发布对象与认证主体或 IP 不一致')
        created = _now_ms()
        expires = created + int(ttl_seconds * 1000)
        if expires > 9_223_372_036_854_775_807:
            raise PublicationApprovalError('批准有效期无效')
        record = {'id': uuid.uuid4().hex, 'payloadHash': digest, 'subject': value, 'state': 'pending',
            'createdAt': created, 'expiresAt': expires, 'actorId': actor_id, 'projectId': project_id,
            'operationId': None, 'result': None, 'decidedAt': None}
        with self._database(write=True) as database:
            if keyed:
                existing = self._lookup_call(database, idempotency_key, request_hash, actor_id, project_id)
                if existing is not None:
                    return self._expire(database, existing)
            database.execute('INSERT INTO publication_approvals VALUES(?,?,?,?,?,?,?,?,?)',
                (record['id'], actor_id, project_id, platform['id'], digest, 'pending', None, None,
                 json.dumps(record, ensure_ascii=False)))
            if keyed:
                database.execute('INSERT INTO publication_approval_calls VALUES(?,?,?,?,?)',
                    (actor_id, project_id, idempotency_key, request_hash, record['id']))
        return record

    def get(self, identifier, *, actor_id, project_id):
        with self._database() as database:
            record = self._load(database, identifier, actor_id, project_id)
        if (record['state'] in {'pending', 'approved'} and record['operationId'] is None
                and _now_ms() >= record['expiresAt']):
            with self._database(write=True) as database:
                record = self._expire(database, self._load(database, identifier, actor_id, project_id))
        return record

    def decide(self, identifier, approved: bool, payload_hash, *, actor_id, project_id):
        if type(approved) is not bool:
            raise PublicationApprovalError('批准决定必须明确为允许或拒绝')
        failure = None
        with self._database(write=True) as database:
            record = self._expire(database, self._load(database, identifier, actor_id, project_id))
            decision = 'approved' if approved else 'rejected'
            if payload_hash != record['payloadHash']:
                if record['operationId'] is None and record['state'] in {'pending', 'approved'}:
                    record['state'] = 'invalidated'
                    self._save(database, record)
                failure = PublicationApprovalError('批准对象已变化，原批准不可使用', 409)
            elif record['state'] == decision:
                pass
            elif record['state'] != 'pending':
                failure = PublicationApprovalError('批准请求已决定、过期或失效', 409)
            else:
                record.update(state=decision, decidedAt=_now_ms())
                self._save(database, record)
        if failure:
            raise failure
        return record

    @staticmethod
    def _profile_busy(database, record):
        row = database.execute('SELECT 1 FROM publication_approvals WHERE project_id=? AND platform_id=? '
            "AND execution_status='executing' AND id<>? LIMIT 1",
            (record['projectId'], record['subject']['platform']['id'], record['id'])).fetchone()
        if row:
            raise PublicationApprovalError('该 IP 的平台账号正在执行另一操作，请等待其结果', 409)

    def profile_busy(self, platform_id, *, project_id):
        """Host-only resource hint; never expose another actor's approval."""
        _identity(platform_id)
        _identity(project_id)
        with self._database() as database:
            return database.execute("SELECT 1 FROM publication_approvals WHERE project_id=? AND platform_id=? "
                "AND execution_status='executing' LIMIT 1", (project_id, platform_id)).fetchone() is not None

    def claim(self, identifier, current_subject, *, actor_id, project_id):
        failure = None
        claimed = False
        with self._database(write=True) as database:
            record = self._expire(database, self._load(database, identifier, actor_id, project_id))
            _, _, current_hash = _canonical(current_subject)
            if current_hash != record['payloadHash']:
                if record['operationId'] is None and record['state'] in {'pending', 'approved'}:
                    record['state'] = 'invalidated'
                    self._save(database, record)
                failure = PublicationApprovalError('发布对象已变化，请重新审核当前对象', 409)
            elif record['state'] != 'approved':
                failure = PublicationApprovalError('该发布对象尚未获批、已过期或已失效', 409)
            elif record['operationId'] is not None:
                pass
            else:
                self._profile_busy(database, record)
                record.update(operationId=uuid.uuid4().hex, result={'status': 'executing'})
                self._save(database, record)
                claimed = True
        if failure:
            raise failure
        return {**record, 'claimed': claimed}

    @staticmethod
    def _result(value):
        result, _, _ = _canonical(value)
        if not isinstance(result, dict):
            raise PublicationApprovalError('执行回执必须是 JSON 对象')
        status = result.get('status')
        if not isinstance(status, str) or status not in _RESULT_STATES:
            status = 'unknown'
        proof = any(isinstance(result.get(key), (str, int)) and not isinstance(result.get(key), bool)
                    and str(result[key]).strip() for key in ('receiptId', 'receipt_id', 'externalId', 'external_id', 'external_message_id'))
        if status == 'confirmed' and not proof:
            status = 'unknown'
        result['status'] = status
        return result

    def finish(self, identifier, operation_id, result, *, actor_id, project_id):
        with self._database(write=True) as database:
            record = self._load(database, identifier, actor_id, project_id)
            if not record['operationId'] or record['operationId'] != operation_id:
                raise PublicationApprovalError('执行回执不属于该批准操作', 409)
            value = self._result(result)
            if value.get('operationId', operation_id) != operation_id or value.get('operation_id', operation_id) != operation_id:
                raise PublicationApprovalError('执行回执操作编号不匹配', 409)
            if value.get('payloadHash', record['payloadHash']) != record['payloadHash'] or value.get('payload_hash', record['payloadHash']) != record['payloadHash']:
                raise PublicationApprovalError('执行回执发布对象摘要不匹配', 409)
            previous = record['result']
            if previous == value:
                return record
            old_state = previous.get('status') if previous else None
            if old_state not in {'executing', 'submitted', 'scheduled'}:
                if not (old_state == 'unknown' and value['status'] == 'confirmed'):
                    raise PublicationApprovalError('已记录该操作的结果，不能覆盖为另一回执', 409)
            if value['status'] == 'executing':
                self._profile_busy(database, record)
            record['result'] = value
            self._save(database, record)
        return record
