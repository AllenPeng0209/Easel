"""Exercise the existing vendor opener with finite fake contexts, never Chromium."""
import ast
import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
import inspect
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


@pytest.fixture
def opener(monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'easel/vendor/creatorhub/main.py'
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef))
             and node.name in {'_OpenBrowserLease', '_release_open_browser', 'open_account_browser'}]
    for node in nodes:
        node.decorator_list = []
    account = SimpleNamespace(id=1, platform='xhs', uid='user-a', sec_uid='user-a', status='active',
        storage_state=json.dumps({'cookies': []}), creator_storage_state='', profile_dir='owned-profile')
    events = []; hooks = {}; authority = {'valid': True}
    page = SimpleNamespace(is_closed=lambda: False, bring_to_front=AsyncMock(), wait_for_timeout=AsyncMock(), on=lambda *_: None)
    async def navigate(*_args, **_kwargs):
        events.append('navigate')
        if hooks.get('navigate'): hooks['navigate']()
    page.goto = navigate
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), add_cookies=AsyncMock(), on=lambda *_: None)
    browser = SimpleNamespace(_contexts={}, identity_for=lambda _: SimpleNamespace(account_id=1, key='acc:1'))
    @asynccontextmanager
    async def account_lock(_key):
        events.append('lock')
        if hooks.get('lock'): hooks['lock']()
        try: yield
        finally: events.append('unlock')
    @asynccontextmanager
    async def visible(*_args, **_kwargs):
        try: yield
        finally:
            if hooks.get('visible_exit'): hooks['visible_exit']()
    async def open_headed(identity):
        events.append('open')
        browser._contexts[identity.key] = context
        if hooks.get('open'): hooks['open']()
        return context
    async def close_context(key):
        events.append('close')
        browser._contexts.pop(key, None)
    browser.lock_for = account_lock; browser.visible_action = visible
    browser.open_headed = open_headed; browser.close_context = close_context
    browser.new_page = AsyncMock(return_value=page)
    database = SimpleNamespace(get=lambda *_: account, add=lambda *_: events.append('db-add'), commit=lambda: events.append('db-commit'))
    @contextmanager
    def session(): yield database
    namespace = {'__name__': '_backend_fixture.main', '__package__': '_backend_fixture',
        'asyncio': asyncio, 'asynccontextmanager': asynccontextmanager, 'suppress': suppress, 'HTTPException': HTTPException,
        'json': json, 'urlsplit': __import__('urllib.parse', fromlist=['urlsplit']).urlsplit,
        'browser': browser, 'engine': None, 'open_browsers': {}, 'get_session': session, 'DouyinAccount': object,
        '_xhs_has_read_login_state': lambda _: True, '_platform_url_allowed': lambda *_: True,
        '_opened_page_login_state': AsyncMock(return_value='authenticated')}
    manager = ModuleType('_backend_fixture.browser.manager'); manager._sanitize_cookies = lambda value: value
    monkeypatch.setitem(__import__('sys').modules, '_backend_fixture.browser.manager', manager)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    def authorize():
        if not authority['valid']: raise HTTPException(403, 'owner permission changed')
        if account.uid != 'user-a' or account.status != 'active': raise HTTPException(409, 'account identity changed')
    return SimpleNamespace(run=namespace['open_account_browser'], namespace=namespace, account=account,
        events=events, hooks=hooks, authority=authority, context=context, page=page, browser=browser, authorize=authorize)


def require_callback(opener):
    assert 'authorize' in inspect.signature(opener.run).parameters, 'Existing opener needs a scoped Host authority callback'


def test_legacy_opener_without_callback_retains_its_existing_contract(opener):
    async def scenario():
        result = await opener.run(1, url='https://creator.xiaohongshu.com/')
        assert result['ok'] is True and opener.namespace['open_browsers'][1].active
        await opener.namespace['open_browsers'][1].close()
    asyncio.run(scenario())


@pytest.mark.parametrize('drift', ['role', 'uid', 'logout'])
def test_drift_during_open_is_checked_before_cookie_injection_and_closes_only_new_context(opener, drift):
    require_callback(opener)
    def change():
        if drift == 'role': opener.authority['valid'] = False
        elif drift == 'uid': opener.account.uid = 'replacement-user'
        else: opener.account.status = 'invalid'
    opener.hooks['open'] = change
    async def scenario():
        with pytest.raises(HTTPException) as error:
            await opener.run(1, url='https://creator.xiaohongshu.com/', authorize=opener.authorize)
        assert error.value.status_code == (403 if drift == 'role' else 409)
        assert opener.events.count('open') == opener.events.count('close') == 1
        assert opener.context.add_cookies.await_count == 0
        assert 'navigate' not in opener.events and 'db-commit' not in opener.events
        assert opener.namespace['open_browsers'] == {}
    asyncio.run(scenario())


def test_role_loss_waiting_for_account_lock_does_not_open_a_context(opener):
    require_callback(opener)
    opener.hooks['lock'] = lambda: opener.authority.update(valid=False)
    async def scenario():
        with pytest.raises(HTTPException) as error:
            await opener.run(1, url='https://creator.xiaohongshu.com/', authorize=opener.authorize)
        assert error.value.status_code == 403
        assert 'open' not in opener.events and opener.events[-1] == 'unlock'
    asyncio.run(scenario())


def test_navigation_drift_cannot_write_status_or_register_a_new_lease(opener):
    require_callback(opener)
    opener.hooks['navigate'] = lambda: opener.authority.update(valid=False)
    async def scenario():
        with pytest.raises(HTTPException) as error:
            await opener.run(1, url='https://creator.xiaohongshu.com/', authorize=opener.authorize)
        assert error.value.status_code == 403
        assert 'db-commit' not in opener.events and 'close' in opener.events
        assert opener.namespace['open_browsers'] == {}
    asyncio.run(scenario())


def test_lease_registration_rechecks_after_releasing_startup_guard(opener):
    require_callback(opener)
    opener.hooks['visible_exit'] = lambda: opener.authority.update(valid=False)
    async def scenario():
        with pytest.raises(HTTPException):
            await opener.run(1, url='https://creator.xiaohongshu.com/', authorize=opener.authorize)
        assert 'close' in opener.events and opener.namespace['open_browsers'] == {}
    asyncio.run(scenario())


def test_reused_user_window_is_not_closed_or_replaced_on_authority_failure(opener):
    require_callback(opener)
    prior = SimpleNamespace(active=True, page=opener.page, context=opener.context, close=AsyncMock())
    opener.namespace['open_browsers'][1] = prior
    opener.browser._contexts['acc:1'] = opener.context
    opener.hooks['navigate'] = lambda: opener.authority.update(valid=False)
    async def scenario():
        with pytest.raises(HTTPException) as error:
            await opener.run(1, url='https://creator.xiaohongshu.com/', authorize=opener.authorize)
        assert error.value.status_code == 403
        assert prior.close.await_count == 0 and 'close' not in opener.events
        assert opener.namespace['open_browsers'][1] is prior
    asyncio.run(scenario())


def test_existing_background_context_is_not_closed_on_authority_failure(opener):
    require_callback(opener)
    opener.browser._contexts['acc:1'] = opener.context
    opener.hooks['open'] = lambda: opener.authority.update(valid=False)
    async def scenario():
        with pytest.raises(HTTPException) as error:
            await opener.run(1, url='https://creator.xiaohongshu.com/', authorize=opener.authorize)
        assert error.value.status_code == 403
        assert 'close' not in opener.events
        assert opener.browser._contexts['acc:1'] is opener.context
        assert opener.namespace['open_browsers'] == {}
    asyncio.run(scenario())


def test_reused_douyin_window_rechecks_after_login_probe_before_writing_status(opener):
    require_callback(opener)
    opener.account.platform = 'douyin'
    prior = SimpleNamespace(active=True, page=opener.page, context=opener.context, close=AsyncMock())
    opener.namespace['open_browsers'][1] = prior
    async def probe(*_args):
        opener.authority['valid'] = False
        return 'logged_out'
    opener.namespace['_opened_page_login_state'] = probe
    async def scenario():
        with pytest.raises(HTTPException) as error:
            await opener.run(1, url='https://creator.douyin.com/creator-micro/content/manage', authorize=opener.authorize)
        assert error.value.status_code == 403
        assert 'db-commit' not in opener.events and prior.close.await_count == 0
    asyncio.run(scenario())
