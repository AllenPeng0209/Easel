"""Existing browser consumers restore the unified account before navigation."""
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from easel.account_sessions import SessionApplyError, save_session

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/shared/scripts"
sys.path.insert(0, str(SCRIPTS))


def snapshot(platform):
    host = "douyin.com" if platform == "douyin" else "xiaohongshu.com"
    return {"cookies": [{"name": "session", "value": "fixture-unified-session", "domain": "." + host, "path": "/"}],
            "origins": [{"origin": "https://creator." + host,
                         "localStorage": [{"name": "account-id", "value": "fixture-user"}]}]}


def prepare_session(tmp_path, monkeypatch, platform):
    monkeypatch.setenv("EASEL_ROOT", str(tmp_path))
    save_session(tmp_path, platform, 3, snapshot(platform), identity={"uid": "fixture-user"}, capabilities={})


class Context:
    def __init__(self):
        self.state = None
        self.events = []
        self.pages = [SimpleNamespace(goto=self.goto)]
    def set_storage_state(self, state):
        self.state = state
        self.events.append("restore")
    def goto(self, *_args, **_kwargs):
        assert self.state is not None, "The first platform request cannot use a previous account profile"
        self.events.append("navigate")
    def close(self):
        self.events.append("close")


def browser(context):
    def launch(*_args, **_kwargs):
        context.events.append("launch")
        return context
    return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))


@pytest.mark.parametrize(("module_name", "platform"), [
    ("douyin_publish", "douyin"), ("xhs_publish", "xhs"), ("xhs_comment", "xhs"),
])
def test_publisher_launch_restores_cookie_and_origin_state_before_returning(tmp_path, monkeypatch, module_name, platform):
    prepare_session(tmp_path, monkeypatch, platform)
    module = importlib.import_module(module_name)
    if module_name == "xhs_publish":
        monkeypatch.setattr(module, "_cloak_executable", lambda: None)
        monkeypatch.setattr(module.login_transport, "install", lambda *_args: None)
    context = Context()
    result = module._launch(browser(context), headed=False, base=str(tmp_path / "browser"), proxy=None)
    assert result.state == snapshot(platform)
    assert result.events == ["launch", "restore"]


def test_douyin_analytics_applies_the_unified_session_before_its_first_request(tmp_path, monkeypatch):
    prepare_session(tmp_path, monkeypatch, "douyin")
    stats = importlib.import_module("account_stats")
    context = Context()
    class Playwright:
        def __enter__(self):
            return browser(context)
        def __exit__(self, *_args):
            return False
    import playwright.sync_api
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", Playwright)
    monkeypatch.setattr(stats, "_login_wall", lambda _page: True)
    result = stats._scrape("douyin", False, str(tmp_path / "browser"), None)
    assert result["logged_in"] is False
    assert context.state == snapshot("douyin")
    assert context.events == ["launch", "restore", "navigate", "close"]


@pytest.mark.parametrize("module_name", ["douyin_publish", "xhs_publish", "xhs_comment"])
def test_a_browser_that_cannot_restore_the_session_is_closed_instead_of_using_an_old_account(tmp_path, monkeypatch, module_name):
    platform = "douyin" if module_name == "douyin_publish" else "xhs"
    prepare_session(tmp_path, monkeypatch, platform)
    module = importlib.import_module(module_name)
    if module_name == "xhs_publish":
        monkeypatch.setattr(module, "_cloak_executable", lambda: None)
        monkeypatch.setattr(module.login_transport, "install", lambda *_args: None)
    context = Context()
    context.set_storage_state = None
    with pytest.raises(SessionApplyError):
        module._launch(browser(context), headed=False, base=str(tmp_path / "browser"), proxy=None)
    assert context.events == ["launch", "close"]


def test_old_cli_profile_behavior_remains_available_without_an_explicit_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("EASEL_ROOT", raising=False)
    module = importlib.import_module("douyin_publish")
    context = Context()
    assert module._launch(browser(context), headed=False, base=str(tmp_path / "browser"), proxy=None) is context
    assert context.events == ["launch"]
