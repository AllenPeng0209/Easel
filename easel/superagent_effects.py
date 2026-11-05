"""Task outbox for publication effects with independent, exact host approval.

Adapted from the persisted receipt/effect boundary in dsh-superagent
8c32a9d src/app/outbox.ts. Publishing uses Xiangya's existing approval dispatcher.
A reserved effect cannot be dispatched again, including after a process crash.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
import re
import sqlite3
import time

from easel.publication_approvals import (
    PublicationApprovalError,
    PublicationApprovalStore,
    _canonical,
    _external_id,
    _identity,
)


class SuperagentEffectError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class SuperagentEffectStore:
    def __init__(self, root: Path):
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = root / 'superagent_effects.sqlite3'
        with self._write() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS superagent_publication_effects (
                    actor TEXT NOT NULL, project TEXT NOT NULL, task TEXT NOT NULL,
                    effect TEXT NOT NULL, approval TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    state TEXT NOT NULL, operation TEXT, receipt TEXT,
                    updated_ms INTEGER NOT NULL,
                    PRIMARY KEY (actor, project, task, effect),
                    UNIQUE (actor, project, approval)
                )
            """)

    @contextmanager
    def _write(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            yield db

    @staticmethod
    def _key(task_id, effect_id, actor_id, project_id):
        key = (actor_id, project_id, task_id, effect_id)
        try:
            for value in key:
                _identity(value)
        except PublicationApprovalError as exc:
            raise SuperagentEffectError(str(exc), exc.status_code) from exc
        return key

    @staticmethod
    def _load(db, key):
        row = db.execute(
            'SELECT * FROM superagent_publication_effects WHERE actor=? AND project=? AND task=? AND effect=?', key,
        ).fetchone()
        if row is None:
            raise SuperagentEffectError('任务动作不存在', 404)
        return row

    @staticmethod
    def _view(row, dispatch=False):
        return {
            'actorId': row['actor'], 'projectId': row['project'], 'taskId': row['task'],
            'effectId': row['effect'], 'approvalId': row['approval'], 'payloadHash': row['payload_hash'],
            'state': row['state'], 'operationId': row['operation'],
            'receipt': json.loads(row['receipt']) if row['receipt'] else None,
            'updatedAt': row['updated_ms'], 'dispatch': dispatch,
        }

    @staticmethod
    def _update(db, key, row, state, operation=None, receipt=None):
        encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True) if receipt is not None else None
        if (row['state'], row['operation'], row['receipt']) != (state, operation, encoded):
            db.execute("""
                UPDATE superagent_publication_effects SET state=?,operation=?,receipt=?,updated_ms=?
                WHERE actor=? AND project=? AND task=? AND effect=?
            """, (state, operation, encoded, int(time.time() * 1000), *key))

    @staticmethod
    def _approval(row, approvals):
        try:
            approval = approvals.get(row['approval'], actor_id=row['actor'], project_id=row['project'])
        except PublicationApprovalError as exc:
            raise SuperagentEffectError(str(exc), exc.status_code) from exc
        if approval['payloadHash'] != row['payload_hash']:
            raise SuperagentEffectError('任务动作与批准内容不一致')
        subject = approval['subject']
        account = subject.get('account')
        try:
            _identity(account.get('id') if isinstance(account, dict) else None)
        except PublicationApprovalError as exc:
            raise SuperagentEffectError('批准对象缺少明确的平台账号') from exc
        if subject.get('mode') not in ('publish_now', 'create_draft', 'scheduled', 'manual_export'):
            raise SuperagentEffectError('批准对象缺少明确的执行方式')
        if row['operation'] is not None and row['operation'] != approval['operationId']:
            raise SuperagentEffectError('平台回执操作编号已变化')
        return approval

    def enqueue(self, task_id, effect_id, *, actor_id, project_id, approval_id, subject):
        """Register an exact proposed action. This operation grants no dispatch authority."""
        key = self._key(task_id, effect_id, actor_id, project_id)
        if not isinstance(approval_id, str) or not re.fullmatch(r'[a-f0-9]{32}', approval_id):
            raise SuperagentEffectError('无效的发布批准编号', 400)
        try:
            _, _, digest = _canonical(subject)
        except PublicationApprovalError as exc:
            raise SuperagentEffectError(str(exc), exc.status_code) from exc
        with self._write() as db:
            try:
                db.execute("""
                    INSERT INTO superagent_publication_effects VALUES(?,?,?,?,?,?,'waiting_approval',NULL,NULL,?)
                    ON CONFLICT(actor,project,task,effect) DO NOTHING
                """, (*key, approval_id, digest, int(time.time() * 1000)))
            except sqlite3.IntegrityError as exc:
                raise SuperagentEffectError('该发布批准已关联其他任务动作') from exc
            row = self._load(db, key)
            if (row['approval'], row['payload_hash']) != (approval_id, digest):
                raise SuperagentEffectError('同一任务动作不能替换发布内容或批准')
            return self._view(row)

    def reserve(self, task_id, effect_id, *, actor_id, project_id, approvals: PublicationApprovalStore):
        """Reserve the only dispatch attempt. The publisher must still claim the approval."""
        key = self._key(task_id, effect_id, actor_id, project_id)
        with self._write() as db:
            row = self._load(db, key)
            if row['state'] != 'waiting_approval':
                return self._view(row)
            approval = self._approval(row, approvals)
            if approval['operationId']:
                state, receipt = self._observed(approval)
                self._update(db, key, row, state, approval['operationId'], receipt)
                return self._view(self._load(db, key))
            if approval['state'] != 'approved':
                if approval['state'] != 'pending':
                    self._update(db, key, row, 'approval_unavailable')
                return self._view(self._load(db, key))
            self._update(db, key, row, 'in_flight')
            return self._view(self._load(db, key), dispatch=True)

    @staticmethod
    def _observed(approval):
        result = approval.get('result') or {}
        subject = approval['subject']
        binding = {
            'operationId': approval['operationId'], 'payloadHash': approval['payloadHash'],
            'platform': subject['platform']['id'], 'mode': subject.get('mode'),
            'accountId': (subject.get('account') or {}).get('id'),
        }
        if any(result.get(field) != expected for field, expected in binding.items()):
            return 'uncertain', None
        status = result.get('status')
        identifier = _external_id(result.get('externalId'))
        if status == 'confirmed' and not (
            identifier and result.get('evidenceMethod') == 'authenticated_readback'
            and result.get('publicationState') == 'public'
        ):
            return 'uncertain', None
        if status in ('submitted', 'scheduled', 'draft_created') and not identifier:
            return 'uncertain', None
        if status not in ('submitted', 'scheduled', 'confirmed', 'draft_created', 'exported', 'failed'):
            return 'uncertain', None
        return status, {**binding, 'status': status, **({'externalId': identifier} if identifier else {})}

    def reconcile(self, task_id, effect_id, *, actor_id, project_id, approvals: PublicationApprovalStore):
        """Observe the current publication receipt. Never dispatch from recovery."""
        key = self._key(task_id, effect_id, actor_id, project_id)
        with self._write() as db:
            row = self._load(db, key)
            approval = self._approval(row, approvals)
            if approval['operationId']:
                state, receipt = self._observed(approval)
                self._update(db, key, row, state, approval['operationId'], receipt)
            elif row['state'] == 'in_flight':
                self._update(db, key, row, 'uncertain')
            return self._view(self._load(db, key))


async def execute_publication_effect(workflow, effects: SuperagentEffectStore, task_id, effect_id, approval_id,
                                     *, check_lease):
    """Execute using the publication workflow's approved subject and read-back path.

    The Host supplies a trusted lease check that raises on stop, expiry, or a
    revoked permission. The existing dispatcher still validates the account,
    immutable media and approval immediately before its one-shot claim.
    """
    check_lease()
    scope = workflow.scope()
    approvals = workflow.store()
    record = workflow.get(approval_id)
    subject = record['subject']
    effects.enqueue(task_id, effect_id, approval_id=approval_id, subject=subject, **scope)
    check_lease()
    reservation = effects.reserve(task_id, effect_id, approvals=approvals, **scope)
    if reservation['dispatch']:
        data = {key: subject.get(key, '') for key in ('title', 'body', 'tags', 'persona')}
        data.update(artifactIds=[item['artifactId'] for item in subject.get('media', [])],
                    approvalId=approval_id, payloadHash=record['payloadHash'],
                    aiGenerated=subject.get('aiGenerated', False))
        check_lease()
        try:
            await workflow.start(subject['platform']['id'], data, before_claim=check_lease, wait_for_execution=True)
        except (TimeoutError, OSError):
            # The call may have crossed the external boundary. Only read-back
            # or an existing normalized receipt can settle that reservation.
            pass
    return effects.reconcile(task_id, effect_id, approvals=approvals, **scope)
