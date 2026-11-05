"""Real unified-session fences and SDK GET seams; never contact a provider."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from easel import account_sessions
from easel.xhs_publication_readback import ReadOnlyClient, XhsReadbackError, capture_baseline


UID = 'fixture-owner'
NOTE = '6ad86a4600000000330118ff'
CONTEXT = {'operationId': 'a' * 32, 'payloadHash': 'b' * 64,
           'platform': 'xiaohongshu', 'mode': 'publish_now',
           'approvedAccountId': 'userId:' + UID}


def state(*, alternate=False):
    # Deliberately fictional credentials: only the SDK transports are mocked.
    value = 'fixture-b' if alternate else 'fixture-a'
    return {'cookies': [{'name': name, 'value': value, 'domain': '.xiaohongshu.com',
                         'path': '/', 'expires': -1, 'httpOnly': True, 'secure': True}
                        for name in ('a1', 'galaxy_creator_session_id', 'web_session')],
            'origins': []}


def save(workspace, snapshot=None, *, account_id=1, identity=None, capabilities=None):
    account_sessions.save_session(workspace, 'xhs', account_id, state() if snapshot is None else snapshot,
        identity=identity or {'userId': UID, 'name': 'Fixture owner'},
        capabilities=capabilities or {'creator': True})


@pytest.fixture
def transport(monkeypatch, tmp_path):
    from easel.vendor.creatorhub.platforms.xhs import creator_api, creator_sign
    import curl_cffi.requests

    workspace = tmp_path / 'owned workspace'
    workspace.mkdir()
    save(workspace)
    events, sync_sessions, async_sessions = [], [], []
    controls = {'on_request': None, 'creator_payload': None, 'main_payload': None,
                'creator_status': 200, 'main_status': 200}

    class Response:
        def __init__(self, payload, status=200):
            self.payload = payload
            self.status_code = status

        def json(self):
            return deepcopy(self.payload)

    class SyncSession:
        def __init__(self, **kwargs):
            self.trust_env = kwargs.get('trust_env', True)
            self.allow_redirects = kwargs.get('allow_redirects', True)
            self.closed = False
            sync_sessions.append(self)

        def get(self, url, **kwargs):
            if self.closed:
                raise RuntimeError('transport_closed')
            assert self.trust_env is False, 'readback inherited an environment proxy'
            assert self.allow_redirects is False, 'readback could forward its login to a redirect'
            parsed = urlsplit(url)
            assert parsed.scheme == 'https' and parsed.netloc == 'creator.xiaohongshu.com'
            events.append(('creator', parsed.path, parsed.query))
            if controls['on_request'] is not None:
                controls['on_request']()
            if parsed.path == '/api/galaxy/user/info':
                uid = UID if kwargs['cookies']['a1'] == 'fixture-a' else 'other-fixture-owner'
                return Response({'success': True, 'code': 0, 'data': {'userId': uid}})
            assert parsed.path == '/api/galaxy/v2/creator/note/user/posted'
            assert parsed.query == 'tab=1&page=0'
            payload = controls['creator_payload'] or {'success': True, 'code': 0, 'data': {
                'notes': [{'id': NOTE, 'permission_code': 0, 'tab_status': 1, 'time': '2026-11-05 10:00'}],
                'page': -1, 'tags': []}}
            return Response(payload, controls['creator_status'])

        def close(self):
            self.closed = True

    class AsyncSession:
        def __init__(self, **kwargs):
            self.closed = False
            self.options = kwargs
            async_sessions.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def get(self, url, **kwargs):
            assert self.options.get('trust_env') is False, 'main readback inherited an environment proxy'
            assert self.options.get('allow_redirects') is False, 'main readback allowed a redirect'
            assert self.options.get('discard_cookies') is True
            parsed = urlsplit(url)
            assert parsed.scheme == 'https' and parsed.netloc == 'edith.xiaohongshu.com'
            assert parsed.path == '/api/sns/web/v1/user_posted'
            query = parse_qs(parsed.query, keep_blank_values=True)
            assert query == {'num': ['30'], 'cursor': [''], 'user_id': [UID],
                'image_formats': ['jpg,webp,avif'], 'xsec_token': [''], 'xsec_source': ['pc_feed']}
            events.append(('main', threading.get_ident(), id(asyncio.get_running_loop())))
            if controls['on_request'] is not None:
                controls['on_request']()
            payload = controls['main_payload'] or {'success': True, 'code': 0, 'data': {
                'notes': [{'note_id': NOTE, 'user': {'user_id': UID}, 'time': 1_793_880_000_000}],
                'cursor': '', 'has_more': False}}
            return Response(payload, controls['main_status'])

    monkeypatch.setattr(creator_api, 'Session', SyncSession)
    monkeypatch.setattr(curl_cffi.requests, 'AsyncSession', AsyncSession)
    monkeypatch.setattr(creator_sign, 'generate_xsc', lambda *_: {})
    monkeypatch.setattr(creator_sign, 'generate_xsc_main', lambda *_: {})
    monkeypatch.setattr(creator_sign, 'available', lambda: True)
    monkeypatch.setenv('EASEL_ROOT', str(workspace))
    monkeypatch.setenv('HTTPS_PROXY', 'http://not-a-real-proxy.invalid:9999')
    return SimpleNamespace(workspace=workspace, events=events, controls=controls,
        sync_sessions=sync_sessions, async_sessions=async_sessions)


def test_actual_factory_reads_only_current_identity_and_fixed_modern_creator_get(transport):
    client = ReadOnlyClient.from_current_session(transport.workspace)
    try:
        assert client.identity() == {'userId': UID}
        assert client.creator_notes() == [{'id': NOTE, 'permission_code': 0,
            'tab_status': 1, 'time': '2026-11-05 10:00'}]
        assert transport.events == [('creator', '/api/galaxy/user/info', ''),
            ('creator', '/api/galaxy/v2/creator/note/user/posted', 'tab=1&page=0')]
        assert set(client.snapshot_stamp) == {'xhsSessionGeneration', 'xhsNativeAccountId', 'xhsSessionHash'}
    finally:
        client.close()
    assert all(session.closed for session in transport.sync_sessions)


def test_main_sdk_works_inside_running_loop_on_its_owned_thread_and_closes_transport(transport):
    client = ReadOnlyClient.from_current_session(transport.workspace)
    try:
        async def outer():
            outer_thread, outer_loop = threading.get_ident(), id(asyncio.get_running_loop())
            notes = client.own_notes(UID)
            assert notes == [{'note_id': NOTE, 'user': {'user_id': UID}, 'time': 1_793_880_000_000}]
            kind, thread_id, loop_id = transport.events[-1]
            assert kind == 'main' and thread_id != outer_thread and loop_id != outer_loop
        asyncio.run(outer())
        assert transport.async_sessions and all(session.closed for session in transport.async_sessions)
    finally:
        client.close()
    before = list(transport.events)
    with pytest.raises(RuntimeError):
        client.own_notes(UID)
    assert transport.events == before


@pytest.mark.parametrize('change', ['generation', 'revoked', 'account-id', 'auth-state'])
def test_shared_snapshot_changes_block_reads_before_they_can_contact_the_provider(transport, change):
    client = ReadOnlyClient.from_current_session(transport.workspace)
    try:
        if change == 'generation':
            account_sessions.begin_generation(transport.workspace, 'xhs')
            save(transport.workspace)
        elif change == 'revoked':
            account_sessions.revoke_session(transport.workspace, 'xhs')
        elif change == 'account-id':
            save(transport.workspace, account_id=2)
        else:
            save(transport.workspace, state(alternate=True))
        with pytest.raises(XhsReadbackError):
            client.identity()
        assert transport.events == []
    finally:
        client.close()


@pytest.mark.parametrize('method', ['identity', 'creator_notes', 'own_notes'])
def test_snapshot_changed_during_get_is_rejected_before_returning_evidence(transport, method):
    client = ReadOnlyClient.from_current_session(transport.workspace)
    transport.controls['on_request'] = lambda: account_sessions.revoke_session(transport.workspace, 'xhs')
    try:
        with pytest.raises(XhsReadbackError):
            getattr(client, method)(UID) if method == 'own_notes' else getattr(client, method)()
        assert len(transport.events) == 1
    finally:
        client.close()


def test_non_auth_profile_updates_do_not_invalidate_the_authorized_login(transport):
    client = ReadOnlyClient.from_current_session(transport.workspace)
    try:
        original = client.snapshot_stamp
        save(transport.workspace, identity={'userId': UID, 'name': 'Renamed owner'},
             capabilities={'creator': True, 'comments': True})
        assert client.identity() == {'userId': UID} and client.snapshot_stamp == original
    finally:
        client.close()


def test_caller_mutation_of_the_supplied_snapshot_cannot_rebind_existing_clients(transport):
    snapshot = state()
    client = ReadOnlyClient.from_storage_state(snapshot, workspace=transport.workspace)
    snapshot['cookies'][0]['value'] = 'fixture-b'
    snapshot['cookies'].clear()
    try:
        assert client.identity() == {'userId': UID}
    finally:
        client.close()


def test_same_device_cookie_does_not_authorize_a_different_creator_session(transport):
    snapshot = state()
    snapshot['cookies'][1]['value'] = 'fixture-b'
    with pytest.raises(XhsReadbackError):
        client = ReadOnlyClient.from_storage_state(snapshot, workspace=transport.workspace)
        try:
            client.identity()
        finally:
            client.close()
    assert transport.events == []


@pytest.mark.parametrize('unavailable', ['missing', 'expired'])
def test_incomplete_unified_auth_cannot_borrow_a_valid_a1_from_caller_state(transport, unavailable):
    shared = state()
    if unavailable == 'missing':
        shared['cookies'] = shared['cookies'][1:]
    else:
        shared['cookies'][0]['expires'] = 1
    save(transport.workspace, shared)
    with pytest.raises(XhsReadbackError):
        client = ReadOnlyClient.from_storage_state(state(), workspace=transport.workspace)
        try:
            client.identity()
        finally:
            client.close()
    assert transport.events == []


def test_dynamic_editor_storage_and_extra_non_auth_cookie_do_not_rebind_the_session(transport):
    snapshot = state()
    snapshot['origins'] = [{'origin': 'https://creator.xiaohongshu.com',
                            'localStorage': [{'name': 'draft-view', 'value': 'changed-ui-state'}]}]
    snapshot['cookies'].append({'name': 'client-build', 'value': 'changed-ui-build',
        'domain': 'creator.xiaohongshu.com', 'path': '/', 'expires': -1})
    client = ReadOnlyClient.from_storage_state(snapshot, workspace=transport.workspace)
    try:
        assert client.identity() == {'userId': UID}
    finally:
        client.close()


def test_rotating_access_cookie_does_not_replace_an_unchanged_stable_creator_session(transport):
    shared = state()
    extra = {'name': 'access-token-creator.xiaohongshu.com', 'value': 'fixture-a',
             'domain': 'creator.xiaohongshu.com', 'path': '/', 'expires': -1}
    shared['cookies'].append(extra)
    save(transport.workspace, shared)
    browser = deepcopy(shared)
    browser['cookies'][-1]['value'] = 'fixture-b'
    client = ReadOnlyClient.from_storage_state(browser, workspace=transport.workspace)
    try:
        assert client.identity() == {'userId': UID}
    finally:
        client.close()


@pytest.mark.parametrize('missing', ['a1', 'galaxy_creator_session_id'])
def test_missing_login_material_never_constructs_a_provider_transport(transport, missing):
    snapshot = state()
    snapshot['cookies'] = [cookie for cookie in snapshot['cookies'] if cookie['name'] != missing]
    with pytest.raises(XhsReadbackError):
        ReadOnlyClient.from_storage_state(snapshot, workspace=transport.workspace)
    assert transport.sync_sessions == [] and transport.async_sessions == [] and transport.events == []


@pytest.mark.parametrize('source', ['creator', 'main'])
@pytest.mark.parametrize('failure', ['missing-notes', 'malformed-notes', 'business-failed', 'redirect'])
def test_incomplete_or_rejected_sdk_response_cannot_become_a_successful_new_baseline(transport, source, failure):
    payload = {'success': True, 'code': 0, 'data': {}}
    if failure == 'malformed-notes':
        payload['data']['notes'] = {'unexpected': 'mapping'}
    elif failure == 'business-failed':
        payload.update(success=False, code=403)
        payload['data']['notes'] = []
    elif failure == 'redirect':
        payload['data']['notes'] = []
        transport.controls[source + '_status'] = 302
    transport.controls[source + '_payload'] = payload
    client = ReadOnlyClient.from_current_session(transport.workspace)
    try:
        with pytest.raises(XhsReadbackError):
            capture_baseline(client, CONTEXT, now_ms=lambda: 1_793_880_000_000)
    finally:
        client.close()
    assert all(session.closed for session in transport.sync_sessions + transport.async_sessions)


def test_absent_unified_session_cannot_borrow_caller_supplied_cookie_state(transport, tmp_path):
    other = tmp_path / 'not connected'
    other.mkdir()
    with pytest.raises(XhsReadbackError):
        ReadOnlyClient.from_storage_state(state(), workspace=other)
    assert transport.events == [] and transport.sync_sessions == []


def test_old_browser_auth_cannot_acquire_the_stamp_of_a_newly_active_unified_account(transport):
    old_browser_state = state()
    account_sessions.begin_generation(transport.workspace, 'xhs')
    save(transport.workspace, state(alternate=True), account_id=2)
    with pytest.raises(XhsReadbackError):
        client = ReadOnlyClient.from_storage_state(old_browser_state, workspace=transport.workspace)
        try:
            client.identity()
        finally:
            client.close()


def test_account_rotated_between_current_session_factory_reads_cannot_use_old_auth_under_new_stamp(transport, monkeypatch):
    original_load = account_sessions.load_session
    first = True

    def rotate_after_first_load(workspace, platform):
        nonlocal first
        snapshot = original_load(workspace, platform)
        if first:
            first = False
            account_sessions.begin_generation(workspace, platform)
            save(workspace, state(alternate=True), account_id=2)
        return snapshot

    monkeypatch.setattr(account_sessions, 'load_session', rotate_after_first_load)
    with pytest.raises(XhsReadbackError):
        client = ReadOnlyClient.from_current_session(transport.workspace)
        try:
            # Before the fix this read uses old credentials while the stamp is
            # already the newly activated account/generation.
            client.identity()
        finally:
            client.close()


def test_main_client_construction_failure_closes_the_already_created_creator_transport(transport, monkeypatch):
    from easel.vendor.creatorhub.platforms.xhs import client as main_module

    def broken_main(*args, **kwargs):
        raise RuntimeError('fictional_main_constructor_failure')

    monkeypatch.setattr(main_module, 'XhsApiClient', broken_main)
    with pytest.raises(Exception):
        ReadOnlyClient.from_current_session(transport.workspace)
    assert len(transport.sync_sessions) == 1 and transport.sync_sessions[0].closed
