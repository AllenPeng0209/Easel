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

_RECEIPT_FIELDS = {'status', 'reason', 'phase', 'operationId', 'payloadHash', 'platform', 'mode',
                   'accountId', 'externalId', 'publicationState', 'evidenceMethod', 'runnerExitCode',
                   'notStarted', 'identityVerification'}
_RECEIPT_REASONS = {'machine_receipt_missing', 'dry_run', 'help', 'validation_failed', 'runner_unavailable',
    'timeout_after_submission', 'interrupted_after_submission', 'submission_not_acknowledged',
    'submission_account_unverified', 'provider_ack_missing', 'provider_id_missing', 'account_mismatch',
    'provider_draft_created', 'unsupported_provider_effect', 'provider_submission_acknowledged',
    'readback_unverified', 'readback_missing', 'readback_id_mismatch', 'readback_account_unverified',
    'readback_ambiguous', 'readback_status_unknown', 'readback_time_unverified',
    'public_readback_confirmed', 'publication_not_public', 'readback_visibility_unknown', 'provider_rejected'}


def _external_id(value):
    if type(value) is int:
        value = str(value) if value > 0 else ''
    return (value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+=-]{0,255}', value)
            and not (value.isdecimal() and int(value) == 0) else None)


def _receipt_fields(value):
    """Retain the normalizer's safe result contract, never raw runner output."""
    clean = {key: item for key, item in value.items() if key in _RECEIPT_FIELDS
             and (isinstance(item, str) and key not in {'runnerExitCode', 'notStarted'}
                  or key == 'runnerExitCode' and type(item) is int
                  or key == 'externalId' and type(item) is int or key == 'notStarted' and type(item) is bool)}
    if clean.get('reason') not in _RECEIPT_REASONS:
        clean.pop('reason', None)
    enums = {'phase': {'unknown', 'not_started', 'submit_attempted', 'platform_ack', 'readback'},
             'evidenceMethod': {'provider_response', 'authenticated_readback'},
             'publicationState': {'unknown', 'public', 'processing', 'reviewing', 'pending', 'private',
                                  'scheduled', 'rejected', 'deleted', 'prohibited', 'draft'},
             'identityVerification': {'host_dispatch_only', 'authenticated_submission', 'account_mismatch'}}
    for key, allowed in enums.items():
        if clean.get(key) not in allowed:
            clean.pop(key, None)
    return clean


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

    def observe(self, identifier, operation_id, result, *, actor_id, project_id):
        """Append same-operation read-only evidence after its runner has ended.

        This is not execution authority: it cannot finish an active runner,
        acquire a profile, generate an operation, or replace a concrete post ID.
        Only the Host calls this with a versioned normalizer's safe result.
        """
        with self._database(write=True) as database:
            record = self._load(database, identifier, actor_id, project_id)
            if not record['operationId'] or record['operationId'] != operation_id:
                raise PublicationApprovalError('核对回执不属于该批准操作', 409)
            previous = record.get('result') or {}
            if previous.get('status') == 'executing':
                raise PublicationApprovalError('运行尚未结束，不能用只读核对终结执行', 409)
            if not isinstance(result, dict):
                raise PublicationApprovalError('核对回执必须是 JSON 对象')
            subject = record['subject']
            expected = {'operationId': operation_id, 'payloadHash': record['payloadHash'],
                        'platform': subject['platform']['id'], 'mode': subject.get('mode'),
                        'accountId': (subject.get('account') or {}).get('id')}
            if (any(not isinstance(value, str) or not value for value in expected.values())
                    or any(result.get(key) != value for key, value in expected.items())):
                raise PublicationApprovalError('核对回执与批准对象、平台或账号不匹配', 409)
            value, _, digest = _canonical(_receipt_fields(result))
            status = value.get('status')
            allowed = {'publish_now': {'unknown', 'submitted', 'confirmed', 'failed'},
                       'scheduled': {'unknown', 'submitted', 'scheduled', 'confirmed', 'failed'},
                       'create_draft': {'unknown', 'draft_created', 'failed'},
                       'manual_export': {'unknown', 'exported', 'failed'}}
            if not isinstance(status, str) or status not in allowed.get(expected['mode'], set()):
                raise PublicationApprovalError('核对结果与批准的执行方式不一致', 409)
            identifier_value = value.get('externalId')
            external_id = _external_id(identifier_value)
            if identifier_value is not None and external_id is None:
                raise PublicationApprovalError('核对回执缺少有效的平台对象编号', 409)
            if external_id is not None:
                value['externalId'] = external_id
            if status == 'confirmed' and (value.get('evidenceMethod') != 'authenticated_readback'
                    or value.get('publicationState') != 'public' or external_id is None):
                raise PublicationApprovalError('公开确认必须具有账号独立读回、公开状态和具体对象编号', 409)
            if status in {'submitted', 'scheduled', 'draft_created'} and external_id is None:
                raise PublicationApprovalError('平台效果回执必须具有具体对象编号', 409)
            observations = record.get('observations') or []
            # B30 accepted snake_case platform IDs before the D30 normalizer
            # standardized externalId. Those older proofs bind the same object.
            known_ids = {_external_id(prior.get(key))
                         for prior in [previous, *(item.get('result', {}) for item in observations)]
                         for key in ('externalId', 'external_id', 'external_message_id')}
            known_ids.discard(None)
            if status == 'failed':
                not_started = (value.get('notStarted') is True and value.get('phase') == 'not_started'
                    and value.get('reason') in {'dry_run', 'help', 'validation_failed', 'runner_unavailable'}
                    and not known_ids)
                provider_rejected = (value.get('notStarted') is not True and value.get('phase') == 'readback'
                    and value.get('evidenceMethod') == 'authenticated_readback' and external_id is not None
                    and value.get('publicationState') in {'rejected', 'prohibited'}
                    and value.get('reason') == 'provider_rejected')
                if not (not_started or provider_rejected):
                    raise PublicationApprovalError('失败核对必须有可靠的未执行证据或同对象的平台拒绝读回', 409)
            if external_id is not None and known_ids and known_ids != {external_id}:
                raise PublicationApprovalError('同一操作的核对不能替换为另一个平台对象', 409)
            # Hash only canonical safe fields; the same observation after a
            # restart or from another tab cannot append duplicate audit entries.
            value, _, digest = _canonical(value)
            if any(item.get('digest') == digest for item in observations):
                return record
            if not observations and previous:
                historical = _receipt_fields(previous)
                # Earlier B30 records may carry a receipt ID instead of the D30
                # externalId field. Preserve that existing proof without logs.
                for key in ('receiptId', 'receipt_id', 'external_id', 'external_message_id'):
                    if _external_id(previous.get(key)) is not None:
                        historical[key] = previous[key]
                historical, _, historical_digest = _canonical(historical)
                observations.append({'digest': historical_digest, 'observedAt': _now_ms(), 'result': historical})
                if historical_digest == digest:
                    record['observations'] = observations
                    self._save(database, record)
                    return record
            observations.append({'digest': digest, 'observedAt': _now_ms(), 'result': value})
            record['observations'] = observations
            old_status = previous.get('status')
            if (old_status in (None, 'unknown') or old_status == status
                    or old_status in {'submitted', 'scheduled'} and status in {'confirmed', 'failed'}):
                # Read-only platform observations cannot replace independent
                # Host facts about process termination or output truncation.
                process = {key: previous[key] for key in ('processState', 'terminationConfirmed',
                    'runnerTimedOut', 'outputTruncated') if key in previous}
                record['result'] = {**value, **process}
            self._save(database, record)
        return record
