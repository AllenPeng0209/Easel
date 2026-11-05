"""Browser responses queued before logout must not restore account auth."""
import json
from importlib import import_module
from types import SimpleNamespace

import pytest
from sqlmodel import select

from easel.operations import OperationsManager


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
async def runtime(tmp_path):
    manager = OperationsManager(tmp_path)
    runtime = await manager.get('default', start=False)
    yield runtime
    await manager.close()


def snapshot(value):
    return {'cookies': [{'name': 'sessionid', 'value': value, 'domain': '.douyin.com', 'path': '/'}],
            'origins': []}


def saved_account(runtime, *, status='active', auth='storage_state'):
    path = runtime.directory / 'profiles' / 'account-one'
    path.mkdir(parents=True, exist_ok=True)
    values = {} if auth is None else {auth: 'fixture-old-cookie' if auth == 'cookie' else json.dumps(snapshot('fixture-old-auth'))}
    with runtime.module.get_session() as db:
        db.add(runtime.module.DouyinAccount(id=1, platform='douyin', uid='100', sec_uid='secure-100',
            status=status, profile_dir=str(path), ua='original-user-agent', identity_mode='native', **values))
        db.commit()
    return SimpleNamespace(account_id=1, profile_dir=str(path), ua='current-user-agent')


def observe(runtime, identity):
    return runtime.module._persist_douyin_read_session(identity,
        {'uid': '100', 'sec_uid': 'secure-100'}, snapshot('fixture-new-auth'),
        {'ua': 'current-user-agent', 'version': 1, 'saved': True})


@pytest.mark.anyio
@pytest.mark.parametrize('status,auth', [('invalid', 'storage_state'), ('invalid', None), ('active', None)])
async def test_invalid_or_cleared_accounts_cannot_be_restored_by_a_delayed_browser_response(runtime, status, auth):
    identity = saved_account(runtime, status=status, auth=auth)
    with runtime.module.get_session() as db:
        before = db.get(runtime.module.DouyinAccount, 1).model_dump()
    assert observe(runtime, identity) is False
    with runtime.module.get_session() as db:
        after = db.get(runtime.module.DouyinAccount, 1).model_dump()
        assert after == before
        assert db.exec(select(import_module(runtime.namespace + '.models').AppSetting)).all() == []


@pytest.mark.anyio
@pytest.mark.parametrize('auth', ['storage_state', 'creator_storage_state', 'cookie'])
async def test_active_accounts_with_existing_auth_can_keep_verified_cookie_rotations(runtime, auth):
    identity = saved_account(runtime, auth=auth)
    assert observe(runtime, identity) is True
    with runtime.module.get_session() as db:
        account = db.get(runtime.module.DouyinAccount, 1)
        assert account.status == 'active'
        assert account.ua == 'current-user-agent'
        assert json.loads(account.storage_state) == snapshot('fixture-new-auth')
        stored = db.exec(select(import_module(runtime.namespace + '.models').AppSetting)).all()
        assert len(stored) == 1
        assert 'saved' not in json.loads(stored[0].value)


@pytest.mark.anyio
async def test_a_response_from_before_single_account_logout_cannot_revive_its_cookie(runtime):
    identity = saved_account(runtime)
    await runtime.connections.revoke_account(1)
    assert observe(runtime, identity) is False
    with runtime.module.get_session() as db:
        account = db.get(runtime.module.DouyinAccount, 1)
        assert account.status == 'invalid'
        assert account.storage_state == account.creator_storage_state == account.cookie == ''
