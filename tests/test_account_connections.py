"""The unified login must verify identity before sharing either session."""

import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
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
