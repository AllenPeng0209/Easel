import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import web.app as web
from easel.projects import project_context


PLATFORMS = ("facebook", "instagram", "tiktok", "x", "youtube")


@pytest.fixture
def account_state(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "LOGIN_DIR", tmp_path / "login")
    monkeypatch.setattr(web, "BROWSER_PROFILES", tmp_path / "browser")
    monkeypatch.setattr(web, "LOGIN_PROCESSES", {})
    monkeypatch.setattr(web, "_WHOAMI_CACHE", {})
    monkeypatch.setattr(web, "_WHOAMI_TASKS", {})
    monkeypatch.setattr(web, "_LOGIN_START_LOCKS", {})
    return tmp_path


def test_account_page_offers_overseas_browser_login(account_state):
    accounts = {item["platform"]: item for item in asyncio.run(web.api_accounts())}
    for platform in PLATFORMS:
        assert platform in accounts
        assert accounts[platform]["supported"]
        assert accounts[platform]["loginMode"] == "browser"
        assert not accounts[platform]["loggedIn"]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_social_login_launches_browser_runner_with_scoped_profile(account_state, monkeypatch, platform):
    launch = Mock(return_value=SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(web.subprocess, "Popen", launch)
    result = asyncio.run(web.api_login_start(platform))
    assert result["mode"] == "browser"
    assert result["state"] == "starting"
    assert result["qr"] == ""
    assert "浏览器" in result["message"]
    command = launch.call_args.args[0]
    assert command[1].endswith("social_login.py")
    assert command[2:5] == ["login", "--platform", platform]
    assert "--no-proxy" not in command
    assert launch.call_args.kwargs["env"]["EASEL_BROWSER_PROFILES"] == str(account_state / "browser")
    assert asyncio.run(web.api_login_start(platform))["mode"] == "browser"
    assert asyncio.run(web.api_login_status(platform))["mode"] == "browser"
    assert launch.call_count == 1


@pytest.mark.parametrize("platform", PLATFORMS)
def test_social_whoami_uses_same_browser_runner(account_state, monkeypatch, platform):
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({
        "loggedIn": True, "name": "Own account", "avatar": "https://example.com/avatar.png",
    })))
    monkeypatch.setattr(web.subprocess, "run", run)
    result = asyncio.run(web.api_account_whoami(platform, force=True))
    assert result["name"] == "Own account"
    assert run.call_args.args[0][1].endswith("social_login.py")
    assert run.call_args.args[0][2:5] == ["whoami", "--platform", platform]
    assert (account_state / "login" / f"{platform}.json").is_file()


def test_social_logout_only_clears_current_ip_browser(account_state, tmp_path):
    from social_login import PLATFORMS as SOCIAL_PLATFORMS

    profile = SOCIAL_PLATFORMS["facebook"]["profile"]
    default = account_state / "browser" / profile
    default.mkdir(parents=True)
    other = tmp_path / "projects" / "creator-a" / "browser" / profile
    other.mkdir(parents=True)
    (other / "keep").write_text("session", encoding="utf-8")
    with project_context("creator-a", tmp_path):
        web._write_login_marker("facebook", "success")
        result = asyncio.run(web.api_logout("facebook"))
        assert profile in result["deleted"]
        assert not web._account_logged_in("facebook", web.LOGIN_RUNNERS["facebook"])
    assert default.is_dir()
    assert not other.exists()


def test_social_browser_login_does_not_claim_automatic_publishing_support(account_state, monkeypatch):
    monkeypatch.setattr(web, "_require_automated_publishing", lambda: None)
    with pytest.raises(web.HTTPException) as failure:
        asyncio.run(web.api_publish("facebook", web.PublishRequest(title="Draft", body="Text")))
    assert failure.value.status_code == 400
    assert "仅支持账号登录" in failure.value.detail


def test_social_proxy_in_project_settings_reaches_browser_runner(account_state, monkeypatch):
    monkeypatch.setattr(web, "read_settings", lambda _path: {"EASEL_SOCIAL_PROXY": "http://127.0.0.1:7890"})
    assert web._proxy_env()["EASEL_SOCIAL_PROXY"] == "http://127.0.0.1:7890"
