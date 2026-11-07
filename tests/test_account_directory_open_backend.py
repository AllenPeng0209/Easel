"""Owner-only native backend opening; the runtime and every browser action are faked."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from easel.access import SESSION_COOKIE
from easel.account_sessions import begin_generation, save_session
from easel.request_identity import actor_context, current_user
from easel.projects import project_context
from tests.test_account_directory_web import host as _host, native, snapshot, login_marker, action_key, A, B
from web import app as web

host = _host
pytestmark = pytest.mark.access_integration
URLS = {'douyin': 'https://creator.douyin.com/creator-micro/content/manage',
        'xiaohongshu': 'https://creator.xiaohongshu.com/'}


def mutate(host, statement, values=()):
    with sqlite3.connect(host.root / '.easel/operations/operations.sqlite3') as db:
        db.execute(statement, values)


def backend(host, *, platform='douyin', open_action=None, get_action=None, manager_action=None,
            runtime_project='default', runtime_workspace=None):
    native(host.root)
    if platform == 'xiaohongshu':
        mutate(host, 'UPDATE douyinaccount SET platform=? WHERE id=1', ('xhs',))
        save_session(host.root, 'xhs', 1, {'cookies': [{'name': 'session', 'value': 'private-xhs-cookie',
            'domain': '.xiaohongshu.com', 'path': '/'}], 'origins': []},
            identity={'accountId': 'secure-alice', 'accountIdKind': 'user_id', 'accountIdentityStatus': 'verified'},
            capabilities={'creator': 'verified', 'publish': 'verified'})
    else:
        snapshot(host.root)
    opened = []

    async def fake_open(identifier, *, url, authorize):
        authorize()
        if open_action:
            await open_action(authorize)
        opened.append((identifier, url))
        return {'ok': True, 'cookies': 'private-vendor-cookie', 'cdp_url': 'ws://private-debugger/token'}

    module = SimpleNamespace(open_account_browser=AsyncMock(side_effect=fake_open))
    runtime = SimpleNamespace(project=runtime_project, workspace=runtime_workspace or host.root, module=module)

    async def get(project, *, start=True):
        assert project == 'default' and start is True
        if get_action:
            await get_action()
        return runtime

    manager = SimpleNamespace(root=host.root.resolve(), get=AsyncMock(side_effect=get))

    async def get_manager():
        if manager_action:
            await manager_action()
        return manager

    host.monkeypatch.setattr(web, '_operations_manager', AsyncMock(side_effect=get_manager))
    host.monkeypatch.setattr(web, '_publication_dir', lambda: host.root / '_private-publication-fixture')
    return SimpleNamespace(opened=opened, manager=manager, runtime=runtime, module=module)


@pytest.mark.parametrize('platform', ['douyin', 'xiaohongshu'])
def test_owner_opens_only_fixed_backend_for_the_verified_native_opaque_key_and_receives_safe_summary(host, platform):
    fake = backend(host, platform=platform)
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/open-backend',
        json={'url': 'https://untrusted.invalid/', 'account_id': 900, 'cookie': 'untrusted'})
    assert response.status_code == 200, response.text
    assert response.json() == {'opened': True, 'summary': '已打开账号后台'}
    assert fake.opened == [(1, URLS[platform])]
    assert 'private-' not in response.text and 'ws://' not in response.text
    fake.manager.get.assert_awaited_once_with('default')


@pytest.mark.parametrize('role', ['operator', 'viewer'])
def test_non_owner_cannot_initialize_runtime_or_open_an_account_backend(host, role):
    fake = backend(host)
    key = action_key(host, 1)
    user = host.access.create_user('backend-' + role, 'temporary-backend-user-password')
    host.access.set_member('default', user['id'], role)
    token = host.access.login('backend-' + role, 'temporary-backend-user-password')[1]
    host.client.cookies.set(SESSION_COOKIE, token)
    response = host.client.post('/api/accounts/directory/' + key + '/open-backend')
    assert response.status_code == 403
    fake.manager.get.assert_not_awaited()
    fake.module.open_account_browser.assert_not_awaited()


def test_legacy_unverified_uidless_and_wrong_ip_keys_never_start_a_runtime(host):
    fake = backend(host)
    key = action_key(host, 1)
    login_marker(host.root, 'xiaohongshu')
    for path in ['/api/accounts/directory/legacy%3Axiaohongshu/open-backend',
                 '/api/accounts/directory/native%3A1/open-backend',
                 f'/api/projects/{A}/accounts/directory/{key}/open-backend']:
        response = host.client.post(path)
        assert response.status_code in (404, 409)
    mutate(host, 'UPDATE douyinaccount SET uid=?,sec_uid=? WHERE id=1', ('bob', 'secure-bob'))
    response = host.client.post('/api/accounts/directory/' + key + '/open-backend')
    assert response.status_code in (404, 409)
    fresh_key = action_key(host, 1)
    response = host.client.post('/api/accounts/directory/' + fresh_key + '/open-backend')
    assert response.status_code == 409
    fake.manager.get.assert_not_awaited()


@pytest.mark.parametrize('runtime_project,runtime_workspace', [(B, None), ('default', 'other')])
def test_wrong_runtime_scope_is_rejected_before_vendor_open(host, runtime_project, runtime_workspace):
    workspace = host.root / 'projects' / B if runtime_workspace else None
    fake = backend(host, runtime_project=runtime_project, runtime_workspace=workspace)
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/open-backend')
    assert response.status_code == 409
    assert fake.opened == []
    fake.module.open_account_browser.assert_not_awaited()


@pytest.mark.parametrize('change', ['uid', 'status', 'auth', 'generation'])
def test_native_identity_logout_and_generation_changes_during_runtime_start_are_rejected(host, change):
    async def changed():
        if change == 'uid':
            mutate(host, 'UPDATE douyinaccount SET uid=?,sec_uid=? WHERE id=1', ('bob', 'secure-bob'))
        elif change == 'status':
            mutate(host, 'UPDATE douyinaccount SET status=? WHERE id=1', ('invalid',))
        elif change == 'auth':
            mutate(host, "UPDATE douyinaccount SET storage_state='',creator_storage_state='',cookie='' WHERE id=1")
        else:
            begin_generation(host.root, 'douyin')

    fake = backend(host, get_action=changed)
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/open-backend')
    assert response.status_code == 409
    fake.module.open_account_browser.assert_not_awaited()
    assert fake.opened == []


def test_permission_removed_while_loading_manager_cannot_start_runtime(host):
    actor = host.access.authenticate_token(host.client.cookies.get(SESSION_COOKIE))
    other = host.access.create_user('second-backend-owner', 'temporary-second-owner-password')
    host.access.set_member('default', other['id'], 'operator')

    async def changed():
        from tests.repository_helpers import transfer_project
        transfer_project(host.access, 'default', other['id'])

    fake = backend(host, manager_action=changed)
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/open-backend')
    assert response.status_code == 403
    fake.manager.get.assert_not_awaited()
    fake.module.open_account_browser.assert_not_awaited()


@pytest.mark.parametrize('change', ['generation', 'actor', 'actor_mutation', 'project', 'publication_lock'])
def test_sync_authorizer_rechecks_fences_inside_vendor_browser_lock(host, change):
    other = host.access.create_user('backend-race-owner', 'temporary-race-owner-password')
    host.access.set_member('default', other['id'], 'operator')

    async def changed(authorize):
        if change == 'generation':
            begin_generation(host.root, 'douyin')
            authorize()
        elif change == 'actor':
            with actor_context(other):
                authorize()
        elif change == 'actor_mutation':
            actor = current_user()
            original = actor['id']
            try:
                actor['id'] = other['id']
                authorize()
            finally:
                actor['id'] = original
        elif change == 'project':
            with project_context(A, host.root):
                authorize()
        else:
            def busy(_platform):
                raise HTTPException(409, 'Fixture publication profile busy')
            host.monkeypatch.setattr(web, '_require_publication_profile_idle', busy)
            authorize()

    fake = backend(host, open_action=changed)
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/open-backend')
    assert response.status_code in (403, 409)
    assert fake.opened == []


def test_publication_profile_busy_is_rejected_before_loading_manager(host):
    fake = backend(host)
    def busy(_platform):
        raise HTTPException(409, 'Fixture publication profile busy')
    host.monkeypatch.setattr(web, '_require_publication_profile_idle', busy)
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/open-backend')
    assert response.status_code == 409
    fake.manager.get.assert_not_awaited()


def test_logout_generation_changed_after_vendor_return_cannot_be_reported_as_opened(host):
    fake = backend(host)

    async def opened_then_changed(identifier, *, url, authorize):
        authorize()
        begin_generation(host.root, 'douyin')
        return {'ok': True, 'cookies': 'private-vendor-cookie'}

    fake.module.open_account_browser.side_effect = opened_then_changed
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/open-backend')
    assert response.status_code == 409
    assert 'opened' not in response.json() and 'private-' not in response.text


@pytest.mark.parametrize('error', [RuntimeError('private-cookie ws://private-cdp/token'),
                                  HTTPException(400, 'private-cookie ws://private-cdp/token')])
def test_vendor_errors_are_fixed_safe_failures_without_raw_details(host, error):
    async def failed(authorize):
        raise error
    fake = backend(host, open_action=failed)
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/open-backend')
    assert response.status_code == 409
    assert 'private' not in response.text and 'ws://' not in response.text
    assert fake.opened == []
