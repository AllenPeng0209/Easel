"""Publishing identity comes from the authenticated platform, never its label."""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/shared/scripts'))
import bili_login
import douyin_publish
import web_publisher
import weixin_mp_stats
import xhs_publish


UNKNOWN = {'accountId': '', 'accountIdKind': '', 'accountIdentityStatus': 'unknown'}


def binding(module, value, *args):
    function = getattr(module, '_account_binding', None)
    assert callable(function), 'whoami needs an authenticated stable-account binding'
    return function(value, *args)


def test_xhs_binding_uses_own_user_id_and_never_uses_display_label():
    assert binding(xhs_publish, {'userId': 'own-id', 'userName': 'Mutable nickname'}) == {
        'accountId': 'own-id', 'accountIdKind': 'userId', 'accountIdentityStatus': 'verified'}
    for value in [{'userId': '', 'userName': 'A label'}, {'userId': '../bad'}, {'userId': True}, {'userId': '0'}, {}]:
        assert binding(xhs_publish, value) == UNKNOWN


@pytest.mark.parametrize('user,identifier,kind', [
    ({'uid': '123', 'sec_uid': 'MS4secondary'}, '123', 'uid'),
    ({'sec_uid': 'MS4secondary'}, 'MS4secondary', 'sec_uid'),
    ({'open_id': 'application-scoped-id'}, 'application-scoped-id', 'open_id'),
])
def test_douyin_binding_keeps_actual_identifier_namespace(user, identifier, kind):
    response = {'httpStatus': 200, 'body': {'status_code': 0, 'user_info': {**user, 'nickname': 'Me'}}}
    assert binding(douyin_publish, response) == {'accountId': identifier, 'accountIdKind': kind, 'accountIdentityStatus': 'verified'}


@pytest.mark.parametrize('response', [
    {'httpStatus': 401, 'body': {'status_code': 0, 'user_info': {'uid': 'own'}}},
    {'httpStatus': 200, 'body': {'status_code': 1002, 'user_info': {'uid': 'own'}}},
    {'httpStatus': 200, 'body': {'status_code': 0, 'user_info': {'nickname': 'Mutable name'}}},
    {'httpStatus': 200, 'body': {'status_code': 0, 'user_info': {'uid': True}}},
    {'httpStatus': 200, 'body': {'status_code': 0, 'user_info': {'uid': '0'}}},
])
def test_douyin_unverified_or_malformed_identity_stays_unknown(response):
    assert binding(douyin_publish, response) == UNKNOWN


def test_bilibili_binding_requires_logged_in_nav_mid():
    assert binding(bili_login, {'isLogin': True, 'mid': 123, 'uname': 'Me'}) == {
        'accountId': '123', 'accountIdKind': 'mid', 'accountIdentityStatus': 'verified'}
    for value in [{'isLogin': False, 'mid': 123}, {'isLogin': True, 'mid': True}, {'isLogin': True, 'mid': 0},
                  {'isLogin': True, 'uname': 'Name only'}, {'isLogin': 'true', 'mid': 123}]:
        assert binding(bili_login, value) == UNKNOWN


@pytest.mark.parametrize('platform,kind', [('zhihu', 'id'), ('weixin-channels', 'finderUsername')])
def test_web_account_binding_uses_verified_native_account_id(platform, kind):
    assert binding(web_publisher, {'id': 'own-native-id', 'name': 'Me'}, platform) == {
        'accountId': 'own-native-id', 'accountIdKind': kind, 'accountIdentityStatus': 'verified'}
    assert binding(web_publisher, {'name': 'Me'}, platform) == UNKNOWN


def test_kuaishou_dom_display_is_not_fabricated_into_an_account_id():
    assert binding(web_publisher, {'id': 'not-current-user-proof', 'name': 'Me'}, 'kuaishou') == UNKNOWN


def fake_playwright(monkeypatch, browser):
    instance = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *args, **kwargs: browser))
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', SimpleNamespace(sync_playwright=lambda: contextlib.nullcontext(instance)))


def result(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_xhs_whoami_emits_stable_id_without_changing_visible_identity(monkeypatch, capsys):
    browser = SimpleNamespace(pages=[object()], close=lambda: None)
    fake_playwright(monkeypatch, browser)
    monkeypatch.setattr(xhs_publish, '_ProfileLock', lambda *_: SimpleNamespace(acquire=lambda *_: None, release=lambda: None))
    monkeypatch.setattr(xhs_publish, '_launch', lambda *args, **kwargs: browser)
    monkeypatch.setattr(xhs_publish, '_authenticated_account', lambda *_: {
        'userId': 'own-xhs', 'userName': 'Me', 'userAvatar': 'https://self.test/avatar'})
    xhs_publish.cmd_whoami(SimpleNamespace(profile_base=None, proxy=None, no_proxy=True))
    data = result(capsys)
    assert data['loggedIn'] is True and data['name'] == 'Me'
    assert data.get('accountId') == 'own-xhs'
    assert data.get('accountIdKind') == 'userId'


def test_bilibili_whoami_uses_nav_mid_not_cookie_file_mid(monkeypatch, capsys):
    monkeypatch.setattr(bili_login, '_load_cookie', lambda *_: ('test-cookie', 999))
    monkeypatch.setattr(bili_login, '_api', lambda *args, **kwargs: {
        'data': {'isLogin': True, 'mid': 123, 'uname': 'Me', 'face': 'https://self.test/avatar'}})
    bili_login.cmd_whoami(SimpleNamespace(cookie=None))
    data = result(capsys)
    assert data['loggedIn'] is True and data['name'] == 'Me'
    assert data.get('accountId') == '123'
    assert data.get('accountIdKind') == 'mid'


def test_douyin_whoami_passes_the_authenticated_account_binding(monkeypatch, capsys):
    response = {'httpStatus': 200, 'body': {'status_code': 0, 'user_info': {
        'uid': '456', 'nickname': 'Me', 'avatar_larger': {'url_list': ['https://self.test/avatar']}}}}
    page = SimpleNamespace(goto=lambda *args, **kwargs: None, evaluate=lambda *args: response)
    browser = SimpleNamespace(pages=[page], close=lambda: None)
    fake_playwright(monkeypatch, browser)
    monkeypatch.setattr(douyin_publish, '_launch', lambda *args, **kwargs: browser)
    monkeypatch.setattr(douyin_publish, '_wait_ready', lambda *args, **kwargs: None)
    monkeypatch.setattr(douyin_publish, '_logged_in', lambda *_: True)
    douyin_publish.cmd_whoami(SimpleNamespace(profile_base=None, proxy=None, no_proxy=True))
    data = result(capsys)
    assert data['loggedIn'] is True and data['name'] == 'Me'
    assert data.get('accountId') == '456'
    assert data.get('accountIdKind') == 'uid'


@pytest.mark.parametrize('platform,account,expected_kind', [
    ('zhihu', {'id': 'own-zhihu', 'name': 'Me', 'avatar_url': 'https://self.test/avatar'}, 'id'),
    ('weixin-channels', {'id': 'own-finder', 'name': 'Me', 'avatar_url': 'https://self.test/avatar'}, 'finderUsername'),
    ('kuaishou', None, ''),
])
def test_web_whoami_preserves_native_binding_or_explicit_unknown(monkeypatch, capsys, tmp_path, platform, account, expected_kind):
    page = SimpleNamespace(url=web_publisher.PLATFORMS[platform]['publish_url'])
    browser = SimpleNamespace(pages=[page], close=lambda: None)
    fake_playwright(monkeypatch, browser)
    monkeypatch.setattr(web_publisher, '_block_heavy', lambda *args, **kwargs: None)
    monkeypatch.setattr(web_publisher, '_observe_channels_account', lambda *args, **kwargs: None)
    monkeypatch.setattr(web_publisher, '_authenticated_account', lambda *args: account)
    monkeypatch.setattr(web_publisher, '_navigate_login', lambda *args: None)
    monkeypatch.setattr(web_publisher, '_settle_login', lambda *args, **kwargs: None)
    monkeypatch.setattr(web_publisher, '_is_logged_in', lambda *args: True)
    monkeypatch.setattr(web_publisher, '_probe_publish_auth', lambda *args: 'ok')
    monkeypatch.setattr(web_publisher, '_visible_account_identity', lambda *args: {'name': 'Me', 'avatar': 'https://self.test/avatar'})
    web_publisher.cmd_whoami(SimpleNamespace(platform=platform, profile_base=str(tmp_path)))
    data = result(capsys)
    assert data['loggedIn'] is True and data['name'] == 'Me'
    assert data.get('accountId') == (account['id'] if account else '')
    assert data.get('accountIdKind') == expected_kind
    assert data.get('accountIdentityStatus') == ('verified' if account else 'unknown')


def test_mp_session_token_and_visible_name_are_not_an_account_uid(monkeypatch, capsys):
    page = SimpleNamespace(url='https://mp.weixin.qq.com/cgi-bin/home?token=123', goto=lambda *args, **kwargs: None)
    browser = SimpleNamespace(pages=[page], close=lambda: None)
    fake_playwright(monkeypatch, browser)
    monkeypatch.setattr(weixin_mp_stats, '_launch', lambda *args, **kwargs: browser)
    monkeypatch.setattr(weixin_mp_stats, '_account_identity', lambda *_: {'name': 'MP label', 'avatar': 'https://self.test/avatar'})
    weixin_mp_stats.cmd_whoami(SimpleNamespace(headed=False, profile_base=None, proxy=None, no_proxy=True))
    data = result(capsys)
    assert data['loggedIn'] is True and data['name'] == 'MP label'
    assert {key: data.get(key) for key in UNKNOWN} == UNKNOWN
