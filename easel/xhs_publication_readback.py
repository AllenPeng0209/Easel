"""Exact-ACK, authenticated XHS readback; every transport operation is a GET.

The current official Creator client uses the v2 posted endpoint (tab=1,
page=0). Its shared enums define Published=1 and Public=0 independently.
Creator rows do not supply an author: the consumer-web row's literal
user.user_id and millisecond time must independently match the same note ID.
No title, URL, tab name, constructor privacy default or UI toast is proof.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time


_CONTEXT = ('operationId', 'payloadHash', 'platform', 'mode', 'approvedAccountId')
_STAMP = ('xhsSessionGeneration', 'xhsNativeAccountId', 'xhsSessionHash')
_CREATOR_POSTED = '/api/galaxy/v2/creator/note/user/posted'
_MAX_NOTES = 200
_MILLISECOND_EPOCH = 1_000_000_000_000


class XhsReadbackError(ValueError):
    """Fixed diagnostics only; never include response bodies or credentials."""


def _fail(code):
    raise XhsReadbackError(code) from None


def _clock(now_ms):
    value = now_ms() if now_ms is not None else int(time.time() * 1000)
    if type(value) is not int or not _MILLISECOND_EPOCH <= value <= 2**53 - 1:
        _fail('invalid_readback_clock')
    return value


def _note_id(value):
    return (value if isinstance(value, str) and re.fullmatch(r'[a-f0-9]{24}', value)
            and value != '0' * 24 else None)


def _context(value):
    if (not isinstance(value, dict) or set(value) != set(_CONTEXT)
            or not isinstance(value.get('operationId'), str)
            or not re.fullmatch(r'[a-f0-9]{32}', value['operationId'])
            or not isinstance(value.get('payloadHash'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', value['payloadHash'])
            or value.get('platform') != 'xiaohongshu' or value.get('mode') != 'publish_now'
            or not isinstance(value.get('approvedAccountId'), str)
            or not re.fullmatch(r'userId:[A-Za-z0-9_-]{1,256}', value['approvedAccountId'])
            or value['approvedAccountId'] == 'userId:0'):
        _fail('invalid_readback_context')
    return tuple((key, value[key]) for key in _CONTEXT)


def _stamp(value):
    if (not isinstance(value, dict) or set(value) != set(_STAMP)
            or any(type(value.get(key)) is not int or value[key] <= 0 for key in _STAMP[:2])
            or not isinstance(value.get(_STAMP[2]), str)
            or not re.fullmatch(r'[a-f0-9]{64}', value[_STAMP[2]])):
        _fail('invalid_account_snapshot')
    return tuple((key, value[key]) for key in _STAMP)


def _identity(reader, expected):
    account = reader.identity()
    if not isinstance(account, dict) or account.get('userId') != expected:
        _fail('creator_account_unverified')


def _notes(value):
    if (not isinstance(value, list) or len(value) > _MAX_NOTES
            or any(not isinstance(item, dict) for item in value)):
        _fail('invalid_note_list')
    return value


def _ids(value, field):
    result = set()
    for item in _notes(value):
        identifier = _note_id(item.get(field))
        if identifier is None or identifier in result:
            _fail('invalid_or_duplicate_baseline_id')
        result.add(identifier)
    return result


@dataclass(frozen=True)
class XhsBaseline:
    context: tuple
    snapshot_stamp: tuple
    since_ms: int
    note_ids: frozenset[str]


def capture_baseline(reader, context, *, now_ms=None):
    """Capture a new operation's real baseline, without changing browser UI.

    Failure raises before the caller's first public click. A successfully read
    empty list is distinct from an absent/malformed list. No historical probe
    or caller-supplied note IDs may stand in for these fresh reads.
    """
    binding = _context(context)
    stamp = _stamp(reader.snapshot_stamp)
    uid = context['approvedAccountId'].split(':', 1)[1]
    try:
        _identity(reader, uid)
        creator = _ids(reader.creator_notes(), 'id')
        main = _notes(reader.own_notes(uid))
        for item in main:
            if not isinstance(item.get('user'), dict) or item['user'].get('user_id') != uid:
                _fail('baseline_author_unverified')
        ids = creator | _ids(main, 'note_id')
        _identity(reader, uid)
        if _stamp(reader.snapshot_stamp) != stamp:
            _fail('account_snapshot_changed')
        return XhsBaseline(binding, stamp, _clock(now_ms), frozenset(ids))
    except XhsReadbackError:
        raise
    except Exception:
        _fail('baseline_read_failed')


def _ack_id(ack):
    if (not isinstance(ack, dict) or ack.get('kind') != 'post'
            or ack.get('accepted') is not True or ack.get('source') != 'provider_response'):
        return None
    return _note_id(ack.get('id'))


def baseline_evidence(baseline, context, *, ack=None, now_ms=None):
    """Private pre-click timestamp, then an ACK-bound new-ID proof.

    Before an ACK the new-ID flag deliberately remains false. The ACK and its
    proof must be written in one reporter revision, never in separate writes.
    """
    if not isinstance(baseline, XhsBaseline) or baseline.context != _context(context):
        _fail('baseline_context_changed')
    checked = _clock(now_ms)
    if checked < baseline.since_ms:
        _fail('invalid_readback_clock')
    identifier = _ack_id(ack)
    return {'outcome': 'unverified', 'matched': None, 'candidates': [], 'evidence': {
        'method': 'authenticated_readback', 'matchMethod': 'ack_id', 'unique': False,
        'account': {'id': context['approvedAccountId'], 'authenticated': True},
        'baselineCaptured': identifier is not None and identifier not in baseline.note_ids,
        'sinceMs': baseline.since_ms, 'checkedAtMs': checked, **dict(baseline.snapshot_stamp)}}


def ack_baseline_evidence(baseline, ack, context, *, now_ms=None):
    return baseline_evidence(baseline, context, ack=ack, now_ms=now_ms)


def _unverified(code, *, evidence=None, outcome='unverified'):
    return {'outcome': outcome, 'matched': None, 'candidates': [],
            'evidence': evidence or {}, 'error': code}


def _original(context, observation, stamp, now):
    if (not isinstance(observation, dict) or observation.get('kind') != 'publication_receipt'
            or type(observation.get('schemaVersion')) is not int or observation['schemaVersion'] != 1
            or observation.get('submissionAmbiguous') is True
            or ('submissionAmbiguous' in observation and type(observation['submissionAmbiguous']) is not bool)
            or observation.get('phase') not in {'platform_ack', 'readback'}
            or any(observation.get(key) != context[key] for key in _CONTEXT)
            or observation.get('account') != {'id': context['approvedAccountId'], 'authenticated': True}):
        _fail('original_receipt_unverified')
    identifier = _ack_id(observation.get('ack'))
    evidence = (observation.get('readback') or {}).get('evidence')
    if identifier is None or not isinstance(evidence, dict):
        _fail('original_ack_or_baseline_missing')
    since = evidence.get('sinceMs')
    if (evidence.get('baselineCaptured') is not True
            or evidence.get('account') != {'id': context['approvedAccountId'], 'authenticated': True}
            or evidence.get('method') not in {'authenticated_readback', 'original_baseline'}
            or evidence.get('matchMethod') != 'ack_id' or type(since) is not int
            or not _MILLISECOND_EPOCH <= since <= now
            or any(evidence.get(key) != value or type(evidence.get(key)) is not type(value)
                   for key, value in stamp)):
        _fail('original_baseline_or_snapshot_unverified')
    return identifier, since


def refresh_evidence_ready(context, observation, *, now_ms=None):
    """Pure preflight: legacy or ambiguous ACKs never start a credential reader."""
    try:
        _context(context)
        evidence = observation['readback']['evidence']
        stamp = _stamp({key: evidence[key] for key in _STAMP})
        _original(context, observation, stamp, _clock(now_ms))
        return True
    except (XhsReadbackError, KeyError, TypeError):
        return False


def _original_failure_evidence(context, observation, now_ms):
    """Retain a valid old baseline on failure without claiming a fresh read."""
    try:
        _context(context)
        now = _clock(now_ms)
        raw = observation['readback']['evidence']
        stamp = _stamp({key: raw[key] for key in _STAMP})
        _, since = _original(context, observation, stamp, now)
        return {'method': 'original_baseline', 'matchMethod': 'ack_id', 'unique': False,
            'account': {'id': context['approvedAccountId'], 'authenticated': True},
            'baselineCaptured': True, 'sinceMs': since, 'checkedAtMs': now, **dict(stamp)}
    except Exception:
        return {}


def verify_ack(reader, context, original_observation, *, now_ms=None):
    """Read the exact original ACK; this never uploads, publishes or retries."""
    try:
        _context(context)
        now = _clock(now_ms)
        stamp = _stamp(reader.snapshot_stamp)
        identifier, since = _original(context, original_observation, stamp, now)
    except Exception:
        return _unverified('original_receipt_unverified',
            evidence=_original_failure_evidence(context, original_observation, now_ms))
    uid = context['approvedAccountId'].split(':', 1)[1]
    evidence = {'method': 'authenticated_readback', 'matchMethod': 'ack_id', 'unique': False,
        'account': {'id': context['approvedAccountId'], 'authenticated': True},
        'baselineCaptured': True, 'sinceMs': since, 'checkedAtMs': now, **dict(stamp)}
    try:
        _identity(reader, uid)
        creators = [item for item in _notes(reader.creator_notes()) if item.get('id') == identifier]
        mains = [item for item in _notes(reader.own_notes(uid)) if item.get('note_id') == identifier]
        _identity(reader, uid)
        if _stamp(reader.snapshot_stamp) != stamp:
            _fail('account_snapshot_changed')
        evidence['checkedAtMs'] = _clock(now_ms)
    except XhsReadbackError:
        return _unverified('account_or_source_unverified', evidence=evidence)
    except Exception:
        return _unverified('source_read_failed', evidence=evidence, outcome='readback_error')
    if len(creators) != 1 or len(mains) > 1:
        return _unverified('note_not_found_or_ambiguous', evidence=evidence)
    item = creators[0]
    status, permission = item.get('tab_status'), item.get('permission_code')
    if type(status) is not int or type(permission) is not int or status not in {1, 2, 3, 4}:
        return _unverified('creator_status_unverified', evidence=evidence)
    if mains and (not isinstance(mains[0].get('user'), dict) or mains[0]['user'].get('user_id') != uid):
        return _unverified('author_conflict', evidence=evidence)
    # Same-ID nonpublic state is useful even before consumer indexing, but it
    # never manufactures an author or a publication timestamp.
    state = {2: 'reviewing', 3: 'rejected', 4: 'scheduled'}.get(status)
    if state is None and permission != 0:
        if permission == 1:
            state = 'private'
        else:
            # The observed code 4 means mutual friends, not "only me".
            # Unknown permissions must not acquire an invented status label.
            return _unverified('visibility_not_public', evidence=evidence)
    published = None
    if state is None:
        if len(mains) != 1:
            return _unverified('author_source_pending', evidence=evidence)
        published = mains[0].get('time')
        if type(published) is not int or not _MILLISECOND_EPOCH <= published <= 2**53 - 1:
            return _unverified('millisecond_time_unverified', evidence=evidence)
        if published < since - 1000 or published > evidence['checkedAtMs'] + 1000:
            return _unverified('publication_time_outside_original_intent', evidence=evidence)
        state = 'published'
    matched = {'platform_content_id': identifier, 'status': state, 'published_at_ms': published}
    evidence.update(unique=True, statusSource='explicit', visibility='public' if state == 'published' else state)
    return {'outcome': 'verified', 'matched': matched, 'candidates': [dict(matched)], 'evidence': evidence}


class _SessionFence:
    def __init__(self, workspace):
        from .account_sessions import load_session
        self.workspace = Path(workspace).absolute()
        self.snapshot = load_session(self.workspace, 'xhs')
        self.stamp = self._stamp(self.snapshot)

    def _stamp(self, snapshot):
        if (not isinstance(snapshot, dict) or snapshot.get('revoked') is not False
                or type(snapshot.get('generation')) is not int or snapshot['generation'] <= 0
                or type(snapshot.get('account_id')) is not int or snapshot['account_id'] <= 0):
            _fail('unified_session_unavailable')
        auth = {'workspace': str(self.workspace), 'generation': snapshot['generation'],
                'accountId': snapshot['account_id'], 'storageState': snapshot.get('storage_state')}
        digest = hashlib.sha256(json.dumps(auth, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        return {'xhsSessionGeneration': snapshot['generation'], 'xhsNativeAccountId': snapshot['account_id'],
                'xhsSessionHash': digest}

    def authorize(self):
        from .account_sessions import load_session
        if self._stamp(load_session(self.workspace, 'xhs')) != self.stamp:
            _fail('unified_session_changed')


class ReadOnlyClient:
    """Reuse existing signing clients under one immutable login-snapshot fence."""
    def __init__(self, creator, main, *, authorize, snapshot_stamp):
        self._creator, self._main, self._authorize = creator, main, authorize
        self._stamp = _stamp(snapshot_stamp)
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='xhs-readback')

    @property
    def snapshot_stamp(self):
        self._authorize()
        return dict(self._stamp)

    @classmethod
    def from_storage_state(cls, state, *, proxy='', workspace=None):
        workspace = Path(workspace or os.environ.get('EASEL_ROOT') or Path.cwd())
        fence = _SessionFence(workspace)
        return cls._from_fenced_state(state, fence, proxy=proxy)

    @classmethod
    def _from_fenced_state(cls, state, fence, *, proxy=''):
        # One frozen source must own both credentials and the generation stamp.
        # Re-loading it into a new fence could attach old auth to a new account.
        fence.authorize()
        from .vendor.creatorhub.platforms.xhs import creator_api
        from .vendor.creatorhub.platforms.xhs.client import XhsApiClient
        from .vendor.creatorhub.platforms.xhs.cookies import cookie_str_from_state, has_a1, has_creator_cookies
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
        cookies = cookie_str_from_state(encoded)
        if not has_a1(cookies) or not has_creator_cookies(encoded):
            _fail('unified_session_unavailable')
        current_encoded = json.dumps(fence.snapshot['storage_state'], ensure_ascii=False, allow_nan=False)
        current = cookie_str_from_state(current_encoded)
        if not has_a1(current) or not has_creator_cookies(current_encoded):
            _fail('unified_session_unavailable')
        supplied_pairs = dict(pair.split('=', 1) for pair in cookies.split('; '))
        current_pairs = dict(pair.split('=', 1) for pair in current.split('; ') if pair)
        stable = {'galaxy_creator_session_id', 'customer-sso-sid'} & current_pairs.keys()
        required = ({'a1', 'web_session'} & current_pairs.keys()) | stable
        if not stable:
            required.add('access-token-creator.xiaohongshu.com')
        if any(not current_pairs.get(key) or supplied_pairs.get(key) != current_pairs[key] for key in required):
            _fail('browser_session_source_changed')
        creator = creator_api.XhsCreatorApi(cookies, timeout=6, proxy=proxy or '')
        try:
            # These are instance-local transports, not changes to the shared vendor.
            creator.cli.trust_env = False
            main = XhsApiClient(cookies, creator_api._UA, timeout=6, proxy=proxy or '')
            session_type = main._session_cls

            def bounded_session(**kwargs):
                return session_type(**{**kwargs, 'trust_env': False, 'allow_redirects': False})

            main._session_cls = bounded_session
            fence.authorize()
            return cls(creator, main, authorize=fence.authorize, snapshot_stamp=fence.stamp)
        except BaseException:
            creator.close()
            raise

    @classmethod
    def from_current_session(cls, workspace, *, proxy=''):
        fence = _SessionFence(workspace)
        return cls._from_fenced_state(fence.snapshot['storage_state'], fence, proxy=proxy)

    def identity(self):
        self._authorize()
        ok, data = self._creator.my_info()
        self._authorize()
        if ok is not True or not isinstance(data, dict):
            _fail('creator_identity_unavailable')
        return data

    def creator_notes(self):
        self._authorize()
        from .vendor.creatorhub.platforms.xhs import creator_api
        from .vendor.creatorhub.platforms.xhs.responses import parse_response
        path = creator_api.sign.splice_str(_CREATOR_POSTED, {'tab': '1', 'page': 0})
        headers = creator_api._common_headers()
        headers.update(creator_api.sign.generate_xsc(self._creator.a1, path))
        response = self._creator._request('get', creator_api.CREATOR_URL + path,
            headers=headers, cookies=self._creator.cookies)
        payload = parse_response(response)
        self._authorize()
        data = payload.get('data')
        if not isinstance(data, dict) or 'notes' not in data:
            _fail('creator_notes_unavailable')
        return _notes(data['notes'])

    def own_notes(self, uid):
        self._authorize()

        async def read():
            return await self._main.notes_by_creator(uid, cursor='', page_size=30,
                xsec_token='', xsec_source='pc_feed')

        # The sync publisher can run inside Playwright's greenlet event loop.
        # This owned worker never borrows another task's async session or loop.
        data = self._worker.submit(lambda: asyncio.run(read())).result()
        self._authorize()
        if not isinstance(data, dict) or 'notes' not in data:
            _fail('main_notes_unavailable')
        return _notes(data['notes'])

    def close(self):
        try:
            self._creator.close()
        finally:
            self._worker.shutdown(wait=True, cancel_futures=True)
