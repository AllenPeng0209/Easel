"""The unified directory uses real RBAC and never starts a browser on read."""
import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

from fastapi.testclient import TestClient
import pytest

from easel.access import AccessStore, SESSION_COOKIE
from easel.account_sessions import begin_generation, load_session, save_session
from easel.projects import ProjectMap, project_context
from web import app as web
from tests.repository_helpers import transfer_project

pytestmark = pytest.mark.access_integration
A, B = 'a' * 16, 'b' * 16


@pytest.fixture
def host(tmp_path, monkeypatch):
    for name, relative in {
        'PROJECT_ROOT': '.', 'OUTPUTS_DIR': 'outputs', 'PROFILES_DIR': 'profiles',
        'BROWSER_PROFILES': 'browser', 'LOGIN_DIR': 'outputs/_login',
        'PUBLISH_DIR': 'outputs/_publish', 'WECHAT_CONFIG_YAML': 'wechat-publisher.yaml',
        'SCHEDULE_FILE': 'outputs/_schedule.json',
    }.items():
        monkeypatch.setattr(web, name, tmp_path / relative)
    registry = [{'id': value, 'name': value} for value in ('default', A, B)]
    (tmp_path / 'projects').mkdir()
    (tmp_path / 'projects/_registry.json').write_text(json.dumps(registry))
    for value in (A, B):
        (tmp_path / 'projects' / value).mkdir()
    for name in ('_WHOAMI_CACHE', '_WHOAMI_SESSION_GENERATIONS', '_WHOAMI_TASKS',
                 'LOGIN_PROCESSES', '_UNIFIED_LOGIN_TASKS'):
        monkeypatch.setattr(web, name, ProjectMap())
    access = AccessStore(tmp_path)
    _, token = access.setup('account-owner', 'account-owner-password-42', project_ids=['default', A, B])
    client = TestClient(web.app, base_url='http://localhost:7860', headers={'Origin': 'http://localhost:7860'})
    client.cookies.set(SESSION_COOKIE, token)
    return SimpleNamespace(root=tmp_path, client=client, access=access, monkeypatch=monkeypatch)


def native(root, *, identifier=1, subject='alice'):
    database = root / '.easel/operations/operations.sqlite3'
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS douyinaccount (
            id INTEGER PRIMARY KEY, platform TEXT, nickname TEXT, avatar TEXT, status TEXT,
            uid TEXT, sec_uid TEXT, storage_state TEXT, creator_storage_state TEXT, cookie TEXT)''')
        db.execute('INSERT INTO douyinaccount VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (identifier, 'douyin', subject, '', 'active', subject, 'secure-' + subject,
                    'private-native-session', '', 'private-cookie'))


def snapshot(root, *, identifier=1, subject='alice'):
    save_session(root, 'douyin', identifier,
        {'cookies': [{'name': 'session', 'value': 'private-snapshot-cookie', 'domain': '.douyin.com', 'path': '/'}], 'origins': []},
        identity={'accountId': subject, 'accountIdKind': 'uid', 'accountIdentityStatus': 'verified'},
        capabilities={'creator': 'verified', 'publish': 'verified', 'comments': 'verified',
                      'analytics': 'verified', 'dm': 'needs_verification'})


def login_marker(root, platform):
    folder = root / 'outputs/_login'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (platform + '.json')).write_text(json.dumps({'state': 'success'}))


def action_key(host, identifier):
    rows = host.client.get('/api/accounts/directory').json()['accounts']
    return quote(next(row['key'] for row in rows if row['nativeAccountId'] == identifier), safe='')


def test_directory_keeps_catalog_but_does_not_create_a_runtime_or_unlogged_rows(host):
    host.monkeypatch.setattr(web, '_operations_manager', lambda: pytest.fail('Directory read initialized a runtime'))
    response = host.client.get('/api/accounts/directory')
    assert response.status_code == 200, response.text
    assert response.json()['platforms'] == host.client.get('/api/accounts').json()
    assert response.json()['accounts'] == []
    assert not (host.root / '.easel/operations').exists()


def test_managed_account_is_one_row_legacy_only_account_stays_visible_and_other_ips_are_empty(host):
    native(host.root)
    snapshot(host.root)
    login_marker(host.root, 'xiaohongshu')
    result = host.client.get('/api/accounts/directory')
    assert result.status_code == 200, result.text
    rows = result.json()['accounts']
    assert {row['nativeAccountId'] for row in rows} == {1, None}
    assert next(row for row in rows if row['nativeAccountId'] is None)['key'] == 'legacy:xiaohongshu'
    assert 'private-' not in result.text
    for identifier in (A, B):
        data = host.client.get(f'/api/projects/{identifier}/accounts/directory').json()
        assert data['project_id'] == identifier and data['accounts'] == []


def test_stale_generation_whoami_cannot_hide_another_saved_account(host):
    native(host.root)
    login_marker(host.root, 'douyin')
    with project_context('default', host.root):
        web._WHOAMI_CACHE['douyin'] = (time.time(), {'loggedIn': True, 'name': 'Alice',
            'accountId': 'alice', 'accountIdKind': 'uid', 'accountIdentityStatus': 'verified'})
        web._WHOAMI_SESSION_GENERATIONS['douyin'] = 99
    result = host.client.get('/api/accounts/directory')
    assert result.status_code == 200
    assert len(result.json()['accounts']) == 2


def test_corrupt_directory_returns_safe_error_instead_of_zero_accounts(host):
    path = host.root / '.easel/operations/operations.sqlite3'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'private-corrupt-auth-material')
    response = host.client.get('/api/accounts/directory')
    assert response.status_code == 503
    assert 'accounts' not in response.json()
    assert 'private-corrupt' not in response.text


def test_inactive_native_logout_never_clears_the_selected_content_profile(host):
    native(host.root, identifier=1)
    native(host.root, identifier=2, subject='bob')
    snapshot(host.root)
    profile = host.root / 'browser/DouyinProfile'
    profile.mkdir(parents=True)
    (profile / 'fixture').write_text('keep Alice content login')
    calls = []

    async def single(identifier, **_options):
        calls.append(identifier)
        return {'account_id': identifier, 'platform': 'douyin', 'shared_active': False}

    manager = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
        connections=SimpleNamespace(revoke_account=single, revoke=AsyncMock(side_effect=AssertionError('platform-wide revoke'))))))
    host.monkeypatch.setattr(web, '_operations_manager', AsyncMock(return_value=manager))
    response = host.client.post('/api/accounts/directory/' + action_key(host, 2) + '/logout')
    assert response.status_code == 200, response.text
    assert calls == [2] and (profile / 'fixture').is_file()
    assert host.client.post(f'/api/projects/{A}/accounts/directory/native%3A2/logout').status_code == 404


def test_legacy_only_logout_does_not_revoke_unrelated_native_accounts(host):
    native(host.root)
    login_marker(host.root, 'xiaohongshu')
    profile = host.root / 'browser/XiaohongshuProfile'
    profile.mkdir(parents=True)
    host.monkeypatch.setattr(web, '_operations_manager', lambda: pytest.fail('Legacy-only logout touched native runtime'))
    response = host.client.post('/api/accounts/directory/legacy%3Axiaohongshu/logout')
    assert response.status_code == 200, response.text
    assert not profile.exists()
    assert any(row['nativeAccountId'] == 1 for row in host.client.get('/api/accounts/directory').json()['accounts'])


def test_viewers_can_read_but_cannot_launch_verify_or_logout(host):
    login_marker(host.root, 'xiaohongshu')
    user = host.access.create_user('account-viewer', 'account-viewer-password-42')
    host.access.set_member('default', user['id'], 'viewer')
    token = host.access.login('account-viewer', 'account-viewer-password-42')[1]
    host.client.cookies.set(SESSION_COOKIE, token)
    assert host.client.get('/api/accounts/directory').status_code == 200
    for action in ('logout', 'verify'):
        assert host.client.post('/api/accounts/directory/legacy%3Axiaohongshu/' + action).status_code == 403


def test_native_logout_cannot_erase_a_newer_shared_account_after_await(host):
    native(host.root)
    native(host.root, identifier=2, subject='bob')
    snapshot(host.root)
    profile = host.root / 'browser/DouyinProfile'
    profile.mkdir(parents=True)

    async def single(identifier, **_options):
        begin_generation(host.root, 'douyin')  # Alice revocation
        begin_generation(host.root, 'douyin')  # Bob starts after that fence
        snapshot(host.root, identifier=2, subject='bob')
        (profile / 'new-login').write_text('Bob profile')
        web._UNIFIED_LOGIN_TASKS['douyin'] = 'bob-task'
        return {'account_id': identifier, 'platform': 'douyin', 'shared_active': True}

    manager = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(connections=SimpleNamespace(revoke_account=single))))
    host.monkeypatch.setattr(web, '_operations_manager', AsyncMock(return_value=manager))
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/logout')
    assert response.status_code == 200, response.text
    assert (profile / 'new-login').is_file()
    assert web._UNIFIED_LOGIN_TASKS['douyin'] == 'bob-task'
    assert load_session(host.root, 'douyin')['account_id'] == 2


def test_legacy_row_with_conflicting_shared_native_subject_is_not_falsely_logged_out(host):
    native(host.root, subject='bob')
    snapshot(host.root, subject='alice')
    profile = host.root / 'browser/DouyinProfile'
    profile.mkdir(parents=True)
    (profile / 'fixture').write_text('Alice content login')
    response = host.client.post('/api/accounts/directory/legacy%3Adouyin/logout')
    assert response.status_code == 409
    assert (profile / 'fixture').is_file()
    assert load_session(host.root, 'douyin')['identity']['accountId'] == 'alice'


def test_native_exit_clears_the_same_verified_unmigrated_legacy_identity(host):
    native(host.root)
    login_marker(host.root, 'douyin')
    web._WHOAMI_CACHE['douyin'] = (time.time(), {'loggedIn': True, 'name': 'Alice', 'accountId': 'alice',
        'accountIdKind': 'uid', 'accountIdentityStatus': 'verified'})
    web._WHOAMI_SESSION_GENERATIONS['douyin'] = 0
    profile = host.root / 'browser/DouyinProfile'
    profile.mkdir(parents=True)
    (profile / 'fixture').write_text('Alice content login')
    connection = SimpleNamespace(revoke_account=AsyncMock(return_value={
        'account_id': 1, 'platform': 'douyin', 'shared_active': False}))
    manager = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(connections=connection)))
    host.monkeypatch.setattr(web, '_operations_manager', AsyncMock(return_value=manager))
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/logout')
    assert response.status_code == 200
    assert not profile.exists()


def test_permission_removed_while_loading_runtime_cannot_start_native_revocation(host):
    native(host.root)
    snapshot(host.root)
    actor = host.access.authenticate_token(host.client.cookies.get(SESSION_COOKIE))
    other = host.access.create_user('second-owner', 'second-owner-password-42')
    host.access.set_member('default', other['id'], 'operator')
    connection = SimpleNamespace(revoke_account=AsyncMock())

    async def changed_manager():
        transfer_project(host.access, 'default', other['id'])
        return SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(connections=connection)))

    host.monkeypatch.setattr(web, '_operations_manager', changed_manager)
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/logout')
    assert response.status_code == 403
    connection.revoke_account.assert_not_awaited()
    assert load_session(host.root, 'douyin')['account_id'] == 1


def test_an_old_native_row_key_cannot_exit_a_replaced_platform_subject(host):
    native(host.root)
    old = action_key(host, 1)
    database = host.root / '.easel/operations/operations.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('UPDATE douyinaccount SET uid=?,sec_uid=?,nickname=? WHERE id=1', ('bob', 'secure-bob', 'Bob'))
    connection = SimpleNamespace(revoke_account=AsyncMock(return_value={'shared_active': False}))
    manager = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(connections=connection)))
    host.monkeypatch.setattr(web, '_operations_manager', AsyncMock(return_value=manager))
    response = host.client.post('/api/accounts/directory/' + old + '/logout')
    assert response.status_code in (404, 409)
    connection.revoke_account.assert_not_awaited()


def test_an_old_verified_legacy_key_cannot_exit_the_newly_verified_person(host):
    login_marker(host.root, 'douyin')
    identity = {'loggedIn': True, 'name': 'Alice', 'accountId': 'alice', 'accountIdKind': 'uid', 'accountIdentityStatus': 'verified'}
    web._WHOAMI_CACHE['douyin'] = (time.time(), identity)
    web._WHOAMI_SESSION_GENERATIONS['douyin'] = 0
    old = host.client.get('/api/accounts/directory').json()['accounts'][0]['key']
    web._WHOAMI_CACHE['douyin'] = (time.time(), {**identity, 'name': 'Bob', 'accountId': 'bob'})
    profile = host.root / 'browser/DouyinProfile'
    profile.mkdir(parents=True)
    (profile / 'new-person').write_text('Bob profile')
    response = host.client.post('/api/accounts/directory/' + quote(old, safe='') + '/logout')
    assert response.status_code in (404, 409)
    assert (profile / 'new-person').is_file()


def test_subject_changed_while_loading_runtime_is_rejected_inside_native_authorization(host):
    native(host.root)
    old = action_key(host, 1)
    invoked = []

    async def single(identifier, *, authorize):
        authorize()
        invoked.append(identifier)
        return {'account_id': identifier, 'platform': 'douyin', 'shared_active': False}

    async def changed_manager():
        with sqlite3.connect(host.root / '.easel/operations/operations.sqlite3') as db:
            db.execute('UPDATE douyinaccount SET uid=?,sec_uid=? WHERE id=1', ('bob', 'secure-bob'))
        return SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(connections=SimpleNamespace(revoke_account=single))))

    host.monkeypatch.setattr(web, '_operations_manager', changed_manager)
    response = host.client.post('/api/accounts/directory/' + old + '/logout')
    assert response.status_code in (404, 409)
    assert invoked == []


def test_a_newer_login_during_old_process_wait_preserves_its_profile_and_task_map(host):
    native(host.root)
    native(host.root, identifier=2, subject='bob')
    snapshot(host.root)
    profile = host.root / 'browser/DouyinProfile'
    profile.mkdir(parents=True)

    async def single(identifier, **_options):
        begin_generation(host.root, 'douyin')
        return {'account_id': identifier, 'platform': 'douyin', 'shared_active': True}

    def wait(timeout):
        assert timeout == 5
        begin_generation(host.root, 'douyin')
        snapshot(host.root, identifier=2, subject='bob')
        (profile / 'new-person').write_text('Bob profile')
        web._UNIFIED_LOGIN_TASKS['douyin'] = 'newer-bob-task'

    web.LOGIN_PROCESSES['douyin'] = SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=wait)
    manager = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(connections=SimpleNamespace(revoke_account=single))))
    host.monkeypatch.setattr(web, '_operations_manager', AsyncMock(return_value=manager))
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/logout')
    assert response.status_code == 200
    assert (profile / 'new-person').is_file()
    assert web._UNIFIED_LOGIN_TASKS['douyin'] == 'newer-bob-task'


def test_active_native_exit_clears_only_its_old_compatibility_slot(host):
    native(host.root)
    snapshot(host.root)
    profile = host.root / 'browser/DouyinProfile'
    profile.mkdir(parents=True)
    (profile / 'old-person').write_text('Alice profile')

    async def single(identifier, **_options):
        begin_generation(host.root, 'douyin')
        return {'account_id': identifier, 'platform': 'douyin', 'shared_active': True}

    manager = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(connections=SimpleNamespace(revoke_account=single))))
    host.monkeypatch.setattr(web, '_operations_manager', AsyncMock(return_value=manager))
    response = host.client.post('/api/accounts/directory/' + action_key(host, 1) + '/logout')
    assert response.status_code == 200
    assert not profile.exists()


def test_legacy_verification_refreshes_identity_in_the_same_directory_without_native_creation(host):
    login_marker(host.root, 'xiaohongshu')

    async def verify(platform, force):
        assert platform == 'xiaohongshu' and force is True
        identity = {'loggedIn': True, 'name': 'Alice XHS', 'avatar': '', 'accountId': 'alice',
                    'accountIdKind': 'userId', 'accountIdentityStatus': 'verified'}
        web._WHOAMI_CACHE[platform] = (time.time(), identity)
        web._WHOAMI_SESSION_GENERATIONS[platform] = 0
        return identity

    host.monkeypatch.setattr(web, 'api_account_whoami', verify)
    host.monkeypatch.setattr(web, '_operations_manager', lambda: pytest.fail('Legacy verification created native runtime'))
    result = host.client.post('/api/accounts/directory/legacy%3Axiaohongshu/verify')
    assert result.status_code == 200, result.text
    row, = result.json()['accounts']
    assert row['nativeAccountId'] is None and row['identityVerified']
    assert row['name'] == 'Alice XHS' and row['key'].startswith('legacy:xiaohongshu:')
    assert row['capabilities']['comments'] != 'verified'
