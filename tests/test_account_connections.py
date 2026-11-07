"""The unified login must verify identity before sharing either session."""

import asyncio
from copy import deepcopy
from contextlib import asynccontextmanager
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlmodel import select

from easel.operations import OperationsManager


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def runtime(tmp_path):
    manager = OperationsManager(tmp_path)
    value = await manager.get("default", start=False)
    yield value
    await manager.close()


@pytest.fixture
def session_store():
    return importlib.import_module("easel.account_sessions")


def connections(runtime):
    path = Path(__file__).parents[1] / "easel/account_connections.py"
    assert path.is_file(), (
        "There is no unified login coordinator; callers still choose a purpose"
    )
    return importlib.import_module("easel.account_connections").AccountConnections(
        runtime
    )


def state(platform, name):
    domain = ".douyin.com" if platform == "douyin" else ".xiaohongshu.com"
    return {
        "cookies": [
            {
                "name": name,
                "value": "private-cookie-value",
                "domain": domain,
                "path": "/",
            }
        ],
        "origins": [],
    }


def native_account(runtime, platform="douyin", identifier=1):
    profile = runtime.directory / "profiles" / f"owned-{identifier}"
    profile.mkdir(parents=True, exist_ok=True)
    with runtime.module.get_session() as db:
        account = runtime.module.DouyinAccount(
            id=identifier,
            platform=platform,
            nickname="Same nickname",
            uid="100" if platform == "douyin" else "",
            sec_uid="secure-100" if platform == "douyin" else "100",
            profile_dir=str(profile),
            storage_state=json.dumps(state(platform, "read-session")),
        )
        db.add(account)
        db.commit()
    return identifier


def install_platform(
    runtime,
    monkeypatch,
    *,
    creator_subject="100",
    fail_creator=False,
    unknown_creator=False,
    unknown_read=False,
    pause=None,
    require_candidate=False,
    final_subject=None,
    rotate_final=False,
    dm_error="",
):
    module = runtime.module
    calls, closed = [], []
    current = {
        "creator": False,
        "subject": "100",
        "platform": "douyin",
        "restored": None,
        "reads": 0,
    }

    class Response:
        status = 200

        async def json(self):
            if fail_creator or unknown_creator:
                return {}
            subject = (
                creator_subject
                if not require_candidate or current["restored"]
                else "obsolete-account"
            )
            if current["platform"] == "douyin":
                return {
                    "status_code": 0,
                    "user_info": {"uid": subject, "sec_uid": "secure-" + subject},
                }
            return {
                "success": True,
                "data": {"userId": creator_subject, "userName": "Same nickname"},
            }

    async def get(url, **options):
        assert url in {
            "https://creator.douyin.com/web/api/media/user/info/?aid=1128",
            "https://creator.xiaohongshu.com/api/galaxy/user/info",
        }
        return Response()

    async def storage_state():
        if rotate_final and current["reads"] >= 2:
            return state(current["platform"], "rotated-session")
        return (
            {"cookies": current["restored"], "origins": []}
            if current["restored"] is not None
            else state(
                current["platform"],
                "creator-session" if current["creator"] else "read-session",
            )
        )

    async def clear_cookies():
        current["restored"] = []

    async def add_cookies(cookies):
        current["restored"] = cookies

    async def restore(snapshot):
        current["restored"] = snapshot["cookies"]

    context = SimpleNamespace(
        request=SimpleNamespace(get=get),
        storage_state=storage_state,
        clear_cookies=clear_cookies,
        add_cookies=add_cookies,
        set_storage_state=restore,
    )

    def identity_for(account):
        current["platform"] = account.platform
        return SimpleNamespace(
            account_id=account.id,
            key=account.id,
            platform=account.platform,
            profile_dir=account.profile_dir,
            proxy="",
            ua="test browser",
            identity_mode="native",
        )

    async def context_for(identity):
        return context

    async def close_context(key):
        closed.append(key)

    browser = SimpleNamespace(
        identity_for=identity_for, context_for=context_for, close_context=close_context
    )
    monkeypatch.setattr(module, "browser", browser)

    async def read_profile(browser, identity, **kwargs):
        current["reads"] += 1
        if unknown_read:
            return {}, "auth_unconfirmed"
        subject = (
            current["subject"]
            if not require_candidate or current["restored"]
            else "obsolete-account"
        )
        if final_subject and current["reads"] >= 2:
            subject = final_subject
        if identity.platform == "douyin":
            return {
                "uid": subject,
                "sec_uid": "secure-" + subject,
                "nickname": "Same nickname",
            }, ""
        return {"user_id": subject, "nickname": "Same nickname"}, ""

    monkeypatch.setattr(module, "fetch_self_profile", read_profile)
    monkeypatch.setattr(module, "fetch_xhs_self_profile", read_profile)

    async def dm_conversations(browser, identity, platform):
        return [], dm_error

    async def guarded_read_pair(account_id, operation_kind, label, operation, **kwargs):
        return await operation()

    monkeypatch.setattr(module, "fetch_dm_conversations", dm_conversations)
    monkeypatch.setattr(
        module, "engine", SimpleNamespace(guarded_read_pair=guarded_read_pair)
    )

    async def run(
        task_id,
        *,
        platform,
        creator=False,
        account_id=None,
        force_reauth=None,
        before_persist=None,
        **kwargs,
    ):
        calls.append(
            {"creator": creator, "account_id": account_id, "force_reauth": force_reauth}
        )
        current.update(
            creator=creator,
            platform=platform,
            subject=creator_subject if creator else "100",
        )
        module.login_tasks[task_id] = {
            "status": "waiting",
            "platform": platform,
            "account_id": account_id,
        }
        if pause is not None:
            try:
                await pause.wait()
            except asyncio.CancelledError:
                # A browser/HTTP boundary may finish after cancellation. The
                # generation fence must still forbid this candidate's commit.
                await pause.wait()
        if creator and fail_creator:
            module.login_tasks[task_id] = {
                "status": "error",
                "error": "https://private.invalid/?token=secret",
                "account_id": account_id,
            }
            return
        with module.get_session() as db:
            account = db.get(module.DouyinAccount, account_id) if account_id else None
            identity = (
                identity_for(account)
                if account
                else SimpleNamespace(
                    account_id=None,
                    key="new",
                    platform=platform,
                    profile_dir=str(runtime.directory / "profiles/new"),
                    proxy="",
                    ua="test browser",
                    identity_mode="native",
                )
            )
        snapshot = state(platform, "creator-session" if creator else "read-session")
        if creator:
            snapshot["cookies"] = (
                state(platform, "read-session")["cookies"] + snapshot["cookies"]
            )
        try:
            verified = await before_persist(
                {
                    "platform": platform,
                    "creator": creator,
                    "account_id": account_id,
                    "state": json.dumps(snapshot),
                    "identity": identity,
                    "observed_profile": {},
                }
            )
        except Exception:
            module.login_tasks[task_id] = {"status": "error", "account_id": account_id}
            return
        with module.get_session() as db:
            account = (
                db.get(module.DouyinAccount, account_id)
                if account_id
                else module.DouyinAccount(
                    platform=platform, profile_dir=identity.profile_dir
                )
            )
            if creator:
                account.creator_storage_state = json.dumps(snapshot)
            else:
                account.storage_state = json.dumps(snapshot)
            for name in ("uid", "sec_uid", "nickname"):
                if verified.get(name):
                    setattr(account, name, verified[name])
            account.status = "active"
            db.add(account)
            db.commit()
            db.refresh(account)
            account_id = account.id
        module.login_tasks[task_id] = {
            "status": "confirmed",
            "account_id": account_id,
            "platform": platform,
        }

    monkeypatch.setattr(module, "_run_login", run)
    return calls, closed


async def settled(api, task_id):
    for _ in range(80):
        item = api.poll(task_id)
        if item["status"] in {"confirmed", "error", "expired"}:
            return item
        await asyncio.sleep(0)
    raise AssertionError("The unified login never reached a terminal state")


@pytest.mark.anyio
@pytest.mark.parametrize("platform", ["douyin", "xhs"])
async def test_one_connection_checks_both_capabilities_and_shares_one_verified_account(
    runtime, monkeypatch, session_store, platform
):
    calls, _ = install_platform(runtime, monkeypatch)
    api = connections(runtime)
    started = await api.start(platform)
    result = await settled(api, started["task_id"])
    assert result["status"] == "confirmed"
    assert result["completion"] == "full"
    assert result["can_send"] is False
    assert result["capabilities"] == {
        "comments": "verified",
        "dm": "verified",
        "creator": "verified",
        "publish": "verified",
        "analytics": "verified",
    }
    assert [item["creator"] for item in calls] == [False, True]
    assert all(item["force_reauth"] is False for item in calls)
    assert calls[1]["account_id"] == result["account_id"]
    record = session_store.load_session(runtime.workspace, platform)
    assert {cookie["name"] for cookie in record["storage_state"]["cookies"]} == {
        "read-session",
        "creator-session",
    }
    assert "private-cookie-value" not in json.dumps(result)
    assert api.poll(started["task_id"]) == result
    result["capabilities"]["publish"] = "tampered"
    assert api.poll(started["task_id"])["capabilities"]["publish"] == "verified"
    await api.close()


@pytest.mark.anyio
async def test_existing_account_is_reused_without_clearing_its_session(
    runtime, monkeypatch, session_store
):
    native_account(runtime)
    calls, _ = install_platform(runtime, monkeypatch)
    api = connections(runtime)
    result = await settled(api, (await api.start("douyin"))["task_id"])
    assert result["account_id"] == 1
    assert calls == [], (
        "An existing session that proves both identities must not start another interactive login"
    )
    with runtime.module.get_session() as db:
        assert len(db.exec(select(runtime.module.DouyinAccount)).all()) == 1
    await api.close()


@pytest.mark.anyio
async def test_new_account_intent_does_not_reuse_the_only_existing_account(
    runtime, monkeypatch, session_store
):
    native_account(runtime)
    calls, _ = install_platform(runtime, monkeypatch)
    api = connections(runtime)
    result = await settled(
        api, (await api.start("douyin", new_account=True))["task_id"]
    )
    assert result["account_id"] != 1
    assert calls[0]["account_id"] is None
    with runtime.module.get_session() as db:
        assert len(db.exec(select(runtime.module.DouyinAccount)).all()) == 2
    await api.close()


@pytest.mark.anyio
async def test_candidate_snapshot_replaces_obsolete_context_authentication_before_proof(
    runtime, monkeypatch, session_store
):
    install_platform(runtime, monkeypatch, require_candidate=True)
    api = connections(runtime)
    result = await settled(api, (await api.start("douyin"))["task_id"])
    assert result["completion"] == "full"
    assert (
        session_store.load_session(runtime.workspace, "douyin")["identity"]["accountId"]
        == "100"
    )
    await api.close()


@pytest.mark.anyio
async def test_final_rotation_does_not_resurrect_cookies_removed_from_the_current_session(
    runtime, monkeypatch, session_store
):
    install_platform(runtime, monkeypatch, rotate_final=True)
    api = connections(runtime)
    result = await settled(api, (await api.start("douyin"))["task_id"])
    assert result["completion"] == "full"
    snapshot = session_store.load_session(runtime.workspace, "douyin")["storage_state"]
    assert {cookie["name"] for cookie in snapshot["cookies"]} == {"rotated-session"}
    await api.close()


@pytest.mark.anyio
@pytest.mark.parametrize("platform", ["douyin", "xhs"])
async def test_dm_probe_that_is_unavailable_or_deferred_stays_unverified(
    runtime, monkeypatch, session_store, platform
):
    install_platform(runtime, monkeypatch, dm_error="risk_deferred:retry_later")
    api = connections(runtime)
    result = await settled(api, (await api.start(platform))["task_id"])
    assert result["capabilities"]["creator"] == "verified"
    assert result["capabilities"]["dm"] == "needs_verification"
    assert result["can_send"] is False
    await api.close()


@pytest.mark.anyio
async def test_final_identity_conflict_invalidates_both_sides_of_the_connection(
    runtime, monkeypatch, session_store
):
    install_platform(runtime, monkeypatch, final_subject="200")
    api = connections(runtime)
    result = await settled(api, (await api.start("douyin"))["task_id"])
    assert result["status"] == "error" and result["stage"] == "identity_mismatch"
    assert (
        result["capabilities"]["comments"]
        == result["capabilities"]["creator"]
        == result["capabilities"]["publish"]
        == "needs_verification"
    )
    with runtime.module.get_session() as db:
        assert (
            db.get(runtime.module.DouyinAccount, result["account_id"]).status
            == "invalid"
        )
    assert session_store.load_session(runtime.workspace, "douyin") is None
    await api.close()


@pytest.mark.anyio
async def test_existing_identity_conflict_invalidates_the_native_account_before_returning(
    runtime, monkeypatch, session_store
):
    native_account(runtime)
    install_platform(runtime, monkeypatch, final_subject="200")
    with runtime.module.get_session() as db:
        account = db.get(runtime.module.DouyinAccount, 1)
        account.uid = "other-owner"
        account.sec_uid = "other-owner-sec"
        db.add(account)
        db.commit()
    api = connections(runtime)
    result = await settled(api, (await api.start("douyin", account_id=1))["task_id"])
    assert result["status"] == "error"
    with runtime.module.get_session() as db:
        assert db.get(runtime.module.DouyinAccount, 1).status == "invalid"
    await api.close()


@pytest.mark.anyio
async def test_an_existing_native_login_cannot_race_the_unified_flow(
    runtime, monkeypatch, session_store
):
    calls, _ = install_platform(runtime, monkeypatch)
    runtime.module.login_tasks["legacy-login"] = {
        "status": "waiting",
        "platform": "douyin",
        "creator": False,
        "account_id": None,
    }
    api = connections(runtime)
    with pytest.raises(HTTPException) as error:
        await api.start("douyin")
    assert error.value.status_code == 409
    assert calls == []
    await api.close()


@pytest.mark.anyio
async def test_logout_cleans_only_owned_profiles_for_this_platform(
    runtime, monkeypatch, session_store, tmp_path
):
    native_account(runtime)
    native_account(runtime, "xhs", identifier=2)
    owned = runtime.directory / "profiles/owned-1"
    protected = runtime.directory / "profiles/owned-2"
    outside = tmp_path / "unrelated-browser"
    outside.mkdir()
    for folder in (owned, protected, outside):
        (folder / "credential-fixture").write_text("fixture")
    with runtime.module.get_session() as db:
        db.add(
            runtime.module.DouyinAccount(
                id=3,
                platform="douyin",
                profile_dir=str(outside),
                storage_state=json.dumps(state("douyin", "read-session")),
            )
        )
        db.commit()
    install_platform(runtime, monkeypatch)
    api = connections(runtime)
    await api.revoke("douyin")
    assert owned.is_dir() and not (owned / "credential-fixture").exists()
    assert (protected / "credential-fixture").read_text() == "fixture"
    assert (outside / "credential-fixture").read_text() == "fixture"
    with runtime.module.get_session() as db:
        assert db.get(runtime.module.DouyinAccount, 2).status == "active"
        assert db.get(runtime.module.DouyinAccount, 3).status == "invalid"
    await api.close()


@pytest.mark.anyio
async def test_a_different_creator_identity_cannot_overwrite_the_read_account(
    runtime, monkeypatch, session_store
):
    native_account(runtime)
    calls, closed = install_platform(runtime, monkeypatch, creator_subject="200")
    api = connections(runtime)
    result = await settled(api, (await api.start("douyin", account_id=1))["task_id"])
    assert result["status"] == "error" and result["stage"] == "identity_mismatch"
    assert result["capabilities"]["publish"] == "needs_verification"
    with runtime.module.get_session() as db:
        account = db.get(runtime.module.DouyinAccount, 1)
        assert account.uid == "100" and account.creator_storage_state == ""
    assert closed
    assert session_store.load_session(runtime.workspace, "douyin") is None
    assert "200" not in json.dumps(result)
    await api.close()


@pytest.mark.anyio
async def test_candidate_restores_complete_storage_without_retaining_obsolete_origin_data(
    runtime, monkeypatch, session_store
):
    api = connections(runtime)
    snapshot = state("xhs", "read-session")
    snapshot["origins"] = [
        {
            "origin": "https://www.xiaohongshu.com",
            "localStorage": [{"name": "user", "value": "100"}],
            "indexedDB": [{"name": "auth", "version": 1, "stores": []}],
        }
    ]
    memory = {
        "cookies": state("xhs", "obsolete")["cookies"],
        "origins": [
            {
                "origin": "https://creator.xiaohongshu.com",
                "localStorage": [{"name": "user", "value": "200"}],
            }
        ],
    }

    async def restore(value):
        memory.clear()
        memory.update(deepcopy(value))

    async def context_for(identity):
        return SimpleNamespace(set_storage_state=restore)

    monkeypatch.setattr(
        runtime.module, "browser", SimpleNamespace(context_for=context_for)
    )
    identity = SimpleNamespace()
    await api._install_candidate("xhs", identity, snapshot)
    assert memory == snapshot
    assert memory["origins"][0]["indexedDB"] == [
        {"name": "auth", "version": 1, "stores": []}
    ]
    await api.close()


@pytest.mark.anyio
async def test_cookie_presence_and_unknown_identity_cannot_be_reported_as_verified(
    runtime, monkeypatch, session_store
):
    install_platform(runtime, monkeypatch, unknown_read=True)
    api = connections(runtime)
    result = await settled(api, (await api.start("douyin"))["task_id"])
    assert result["status"] != "confirmed"
    assert "verified" not in result["capabilities"].values()
    assert session_store.load_session(runtime.workspace, "douyin") is None
    with runtime.module.get_session() as db:
        assert db.exec(select(runtime.module.DouyinAccount)).all() == []
    await api.close()


@pytest.mark.anyio
async def test_creator_failure_leaves_a_safe_partial_connection_and_hides_raw_errors(
    runtime, monkeypatch, session_store
):
    install_platform(runtime, monkeypatch, fail_creator=True)
    api = connections(runtime)
    result = await settled(api, (await api.start("xhs"))["task_id"])
    assert result["status"] == "confirmed" and result["completion"] == "partial"
    assert result["capabilities"]["comments"] == "verified"
    assert result["capabilities"]["publish"] == "needs_verification"
    assert "secret" not in json.dumps(result)
    await api.close()


@pytest.mark.anyio
async def test_an_unverified_creator_candidate_is_not_exported_to_legacy_content_tools(
    runtime, monkeypatch, session_store
):
    install_platform(runtime, monkeypatch, unknown_creator=True)
    api = connections(runtime)
    result = await settled(api, (await api.start("xhs"))["task_id"])
    assert result["status"] == "confirmed" and result["completion"] == "partial"
    snapshot = session_store.load_session(runtime.workspace, "xhs")["storage_state"]
    assert {cookie["name"] for cookie in snapshot["cookies"]} == {"read-session"}
    await api.close()


@pytest.mark.anyio
async def test_platform_and_account_validation_happen_before_any_login(
    runtime, monkeypatch, session_store
):
    native_account(runtime, "xhs")
    calls, _ = install_platform(runtime, monkeypatch)
    api = connections(runtime)
    for platform, identifier, expected in [
        ("youtube", None, 400),
        ("douyin", 1, 404),
        ("xhs", 99, 404),
    ]:
        with pytest.raises(HTTPException) as error:
            await api.start(platform, account_id=identifier)
        assert error.value.status_code == expected
    with pytest.raises(HTTPException) as error:
        api.poll("another-project-task")
    assert error.value.status_code == 404
    assert calls == []
    await api.close()


@pytest.mark.anyio
async def test_revocation_fences_a_browser_that_finishes_after_cancellation(
    runtime, monkeypatch, session_store
):
    native_account(runtime)
    pause = asyncio.Event()
    _, closed = install_platform(runtime, monkeypatch, pause=pause)
    api = connections(runtime)
    started = await api.start("douyin", account_id=1)
    await asyncio.sleep(0)
    revoke = asyncio.create_task(api.revoke("douyin"))
    await asyncio.sleep(0)
    pause.set()
    await revoke
    assert api.poll(started["task_id"])["status"] == "error"
    assert session_store.load_session(runtime.workspace, "douyin") is None
    with runtime.module.get_session() as db:
        account = db.get(runtime.module.DouyinAccount, 1)
        assert account.status == "invalid"
        assert (
            account.storage_state
            == account.creator_storage_state
            == account.cookie
            == ""
        )
    assert closed
    await api.close()


@pytest.mark.anyio
@pytest.mark.parametrize('selected,shared_active', [(1, True), (2, False)])
async def test_revoking_one_account_keeps_the_other_same_platform_account_logged_in(
    runtime, monkeypatch, session_store, selected, shared_active
):
    native_account(runtime, identifier=1)
    native_account(runtime, identifier=2)
    _, closed = install_platform(runtime, monkeypatch)
    profiles = {}
    with runtime.module.get_session() as db:
        for identifier in (1, 2):
            account = db.get(runtime.module.DouyinAccount, identifier)
            account.uid = str(identifier * 100)
            account.sec_uid = 'secure-' + str(identifier * 100)
            account.creator_storage_state = json.dumps(state('douyin', 'creator-' + str(identifier)))
            account.cookie = 'account-' + str(identifier)
            profiles[identifier] = Path(account.profile_dir)
            (profiles[identifier] / 'account-session-marker').write_text(str(identifier))
            db.add(account)
        db.commit()
    generation = session_store.begin_generation(runtime.workspace, 'douyin')
    session_store.save_session(runtime.workspace, 'douyin', 1, state('douyin', 'active-content'),
        identity={'uid': '100', 'accountIdentityStatus': 'verified'}, capabilities={'creator': 'verified'}, expected_generation=generation)
    api = connections(runtime)
    result = await api.revoke_account(selected)
    assert result == {'account_id': selected, 'platform': 'douyin', 'shared_active': shared_active}
    with runtime.module.get_session() as db:
        revoked = db.get(runtime.module.DouyinAccount, selected)
        other = db.get(runtime.module.DouyinAccount, 3 - selected)
        assert revoked.status == 'invalid'
        assert revoked.storage_state == revoked.creator_storage_state == revoked.cookie == ''
        assert other.status == 'active'
        assert other.storage_state and other.creator_storage_state and other.cookie
    assert closed == [selected]
    assert not (profiles[selected] / 'account-session-marker').exists()
    assert (profiles[3 - selected] / 'account-session-marker').read_text() == str(3 - selected)
    if shared_active:
        assert session_store.load_session(runtime.workspace, 'douyin') is None
        assert session_store.session_generation(runtime.workspace, 'douyin') > generation
    else:
        assert session_store.load_session(runtime.workspace, 'douyin')['account_id'] == 1
        assert session_store.session_generation(runtime.workspace, 'douyin') == generation
    await api.close()


@pytest.mark.anyio
async def test_revoking_an_unknown_account_does_not_change_current_session(runtime, session_store):
    native_account(runtime)
    session_store.save_session(runtime.workspace, 'douyin', 1, state('douyin', 'active-content'),
        identity={'uid': '100', 'accountIdentityStatus': 'verified'}, capabilities={'creator': 'verified'})
    before = session_store.load_session(runtime.workspace, 'douyin')
    api = connections(runtime)
    with pytest.raises(HTTPException) as error:
        await api.revoke_account(999)
    assert error.value.status_code == 404
    assert session_store.load_session(runtime.workspace, 'douyin') == before
    await api.close()


@pytest.mark.anyio
async def test_revoking_an_account_cannot_cancel_another_account_login(runtime, monkeypatch, session_store):
    native_account(runtime, identifier=1)
    native_account(runtime, identifier=2)
    pause = asyncio.Event()
    install_platform(runtime, monkeypatch, pause=pause, unknown_read=True)
    api = connections(runtime)
    started = await api.start('douyin', account_id=2)
    await asyncio.sleep(0)
    with pytest.raises(HTTPException) as error:
        await api.revoke_account(1)
    assert error.value.status_code == 409
    assert api.poll(started['task_id'])['status'] not in {'error', 'expired'}
    with runtime.module.get_session() as db:
        assert all(account.status == 'active' for account in db.exec(select(runtime.module.DouyinAccount)).all())
    pause.set()
    await settled(api, started['task_id'])
    await api.close()


@pytest.mark.anyio
async def test_single_account_logout_fences_a_late_login_without_clearing_another_account(
    runtime, monkeypatch, session_store
):
    native_account(runtime, identifier=1)
    native_account(runtime, identifier=2)
    pause = asyncio.Event()
    install_platform(runtime, monkeypatch, pause=pause, unknown_read=True)
    api = connections(runtime)
    started = await api.start('douyin', account_id=1)
    await asyncio.sleep(0)
    logout = asyncio.create_task(api.revoke_account(1))
    await asyncio.sleep(0)
    pause.set()
    result = await logout
    assert result['account_id'] == 1
    assert api.poll(started['task_id'])['status'] == 'error'
    assert session_store.load_session(runtime.workspace, 'douyin') is None
    with runtime.module.get_session() as db:
        account = db.get(runtime.module.DouyinAccount, 1)
        assert account.status == 'invalid' and account.storage_state == ''
        other = db.get(runtime.module.DouyinAccount, 2)
        assert other.status == 'active' and other.storage_state
    await api.close()


@pytest.mark.anyio
async def test_single_logout_does_not_erase_a_newer_other_account_session(runtime, monkeypatch, session_store):
    native_account(runtime, identifier=1)
    native_account(runtime, identifier=2)
    install_platform(runtime, monkeypatch)
    api = connections(runtime)
    generation = session_store.begin_generation(runtime.workspace, 'douyin')
    session_store.save_session(runtime.workspace, 'douyin', 1, state('douyin', 'old-active'),
        identity={'uid': '100', 'accountIdentityStatus': 'verified'}, capabilities={'creator': 'verified'}, expected_generation=generation)
    finish = asyncio.Event()

    async def boundary():
        try:
            await finish.wait()
        except asyncio.CancelledError:
            await finish.wait()

    runner = asyncio.create_task(boundary())
    api._runners['target'] = runner
    api._records['target'] = {'platform': 'douyin', 'account_id': 1, '_generation': generation,
                              'status': 'waiting', '_leaves': []}
    await asyncio.sleep(0)
    logout = asyncio.create_task(api.revoke_account(1))
    await asyncio.sleep(0)
    newer = session_store.begin_generation(runtime.workspace, 'douyin')
    session_store.save_session(runtime.workspace, 'douyin', 2, state('douyin', 'new-active'),
        identity={'uid': '200'}, capabilities={'creator': 'verified'}, expected_generation=newer)
    finish.set()
    await logout
    assert session_store.load_session(runtime.workspace, 'douyin')['account_id'] == 2
    assert session_store.session_generation(runtime.workspace, 'douyin') == newer
    await api.close()


@pytest.mark.anyio
@pytest.mark.parametrize('identity', [
    {'uid': 'different-person', 'accountIdentityStatus': 'verified'},
    {'uid': '100'},
])
async def test_a_reused_internal_id_cannot_log_out_a_different_or_unverified_shared_subject(
    runtime, session_store, identity
):
    native_account(runtime)
    session_store.save_session(runtime.workspace, 'douyin', 1, state('douyin', 'shared-person'),
        identity=identity, capabilities={'creator': 'verified'})
    before = session_store.load_session(runtime.workspace, 'douyin')
    api = connections(runtime)
    with pytest.raises(HTTPException) as error:
        await api.revoke_account(1)
    assert error.value.status_code == 409
    assert session_store.load_session(runtime.workspace, 'douyin') == before
    with runtime.module.get_session() as db:
        account = db.get(runtime.module.DouyinAccount, 1)
        assert account.status == 'active' and account.storage_state
    await api.close()


@pytest.mark.anyio
async def test_logout_rechecks_owner_permission_after_waiting_for_the_account_lock(
    runtime, monkeypatch, session_store
):
    native_account(runtime)
    _, closed = install_platform(runtime, monkeypatch)
    session_store.save_session(runtime.workspace, 'douyin', 1, state('douyin', 'active-session'),
        identity={'uid': '100', 'accountIdentityStatus': 'verified'}, capabilities={'creator': 'verified'})
    before = session_store.load_session(runtime.workspace, 'douyin')
    with runtime.module.get_session() as db:
        prior_account = db.get(runtime.module.DouyinAccount, 1).model_dump()
        profile = Path(prior_account['profile_dir'])
    marker = profile / 'still-authenticated'
    marker.write_text('owned fixture')
    permission = {'owner': True}

    def authorize():
        if not permission['owner']:
            raise HTTPException(403, '账号操作权限已撤回')

    api = connections(runtime)
    await api._lock.acquire()
    logout = asyncio.create_task(api.revoke_account(1, authorize=authorize))
    await asyncio.sleep(0)
    permission['owner'] = False
    api._lock.release()
    with pytest.raises(HTTPException) as error:
        await logout
    assert error.value.status_code == 403
    assert session_store.load_session(runtime.workspace, 'douyin') == before
    with runtime.module.get_session() as db:
        assert db.get(runtime.module.DouyinAccount, 1).model_dump() == prior_account
    assert marker.read_text() == 'owned fixture'
    assert closed == []
    await api.close()


@pytest.mark.anyio
@pytest.mark.parametrize("force_reauth,expected", [(None, True), (False, False)])
async def test_native_login_preserves_default_reauth_and_checks_the_candidate_before_writing(
    runtime, monkeypatch, force_reauth, expected
):
    native_account(runtime)
    module = runtime.module
    seen = []
    with module.get_session() as db:
        account = db.get(module.DouyinAccount, 1)
        identity = SimpleNamespace(
            account_id=1,
            key=1,
            platform="douyin",
            profile_dir=account.profile_dir,
            proxy="",
            ua="test browser",
            identity_mode="native",
        )
    monkeypatch.setattr(
        module,
        "browser",
        SimpleNamespace(
            identity_for=lambda account: identity,
            environment_snapshot=lambda *args, **kwargs: {},
        ),
    )
    monkeypatch.setattr(module, "ensure_identity", lambda *args, **kwargs: None)

    async def login(browser, identity, **kwargs):
        seen.append(kwargs["force_reauth"])
        return True, json.dumps(state("douyin", "candidate")), "Different nickname"

    async def reject(candidate):
        assert candidate["account_id"] == 1
        raise ValueError("Unverified candidate must not overwrite this account")

    monkeypatch.setattr(module, "interactive_creator_login", login)
    options = {"before_persist": reject}
    if force_reauth is not None:
        options["force_reauth"] = force_reauth
    await module._run_login(
        "candidate", platform="douyin", creator=True, account_id=1, **options
    )
    assert seen == [expected]
    assert module.login_tasks["candidate"]["status"] == "error"
    with module.get_session() as db:
        account = db.get(module.DouyinAccount, 1)
        assert (
            account.creator_storage_state == "" and account.nickname == "Same nickname"
        )


@pytest.mark.anyio
@pytest.mark.parametrize("platform", ["douyin", "xhs"])
async def test_remote_login_is_task_scoped_and_revoked_after_verified_completion(runtime, monkeypatch, platform):
    install_platform(runtime, monkeypatch)
    real_runner = runtime.module._run_login
    created = []
    bound = []

    class Remote:
        def __init__(self, project, actor, platform, task, **callbacks):
            self.owner = project, actor, platform, task
            self.callbacks = callbacks
            self.stopped = False

        def public(self):
            return {"id": "remote-test-session", "platform": platform}

        def bind_context(self, context, page=None):
            bound.append((context, page))

        async def stop(self, cancel=True):
            self.stopped = True
            if cancel:
                self.callbacks["cancel"]()

    def create(*args, **kwargs):
        session = Remote(*args, **kwargs)
        created.append(session)
        return session

    monkeypatch.setitem(sys.modules, "easel.login_remote", SimpleNamespace(
        registry=SimpleNamespace(create=create), enabled=lambda: True,
    ))
    contexts = [object(), object()]
    pages = [object(), object()]

    async def runner(task, **kwargs):
        index = int(kwargs["creator"])
        assert 0 < kwargs["timeout_seconds"] <= 600
        kwargs.pop("context_callback")(contexts[index], page=pages[index])
        kwargs.pop("timeout_seconds")
        await real_runner(task, **kwargs)

    monkeypatch.setattr(runtime.module, "_run_login", runner)
    api = connections(runtime)
    started = await api.start(platform, actor_id="owner-one")
    assert started["remote"] == {"id": "remote-test-session", "platform": platform}
    assert created[0].owner == ("default", "owner-one", platform, started["task_id"])
    assert created[0].callbacks["is_active"]()
    await api._runners[started["task_id"]]
    result = api.poll(started["task_id"], actor_id="owner-one")
    assert result["status"] == "confirmed"
    assert "remote" not in result
    assert bound == list(zip(contexts, pages))
    await asyncio.wait_for(asyncio.gather(*tuple(api._remote_finalizers)), 1)
    assert created[0].stopped and not created[0].callbacks["is_active"]()
    await api.close()


@pytest.mark.anyio
@pytest.mark.parametrize("platform,creator", [("douyin", False), ("douyin", True), ("xhs", False), ("xhs", True)])
async def test_native_remote_binding_reaches_the_selected_login_adapter(runtime, monkeypatch, platform, creator):
    native_account(runtime, platform)
    module = runtime.module
    with module.get_session() as db:
        account = db.get(module.DouyinAccount, 1)
        identity = SimpleNamespace(account_id=1, key=1, platform=platform,
                                   profile_dir=account.profile_dir, proxy="", ua="", identity_mode="native")
    monkeypatch.setattr(module, "browser", SimpleNamespace(
        identity_for=lambda _: identity, environment_snapshot=lambda *a, **k: {},
    ))
    monkeypatch.setattr(module, "ensure_identity", lambda *a, **k: None)
    selected = {("douyin", False): "interactive_login", ("douyin", True): "interactive_creator_login",
                ("xhs", False): "interactive_xhs_login", ("xhs", True): "interactive_xhs_creator_login"}
    observed = []
    context, page = object(), object()

    async def login(browser, current, **kwargs):
        assert kwargs["timeout_seconds"] == 550
        kwargs["context_callback"](context, page=page)
        return False, "", ""

    monkeypatch.setattr(module, selected[(platform, creator)], login)
    await module._run_login("remote-adapter", platform=platform, creator=creator, account_id=1,
                            context_callback=lambda ctx, **kwargs: observed.append((ctx, kwargs["page"])),
                            timeout_seconds=550)
    assert observed == [(context, page)]
    assert module.login_tasks["remote-adapter"]["status"] != "confirmed"


@pytest.mark.anyio
@pytest.mark.parametrize("function", ["interactive_login", "interactive_creator_login", "interactive_xhs_login", "interactive_xhs_creator_login"])
async def test_remote_adapter_binds_the_login_page_before_navigation(runtime, function):
    module = importlib.import_module(runtime.namespace + ".browser.login")
    observed, closed = [], []

    async def unreachable(*args, **kwargs):
        raise RuntimeError("fixture stops before network navigation")

    page = SimpleNamespace(goto=unreachable)

    async def new_page():
        return page

    async def close():
        closed.append(True)

    context = SimpleNamespace(new_page=new_page, close=close, pages=[object()])

    async def get_context(identity):
        return context

    @asynccontextmanager
    async def visible(identity):
        yield page

    manager = SimpleNamespace(open_headed=get_context, context_for=get_context, visible_page=visible)
    with pytest.raises(RuntimeError, match="fixture stops"):
        await getattr(module, function)(manager, SimpleNamespace(),
            context_callback=lambda ctx, **kwargs: observed.append((ctx, kwargs["page"])))
    assert observed == [(context, page)]
    if function.startswith("interactive_xhs") is False:
        assert closed == [True]


@pytest.mark.anyio
async def test_cancel_before_native_runner_starts_releases_remote_without_clearing_account(runtime, monkeypatch):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    native_account(runtime)
    install_platform(runtime, monkeypatch)
    with runtime.module.get_session() as db:
        before = db.get(runtime.module.DouyinAccount, 1).model_dump()
    api = connections(runtime)
    started = await api.start("douyin", account_id=1, actor_id="owner-one")
    session = registry.get(started["remote"]["id"], "default", "owner-one", "douyin")
    # A cancellation can arrive before asyncio first enters the login coroutine.
    session._cancel()
    await session.stop(cancel=False)
    await asyncio.gather(api._runners[started["task_id"]], return_exceptions=True)
    result = api.poll(started["task_id"], actor_id="owner-one")
    assert result["status"] in {"error", "expired"}
    assert result["stage"] == "canceled"
    assert "remote" not in result
    with runtime.module.get_session() as db:
        assert db.get(runtime.module.DouyinAccount, 1).model_dump() == before
    await api.close()


@pytest.mark.anyio
async def test_revoked_login_request_cancels_the_waiting_browser_without_erasing_account(runtime, monkeypatch):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    native_account(runtime)
    install_platform(runtime, monkeypatch, unknown_read=True)
    with runtime.module.get_session() as db:
        before = db.get(runtime.module.DouyinAccount, 1).model_dump()
    entered, flushed = asyncio.Event(), asyncio.Event()

    async def waiting_browser(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            flushed.set()

    monkeypatch.setattr(runtime.module, "_run_login", waiting_browser)
    authorized = True

    def authorize():
        if not authorized:
            raise HTTPException(401, "request session ended")

    api = connections(runtime)
    started = await api.start("douyin", account_id=1, actor_id="owner-one", authorize=authorize)
    await asyncio.wait_for(entered.wait(), 1)
    session = registry.get(started["remote"]["id"], "default", "owner-one", "douyin")
    authorized = False
    try:
        assert session._is_active() is False
        await asyncio.wait_for(flushed.wait(), 1)
        await asyncio.gather(api._runners[started["task_id"]], return_exceptions=True)
        result = api.poll(started["task_id"], actor_id="owner-one")
        assert result["status"] == "error" and "remote" not in result
        with runtime.module.get_session() as db:
            assert db.get(runtime.module.DouyinAccount, 1).model_dump() == before
    finally:
        await api.close()


@pytest.mark.anyio
async def test_login_waiting_for_coordinator_lock_rechecks_its_request_session(runtime, monkeypatch):
    install_platform(runtime, monkeypatch)
    api = connections(runtime)
    authorized = True

    def authorize():
        if not authorized:
            raise HTTPException(401, "session ended")

    async with api._lock:
        task = asyncio.create_task(api.start("douyin", actor_id="owner", authorize=authorize))
        await asyncio.sleep(0)
        authorized = False
    with pytest.raises(HTTPException) as ended:
        await task
    assert ended.value.status_code == 401
    assert api._records == {}
    await api.close()


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_remote_native_login_flushes_only_its_account_context(runtime, monkeypatch, cancel):
    native_account(runtime)
    module = runtime.module
    closed, entered = [], asyncio.Event()
    with module.get_session() as db:
        before = db.get(module.DouyinAccount, 1).model_dump()
    identity = SimpleNamespace(account_id=1, key=1, platform="douyin",
                               profile_dir=before["profile_dir"], proxy="", ua="", identity_mode="native")

    async def close_context(key):
        closed.append(key)

    monkeypatch.setattr(module, "browser", SimpleNamespace(identity_for=lambda _: identity,
        environment_snapshot=lambda *a, **k: {}, close_context=close_context))
    monkeypatch.setattr(module, "ensure_identity", lambda *a, **k: None)

    async def login(*args, **kwargs):
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        return False, "", ""

    monkeypatch.setattr(module, "interactive_login", login)
    task = asyncio.create_task(module._run_login("remote-close", platform="douyin", account_id=1,
                                                 context_callback=lambda *a, **k: None))
    await entered.wait()
    if cancel:
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert closed == [1]
    with module.get_session() as db:
        assert db.get(module.DouyinAccount, 1).model_dump() == before


@pytest.mark.anyio
async def test_remote_capacity_rejection_preserves_verified_session_and_creates_no_login(runtime, monkeypatch, session_store):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    native_account(runtime)
    install_platform(runtime, monkeypatch)
    generation = session_store.begin_generation(runtime.workspace, "douyin")
    session_store.save_session(runtime.workspace, "douyin", 1, state("douyin", "existing-session"),
        identity={"uid": "100", "accountIdentityStatus": "verified"},
        capabilities={"creator": "verified"}, expected_generation=generation)
    before = session_store.load_session(runtime.workspace, "douyin")

    def capacity_full(*args, **kwargs):
        raise HTTPException(409, "another login is still using the browser slot")

    monkeypatch.setattr(login_remote, "registry", SimpleNamespace(create=capacity_full))
    api = connections(runtime)
    with pytest.raises(HTTPException) as busy:
        await api.start("douyin", actor_id="owner", account_id=1)
    assert busy.value.status_code == 409
    assert session_store.session_generation(runtime.workspace, "douyin") == generation
    assert session_store.load_session(runtime.workspace, "douyin") == before
    assert api._records == {} and api._runners == {}
    await api.close()


@pytest.mark.anyio
async def test_remote_close_waits_until_native_browser_cleanup_has_finished(runtime, monkeypatch):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    native_account(runtime)
    install_platform(runtime, monkeypatch, unknown_read=True)
    entered, cleanup_started, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def waiting_browser(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()

    monkeypatch.setattr(runtime.module, "_run_login", waiting_browser)
    api = connections(runtime)
    started = await api.start("douyin", actor_id="owner", account_id=1)
    await asyncio.wait_for(entered.wait(), 1)
    session = registry.get(started["remote"]["id"], "default", "owner", "douyin")
    closing = asyncio.create_task(session.stop())
    try:
        await asyncio.wait_for(cleanup_started.wait(), 1)
        await asyncio.sleep(0)
        assert not closing.done(), "The browser slot was released while native cleanup was still running"
        release_cleanup.set()
        await asyncio.wait_for(closing, 1)
        assert api._runners[started["task_id"]].done()
    finally:
        release_cleanup.set()
        await api.close()
        await asyncio.gather(closing, return_exceptions=True)
        await registry.close()


@pytest.mark.anyio
@pytest.mark.parametrize("remote_enabled,actor", [(False, "owner"), (True, None)])
async def test_local_login_is_not_blocked_by_remote_browser_capacity(runtime, monkeypatch, remote_enabled, actor):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1" if remote_enabled else "0")
    monkeypatch.setenv("EASEL_REMOTE_LOGIN_MAX_ACTIVE", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    registry.create("other-project", "other-owner", "facebook", "other-task")
    install_platform(runtime, monkeypatch)
    api = connections(runtime)
    try:
        started = await api.start("douyin", actor_id=actor)
        assert "remote" not in started
        await api._runners[started["task_id"]]
        assert api.poll(started["task_id"], actor_id=actor)["status"] == "confirmed"
    finally:
        await api.close()
        await registry.close()


@pytest.mark.anyio
async def test_failed_generation_write_releases_reserved_remote_capacity(runtime, monkeypatch, session_store):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    monkeypatch.setenv("EASEL_REMOTE_LOGIN_MAX_ACTIVE", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    install_platform(runtime, monkeypatch)
    api = connections(runtime)

    def failing_generation(*args, **kwargs):
        raise OSError("fixture cannot write generation")

    monkeypatch.setattr(session_store, "begin_generation", failing_generation)
    try:
        with pytest.raises(OSError, match="fixture cannot write"):
            await api.start("douyin", actor_id="owner")
        assert api._records == {} and api._runners == {}
        assert registry.create("other-project", "other-owner", "facebook", "next-task").public()["platform"] == "facebook"
    finally:
        await api.close()
        await registry.close()


@pytest.mark.anyio
async def test_confirmed_native_login_releases_slot_only_after_cleanup_without_self_wait(runtime, monkeypatch):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    monkeypatch.setenv("EASEL_REMOTE_LOGIN_MAX_ACTIVE", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    install_platform(runtime, monkeypatch)
    api = connections(runtime)
    cleanup_started, release_cleanup = asyncio.Event(), asyncio.Event()
    original_cleanup = api._cleanup_unbound

    async def cleanup(record):
        cleanup_started.set()
        await release_cleanup.wait()
        await original_cleanup(record)

    monkeypatch.setattr(api, "_cleanup_unbound", cleanup)
    try:
        started = await api.start("douyin", actor_id="owner")
        await asyncio.wait_for(cleanup_started.wait(), 1)
        assert api.poll(started["task_id"], actor_id="owner")["status"] == "confirmed"
        with pytest.raises(HTTPException) as busy:
            registry.create("other-project", "other-owner", "facebook", "next-task")
        assert busy.value.status_code == 409
        release_cleanup.set()
        await asyncio.wait_for(api._runners[started["task_id"]], 1)
        await asyncio.wait_for(asyncio.gather(*tuple(api._remote_finalizers)), 1)
        assert registry.create("other-project", "other-owner", "facebook", "next-task").public()["platform"] == "facebook"
    finally:
        release_cleanup.set()
        await api.close()
        await registry.close()


@pytest.mark.anyio
@pytest.mark.parametrize("remote,explicit,want", [
    (False, None, "https://www.douyin.com/"),
    (True, None, "https://creator.douyin.com/"),
    (True, "https://www.douyin.com/", "https://www.douyin.com/"),
])
async def test_remote_douyin_uses_official_creator_login_without_changing_local_or_explicit_entry(
    runtime, remote, explicit, want
):
    module = importlib.import_module(runtime.namespace + ".browser.login")
    navigated, closed = [], []

    async def goto(url, **kwargs):
        navigated.append(url)
        raise RuntimeError("fixture stops before navigation")

    page = SimpleNamespace(goto=goto)

    async def new_page():
        return page

    async def close():
        closed.append(True)

    context = SimpleNamespace(new_page=new_page, close=close)

    async def open_headed(identity):
        return context

    options = {}
    if remote:
        options["context_callback"] = lambda ctx, **kwargs: None
    if explicit is not None:
        options["start_url"] = explicit
    with pytest.raises(RuntimeError, match="fixture stops"):
        await module.interactive_login(SimpleNamespace(open_headed=open_headed), SimpleNamespace(), **options)
    assert navigated == [want]
    assert closed == [True]


@pytest.mark.anyio
@pytest.mark.parametrize("remote,want", [
    (False, "https://www.xiaohongshu.com/"),
    (True, "https://www.xiaohongshu.com/login"),
])
async def test_remote_xhs_uses_explicit_login_while_local_keeps_homepage(runtime, remote, want):
    module = importlib.import_module(runtime.namespace + ".browser.login")
    navigated = []

    async def goto(url, **kwargs):
        navigated.append(url)
        raise RuntimeError("fixture stops before navigation")

    page = SimpleNamespace(goto=goto)
    context = SimpleNamespace(pages=[])

    async def context_for(identity):
        return context

    @asynccontextmanager
    async def visible_page(identity):
        yield page

    manager = SimpleNamespace(context_for=context_for, visible_page=visible_page)
    options = {"context_callback": lambda ctx, **kwargs: None} if remote else {}
    with pytest.raises(RuntimeError, match="fixture stops"):
        await module.interactive_xhs_login(manager, SimpleNamespace(), **options)
    assert navigated == [want]


@pytest.mark.anyio
@pytest.mark.parametrize("platform,remote,configured,fail", [
    ("xhs", True, True, False), ("xhs", True, False, False),
    ("xhs", False, True, False), ("douyin", True, True, False),
    ("xhs", True, True, True),
])
async def test_backend_xhs_proxy_is_scoped_to_remote_xhs_and_never_returned(runtime, monkeypatch, platform, remote, configured, fail):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1" if remote else "0")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    proxy = "http://fixture-user:fixture-proxy-password@127.0.0.1:20810"
    if configured:
        monkeypatch.setenv("EASEL_XHS_LOGIN_PROXY", proxy)
    else:
        monkeypatch.delenv("EASEL_XHS_LOGIN_PROXY", raising=False)
    install_platform(runtime, monkeypatch)
    original = runtime.module._run_login
    used = []

    async def login(task, **kwargs):
        used.append(kwargs["proxy_choice"])
        if fail:
            raise RuntimeError(proxy)
        await original(task, **kwargs)

    monkeypatch.setattr(runtime.module, "_run_login", login)
    api = connections(runtime)
    try:
        started = await api.start(platform, actor_id="owner")
        await api._runners[started["task_id"]]
        result = api.poll(started["task_id"], actor_id="owner")
        expected = proxy if platform == "xhs" and remote and configured else ""
        assert used == ([expected] if fail else [expected, expected])
        assert result["status"] == ("error" if fail else "confirmed")
        assert "fixture-proxy-password" not in json.dumps(started) + json.dumps(result)
        assert "fixture-user" not in json.dumps(started) + json.dumps(result)
    finally:
        await api.close()
        await registry.close()


@pytest.mark.anyio
@pytest.mark.parametrize("check_during_creator", [True, False])
async def test_verified_read_login_survives_closing_creator_verification_and_releases_other_platform(
    runtime, monkeypatch, session_store, check_during_creator
):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    monkeypatch.setenv("EASEL_REMOTE_LOGIN_MAX_ACTIVE", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    install_platform(runtime, monkeypatch)
    original = runtime.module._run_login
    creator_waiting, creator_closed = asyncio.Event(), asyncio.Event()

    async def login(task_id, **kwargs):
        if kwargs["creator"]:
            runtime.module.login_tasks[task_id] = {
                "status": "waiting", "platform": kwargs["platform"],
                "account_id": kwargs["account_id"],
            }
            creator_waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                creator_closed.set()
        else:
            await original(task_id, **kwargs)

    monkeypatch.setattr(runtime.module, "_run_login", login)
    api = connections(runtime)
    try:
        started = await api.start("xhs", actor_id="owner", new_account=True)
        await asyncio.wait_for(creator_waiting.wait(), 1)
        if check_during_creator:
            pending = api.poll(started["task_id"], actor_id="owner")
            assert pending.get("loginSaved") is True
            assert pending["capabilities"]["comments"] == "verified"
            assert pending["capabilities"]["creator"] == "needs_verification"
            assert session_store.load_session(runtime.workspace, "xhs") is not None
        session = registry.get(started["remote"]["id"], "default", "owner", "xhs")
        await asyncio.wait_for(session.stop(), 2)
        result = api.poll(started["task_id"], actor_id="owner")
        assert result["status"] == "confirmed" and result["completion"] == "partial"
        assert result["loginSaved"] is True
        assert result["capabilities"]["comments"] == "verified"
        assert result["capabilities"]["creator"] == "needs_verification"
        assert result["capabilities"]["publish"] == "needs_verification"
        saved = session_store.load_session(runtime.workspace, "xhs")
        assert saved["account_id"] == result["account_id"]
        assert saved["identity"]["accountId"] == "100"
        assert saved["storage_state"] == state("xhs", "read-session")
        with runtime.module.get_session() as db:
            account = db.get(runtime.module.DouyinAccount, result["account_id"])
            assert account.status == "active" and account.storage_state
            assert account.creator_storage_state == ""
        from easel.account_directory import build_account_directory, read_native_accounts
        directory = build_account_directory("default", [{"platform": "xiaohongshu", "name": "小红书",
            "backend": "xhs", "supported": True, "loggedIn": False}],
            read_native_accounts(runtime.workspace), shared_sessions={"xiaohongshu": saved})
        row = directory["accounts"][0]
        assert row["loggedIn"] is True and row["status"] == "logged_in" and row["identityVerified"] is True
        assert row["capabilities"]["comments"] == "verified" and row["capabilities"]["creator"] == "needs_verification"
        assert creator_closed.is_set()
        assert runtime.module._active_login_task() is None
        assert api._runners[started["task_id"]].done()
        other = await api.start("douyin", actor_id="owner", new_account=True)
        assert other["platform"] == "douyin"
        await registry.get(other["remote"]["id"], "default", "owner", "douyin").stop()
        await api.revoke_account(result["account_id"])
        assert session_store.load_session(runtime.workspace, "xhs") is None
        assert api.poll(started["task_id"], actor_id="owner")["loginSaved"] is False
    finally:
        await api.close()
        await registry.close()


def test_revoking_a_partial_checkpoint_cannot_erase_a_newer_generation(tmp_path, session_store):
    first = session_store.begin_generation(tmp_path, "xhs")
    session_store.save_session(tmp_path, "xhs", 1, state("xhs", "first"),
        identity={"accountId": "100"}, capabilities={"comments": "verified"}, expected_generation=first)
    second = session_store.begin_generation(tmp_path, "xhs")
    session_store.save_session(tmp_path, "xhs", 2, state("xhs", "second"),
        identity={"accountId": "200"}, capabilities={"comments": "verified"}, expected_generation=second)
    assert session_store.revoke_session(tmp_path, "xhs", expected_generation=first) is False
    assert session_store.load_session(tmp_path, "xhs")["account_id"] == 2
    assert session_store.revoke_session(tmp_path, "xhs", expected_generation=second) is True
    assert session_store.load_session(tmp_path, "xhs") is None


@pytest.mark.anyio
@pytest.mark.parametrize("verified", [True, False])
async def test_existing_native_session_recovers_partial_only_after_authoritative_read_proof(
    runtime, monkeypatch, session_store, verified
):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    native_account(runtime, "xhs")
    with runtime.module.get_session() as db:
        account = db.get(runtime.module.DouyinAccount, 1)
        account.status = "invalid"
        db.add(account)
        db.commit()
    install_platform(runtime, monkeypatch, unknown_read=not verified, fail_creator=True)
    waiting = asyncio.Event()

    async def login(task, **kwargs):
        runtime.module.login_tasks[task] = {"status": "waiting", "platform": "xhs", "account_id": 1}
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime.module, "_run_login", login)
    api = connections(runtime)
    try:
        assert session_store.load_session(runtime.workspace, "xhs") is None
        started = await api.start("xhs", account_id=1, actor_id="owner")
        await asyncio.wait_for(waiting.wait(), 1)
        pending = api.poll(started["task_id"], actor_id="owner")
        assert pending["loginSaved"] is verified
        assert pending["stage"] == ("creator" if verified else "read")
        saved = session_store.load_session(runtime.workspace, "xhs")
        assert (saved is not None) is verified
        with runtime.module.get_session() as db:
            account = db.get(runtime.module.DouyinAccount, 1)
            assert account.status == ("active" if verified else "invalid")
        await registry.get(started["remote"]["id"], "default", "owner", "xhs").stop()
        final = api.poll(started["task_id"], actor_id="owner")
        assert final["status"] == ("confirmed" if verified else "error")
    finally:
        await api.close()
        await registry.close()


@pytest.mark.anyio
async def test_close_cannot_preserve_a_checkpoint_after_creator_identity_mismatch(runtime, monkeypatch, session_store):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    install_platform(runtime, monkeypatch, creator_subject="200")
    original = runtime.module._run_login
    mismatch_seen = asyncio.Event()

    async def login(task, **kwargs):
        await original(task, **kwargs)
        if kwargs["creator"]:
            mismatch_seen.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(runtime.module, "_run_login", login)
    api = connections(runtime)
    try:
        started = await api.start("xhs", actor_id="owner", new_account=True)
        await asyncio.wait_for(mismatch_seen.wait(), 1)
        assert api._records[started["task_id"]]["_mismatch"] is True
        await registry.get(started["remote"]["id"], "default", "owner", "xhs").stop()
        result = api.poll(started["task_id"], actor_id="owner")
        assert result["status"] == "error" and result["loginSaved"] is False
        assert result["capabilities"]["comments"] == "needs_verification"
        assert session_store.load_session(runtime.workspace, "xhs") is None
        with runtime.module.get_session() as db:
            assert db.get(runtime.module.DouyinAccount, result["account_id"]).status == "invalid"
    finally:
        await api.close()
        await registry.close()


def publish_newer_same_account(runtime, session_store, account_id, platform):
    generation = session_store.begin_generation(runtime.workspace, platform)
    snapshot = state(platform, "newer-verified-session")
    identity = {"accountId": "100", "accountIdKind": "user_id" if platform == "xhs" else "uid",
                "accountIdentityStatus": "verified"}
    with runtime.module.get_session() as db:
        account = db.get(runtime.module.DouyinAccount, account_id)
        account.status = "active"
        account.storage_state = json.dumps(snapshot)
        db.add(account)
        db.commit()
    session_store.save_session(runtime.workspace, platform, account_id, snapshot,
        identity=identity, capabilities={"comments": "verified"}, expected_generation=generation)
    return session_store.load_session(runtime.workspace, platform)


@pytest.mark.anyio
async def test_stale_mismatch_cannot_invalidate_a_newer_same_account_login(runtime, monkeypatch, session_store):
    install_platform(runtime, monkeypatch, final_subject="200")
    api = connections(runtime)
    original = api._read_proof
    calls, newer = 0, None

    async def read(platform, identity):
        nonlocal calls, newer
        result = await original(platform, identity)
        calls += 1
        if calls == 2:
            newer = publish_newer_same_account(runtime, session_store, identity.account_id, platform)
        return result

    monkeypatch.setattr(api, "_read_proof", read)
    try:
        started = await api.start("douyin")
        await api._runners[started["task_id"]]
        assert newer is not None
        assert session_store.load_session(runtime.workspace, "douyin") == newer
        with runtime.module.get_session() as db:
            account = db.get(runtime.module.DouyinAccount, newer["account_id"])
            assert account.status == "active"
            assert json.loads(account.storage_state) == newer["storage_state"]
    finally:
        await api.close()


@pytest.mark.anyio
async def test_delayed_mismatch_close_cannot_invalidate_a_newer_same_account_login(runtime, monkeypatch, session_store):
    from easel import login_remote

    monkeypatch.setenv("EASEL_REMOTE_LOGIN", "1")
    registry = login_remote.RemoteLoginRegistry()
    monkeypatch.setattr(login_remote, "registry", registry)
    install_platform(runtime, monkeypatch, creator_subject="200")
    original = runtime.module._run_login
    mismatch_seen, cleanup_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def login(task, **kwargs):
        await original(task, **kwargs)
        if kwargs["creator"]:
            mismatch_seen.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await release.wait()

    monkeypatch.setattr(runtime.module, "_run_login", login)
    api = connections(runtime)
    closing = None
    try:
        started = await api.start("xhs", actor_id="owner", new_account=True)
        await asyncio.wait_for(mismatch_seen.wait(), 1)
        session = registry.get(started["remote"]["id"], "default", "owner", "xhs")
        closing = asyncio.create_task(session.stop())
        await asyncio.wait_for(cleanup_started.wait(), 1)
        account_id = api.poll(started["task_id"], actor_id="owner")["account_id"]
        newer = publish_newer_same_account(runtime, session_store, account_id, "xhs")
        release.set()
        await asyncio.wait_for(closing, 2)
        assert session_store.load_session(runtime.workspace, "xhs") == newer
        with runtime.module.get_session() as db:
            account = db.get(runtime.module.DouyinAccount, account_id)
            assert account.status == "active"
            assert json.loads(account.storage_state) == newer["storage_state"]
    finally:
        release.set()
        await api.close()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await registry.close()


@pytest.mark.anyio
@pytest.mark.parametrize("superseded", [False, True])
async def test_final_read_error_invalidates_only_its_current_generation(runtime, monkeypatch, session_store, superseded):
    _, closed = install_platform(runtime, monkeypatch)
    api = connections(runtime)
    original = api._read_proof
    calls, newer, account_id = 0, None, None

    async def read(platform, identity):
        nonlocal calls, newer, account_id
        result = await original(platform, identity)
        calls += 1
        if calls == 2:
            account_id = identity.account_id
            if superseded:
                newer = publish_newer_same_account(runtime, session_store, account_id, platform)
            raise RuntimeError("fixture final identity endpoint unavailable")
        return result

    monkeypatch.setattr(api, "_read_proof", read)
    try:
        started = await api.start("douyin")
        await api._runners[started["task_id"]]
        assert account_id is not None
        with runtime.module.get_session() as db:
            account = db.get(runtime.module.DouyinAccount, account_id)
            assert account.status == ("active" if superseded else "invalid")
            if superseded:
                assert json.loads(account.storage_state) == newer["storage_state"]
        if superseded:
            assert session_store.load_session(runtime.workspace, "douyin") == newer
            assert closed == []
        else:
            assert closed == [account_id]
    finally:
        await api.close()
