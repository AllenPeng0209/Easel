"""The existing content-account API must use the same owned login as interactions."""

import asyncio
import json
import time
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import AsyncMock, Mock

import pytest

import web.app as web
from easel.projects import ProjectMap, project_context


@pytest.fixture
def unified(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "LOGIN_DIR", tmp_path / "outputs/_login")
    monkeypatch.setattr(web, "BROWSER_PROFILES", tmp_path / "browser")
    monkeypatch.setattr(web, "LOGIN_PROCESSES", ProjectMap())
    monkeypatch.setattr(web, "_WHOAMI_CACHE", ProjectMap())
    monkeypatch.setattr(web, "_LOGIN_START_LOCKS", ProjectMap())
    monkeypatch.setattr(web, "_UNIFIED_LOGIN_TASKS", ProjectMap(), raising=False)
    monkeypatch.setattr(web, "_WHOAMI_SESSION_GENERATIONS", ProjectMap(), raising=False)
    events, records, generations, runtimes = [], {}, {}, {}
    monkeypatch.setattr(web, "_require_publication_profile_idle", lambda platform: events.append(("idle", platform)))
    monkeypatch.setattr(web.subprocess, "Popen", Mock(side_effect=AssertionError("legacy native runner started")))
    sessions = ModuleType("easel.account_sessions")
    sessions.load_session = lambda workspace, platform: records.get((str(workspace), platform))
    sessions.session_generation = lambda workspace, platform: generations.get((str(workspace), platform), 0)
    monkeypatch.setitem(sys.modules, "easel.account_sessions", sessions)

    class Connections:
        def __init__(self, project):
            self.project = project
            self.status = {"task_id": project + "-task", "status": "opening"}

        async def start(self, platform, account_id=None):
            events.append(("start", self.project, platform, account_id))
            return dict(self.status)

        def poll(self, task_id):
            assert task_id == self.project + "-task"
            return dict(self.status)

        async def revoke(self, platform):
            events.append(("revoke", self.project, platform))

    async def get(project, **kwargs):
        if project not in runtimes:
            runtimes[project] = SimpleNamespace(connections=Connections(project))
        return runtimes[project]

    manager = SimpleNamespace(get=get)
    monkeypatch.setattr(web, "_operations_manager", AsyncMock(return_value=manager))
    return SimpleNamespace(root=tmp_path, events=events, records=records,
                           generations=generations, runtimes=runtimes)


@pytest.mark.parametrize(("platform", "canonical"), [("douyin", "douyin"), ("xiaohongshu", "xhs")])
def test_original_login_enters_the_unified_connection(unified, platform, canonical):
    response = asyncio.run(web.api_login_start(platform))
    assert unified.events == [("idle", platform), ("start", "default", canonical, None)]
    assert response["mode"] == "browser"
    assert response["state"] == "window_login"
    assert response["qr"] == ""


@pytest.mark.parametrize(("status", "expected"), [
    ("opening", "window_login"), ("waiting", "window_login"),
    ("verification", "window_login"), ("persisted", "window_login"),
    ("confirmed", "success"), ("error", "error"), ("expired", "expired"),
])
def test_original_poll_translates_unified_status_without_secrets(unified, status, expected):
    asyncio.run(web.api_login_start("douyin"))
    unified.runtimes["default"].connections.status.update(
        status=status, hint="请在同一登录窗口完成平台验证", storage_state="must-not-return",
        capabilities={"comments": "verified", "publish": "needs_verification"})
    first = asyncio.run(web.api_login_status("douyin"))
    second = asyncio.run(web.api_login_status("douyin"))
    assert first == second
    assert first["mode"] == "browser"
    assert first["state"] == expected
    assert "must-not-return" not in json.dumps(first)
    if status == "confirmed":
        assert "补充" in first["message"] or "部分" in first["message"]


def test_native_login_tasks_are_owned_by_the_current_ip(unified):
    asyncio.run(web.api_login_start("douyin"))
    unified.runtimes["default"].connections.status.update(status="confirmed")
    with project_context("0123456789abcdef", unified.root):
        asyncio.run(web.api_login_start("douyin"))
        unified.runtimes["0123456789abcdef"].connections.status.update(status="verification")
        assert asyncio.run(web.api_login_status("douyin"))["state"] == "window_login"
    assert asyncio.run(web.api_login_status("douyin"))["state"] == "success"


def test_an_expired_coordinator_task_returns_a_terminal_legacy_login_error(unified):
    asyncio.run(web.api_login_start("douyin"))
    unified.runtimes["default"].connections.poll = Mock(side_effect=web.HTTPException(404, "task missing"))
    result = asyncio.run(web.api_login_status("douyin"))
    assert result["state"] == "error"
    assert "重新连接" in result["message"]


def test_partial_private_message_access_is_not_described_as_all_features_verified(unified):
    asyncio.run(web.api_login_start("douyin"))
    unified.runtimes["default"].connections.status.update(status="confirmed", completion="partial",
        capabilities={"creator": "verified", "publish": "verified", "analytics": "verified",
                      "comments": "verified", "dm": "needs_verification"})
    result = asyncio.run(web.api_login_status("douyin"))
    assert "部分" in result["message"]


def test_content_status_uses_verified_creator_capability_not_old_success_marker(unified):
    web._write_login_marker("douyin", "success")
    record = {"account_id": 7, "storage_state": {"cookies": [{"value": "private"}]},
              "capabilities": {"comments": "verified", "publish": "needs_verification", "creator": "needs_verification"}}
    unified.records[(str(unified.root), "douyin")] = record
    accounts = {item["platform"]: item for item in asyncio.run(web.api_accounts())}
    assert accounts["douyin"]["loggedIn"] is False
    assert "private" not in json.dumps(accounts)
    record["capabilities"]["creator"] = "verified"
    assert web._account_logged_in("douyin", web.LOGIN_RUNNERS["douyin"]) is True


def test_unmigrated_content_state_keeps_the_existing_marker_fallback(unified):
    web._write_login_marker("xiaohongshu", "success")
    assert web._account_logged_in("xiaohongshu", web.LOGIN_RUNNERS["xiaohongshu"]) is True
    unified.generations[(str(unified.root), "xhs")] = 2
    assert web._account_logged_in("xiaohongshu", web.LOGIN_RUNNERS["xiaohongshu"]) is False


def test_verified_snapshot_does_not_mark_another_ip_logged_in(unified):
    unified.records[(str(unified.root), "douyin")] = {"capabilities": {"creator": "verified"}}
    assert web._account_logged_in("douyin", web.LOGIN_RUNNERS["douyin"]) is True
    with project_context("0123456789abcdef", unified.root):
        assert web._account_logged_in("douyin", web.LOGIN_RUNNERS["douyin"]) is False


def test_native_logout_revokes_the_shared_session_before_old_files_are_cleared(unified):
    asyncio.run(web.api_login_start("douyin"))
    web._write_login_marker("douyin", "success")
    profile = unified.root / "browser/DouyinProfile"
    profile.mkdir(parents=True)
    (profile / "fixture").write_text("preserve until revoke")
    original = unified.runtimes["default"].connections.revoke

    async def revoke(platform):
        assert profile.is_dir()
        assert (web._login_dir() / "douyin.json").is_file()
        await original(platform)

    unified.runtimes["default"].connections.revoke = revoke
    assert asyncio.run(web.api_logout("douyin"))["ok"] is True
    assert ("revoke", "default", "douyin") in unified.events
    assert not profile.exists()
    assert "douyin" not in web._UNIFIED_LOGIN_TASKS


def test_other_platforms_still_use_the_existing_runner(unified, monkeypatch):
    launch = Mock(return_value=SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(web.subprocess, "Popen", launch)
    response = asyncio.run(web.api_login_start("youtube"))
    assert response["mode"] == "browser"
    assert launch.call_args.args[0][1].endswith("social_login.py")
    assert unified.runtimes == {}


def test_account_identity_cache_is_invalidated_when_the_shared_account_changes(unified, monkeypatch):
    key = (str(unified.root), "douyin")
    unified.records[key] = {"capabilities": {"creator": "verified"},
                            "identity": {"accountId": "new-user", "accountIdKind": "uid"}}
    unified.generations[key] = 2
    web._WHOAMI_CACHE["douyin"] = (time.time(), {"loggedIn": True, "name": "Old identity", "avatar": "old.png"})
    web._WHOAMI_SESSION_GENERATIONS["douyin"] = 1
    probe = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({
        "loggedIn": True, "name": "New identity", "avatar": "new.png", "accountId": "new-user",
        "accountIdKind": "uid", "accountIdentityStatus": "verified"})))
    monkeypatch.setattr(web.subprocess, "run", probe)
    assert asyncio.run(web._check_account_whoami("douyin"))["name"] == "New identity"
    assert probe.call_count == 1


def test_a_late_identity_probe_cannot_cache_an_account_from_an_older_shared_session(unified, monkeypatch):
    key = (str(unified.root), "douyin")
    unified.records[key] = {"capabilities": {"creator": "verified"}}
    unified.generations[key] = 1

    def probe(*args, **kwargs):
        unified.generations[key] = 2
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "loggedIn": True, "name": "Old identity", "avatar": "old.png"}))

    monkeypatch.setattr(web.subprocess, "run", probe)
    result = asyncio.run(web._check_account_whoami("douyin", force=True))
    assert result.get("name") != "Old identity"
    assert "douyin" not in web._WHOAMI_CACHE


def test_strict_content_identity_requires_creator_verification_before_launching_a_probe(unified, monkeypatch):
    unified.records[(str(unified.root), "xhs")] = {
        "capabilities": {"comments": "verified", "creator": "needs_verification", "publish": "needs_verification"},
        "identity": {"accountId": "reader-a", "accountIdKind": "user_id"}}
    probe = Mock(side_effect=AssertionError("Unverified creator session must not start a publishing probe"))
    monkeypatch.setattr(web.subprocess, "run", probe)
    with pytest.raises(web.HTTPException) as failure:
        asyncio.run(web._check_account_whoami("xiaohongshu", strict=True))
    assert failure.value.status_code == 409
    assert "补充验证" in failure.value.detail
    probe.assert_not_called()
    assert "xiaohongshu" not in web._WHOAMI_CACHE
    assert not (web._login_dir() / "xiaohongshu.json").exists()


@pytest.mark.parametrize("strict,expected_status", [(True, 409), (False, 502)])
@pytest.mark.parametrize("binding", [
    {"accountId": "creator-b", "accountIdKind": "uid", "accountIdentityStatus": "verified"},
    {"accountId": "same-label-a", "accountIdKind": "sec_uid", "accountIdentityStatus": "verified"},
    {"accountId": "", "accountIdKind": "", "accountIdentityStatus": "unknown"},
])
def test_probe_cannot_bind_a_different_or_incomparable_subject_to_the_shared_login(
    unified, monkeypatch, strict, expected_status, binding,
):
    unified.records[(str(unified.root), "douyin")] = {
        "capabilities": {"creator": "verified", "publish": "verified"},
        "identity": {"accountId": "same-label-a", "accountIdKind": "uid"}}
    probe = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({
        "loggedIn": True, "name": "Wrong creator", "avatar": "other.png", **binding})))
    monkeypatch.setattr(web.subprocess, "run", probe)
    with pytest.raises(web.HTTPException) as failure:
        asyncio.run(web._check_account_whoami("douyin", force=True, strict=strict))
    assert failure.value.status_code == expected_status
    assert "douyin" not in web._WHOAMI_CACHE
    assert not (web._login_dir() / "douyin.json").exists()


@pytest.mark.parametrize(("platform", "canonical", "identity", "binding"), [
    ("douyin", "douyin", {"accountId": "owner-a", "accountIdKind": "uid"},
     {"accountId": "owner-a", "accountIdKind": "uid"}),
    ("douyin", "douyin", {"accountId": "sec-a", "accountIdKind": "sec_uid", "uid": "uid-a"},
     {"accountId": "uid-a", "accountIdKind": "uid"}),
    ("xiaohongshu", "xhs", {"accountId": "owner-a", "accountIdKind": "user_id", "user_id": "owner-a"},
     {"accountId": "owner-a", "accountIdKind": "userId"}),
])
def test_strict_probe_accepts_only_the_shared_subject_in_the_same_identifier_namespace(
    unified, monkeypatch, platform, canonical, identity, binding,
):
    unified.records[(str(unified.root), canonical)] = {
        "capabilities": {"creator": "verified", "publish": "verified"}, "identity": identity}
    probe = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps({
        "loggedIn": True, "name": "Same owner", "avatar": "own.png",
        "accountIdentityStatus": "verified", **binding})))
    monkeypatch.setattr(web.subprocess, "run", probe)
    result = asyncio.run(web._check_account_whoami(platform, force=True, strict=True))
    assert result["name"] == "Same owner"
    assert web._WHOAMI_CACHE[platform][1]["accountId"] == binding["accountId"]
