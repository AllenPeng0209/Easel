"""Native operations must run in-process with independently owned project state."""

import importlib
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


def manager(tmp_path):
    path = Path(__file__).parents[1] / "easel" / "operations.py"
    assert path.is_file(), "Easel has no embedded operations runtime"
    return importlib.import_module("easel.operations").OperationsManager(tmp_path)


@pytest.mark.anyio
async def test_runtime_owns_one_unified_connection_service_per_project(tmp_path):
    owner = manager(tmp_path)
    first = await owner.get('default', start=False)
    second = await owner.get('0123456789abcdef', start=False)
    assert hasattr(first, 'connections'), 'Account login has no owned coordinator'
    assert first.connections is not second.connections
    assert first.connections.runtime is first
    await owner.close()


@pytest.mark.anyio
async def test_project_databases_and_orm_classes_are_independent(tmp_path):
    owner = manager(tmp_path)
    first = await owner.get("default", start=False)
    second = await owner.get("0123456789abcdef", start=False)
    assert first.module is not second.module
    assert first.module.cfg.db_path != second.module.cfg.db_path
    assert first.module.cfg.engine.profiles_dir != second.module.cfg.engine.profiles_dir
    for runtime, name in ((first, "first owner"), (second, "second owner")):
        with runtime.module.get_session() as db:
            db.add(runtime.module.DouyinAccount(id=1, platform="douyin", nickname=name))
            db.commit()
    for runtime, name in ((first, "first owner"), (second, "second owner")):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime.app), base_url="http://localhost"
        ) as client:
            response = await client.get("/api/accounts")
            assert response.status_code == 200
            assert [(a["id"], a["nickname"]) for a in response.json()] == [(1, name)]
    await owner.close()


@pytest.mark.anyio
async def test_cached_comments_and_dms_are_available_without_an_external_service(
    tmp_path,
):
    owner = manager(tmp_path)
    runtime = await owner.get("default", start=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime.app), base_url="http://localhost"
    ) as client:
        for path in (
            "/api/account-works?account_id=1",
            "/api/dm/conversations?account_id=1",
            "/api/dm/messages?account_id=1&conv_id=none",
            "/api/account-actions",
            "/api/comment-tasks",
        ):
            result = await client.get(path)
            assert result.status_code == 200
            assert result.json() == []
        result = await client.get("/api/dm/inbox")
        assert result.status_code == 200 and result.json()["items"] == []
    await owner.close()


@pytest.mark.anyio
@pytest.mark.parametrize("relative_profile", [False, True])
async def test_migration_preserves_accounts_and_holds_imported_automation(
    tmp_path, relative_profile
):
    import json

    source = manager(tmp_path / "source")
    old = await source.get("default", start=False)
    profiles = Path(old.module.cfg.engine.profiles_dir)
    profile = profiles / "account-one"
    profile.mkdir(parents=True)
    (profile / "Preferences").write_text('{"fixture":true}')
    with old.module.get_session() as db:
        db.add(
            old.module.DouyinAccount(
                id=1,
                platform="douyin",
                nickname="owner",
                profile_dir=str(profile.relative_to(tmp_path / "source"))
                if relative_profile
                else str(profile),
                storage_state="{}",
            )
        )
        db.add(
            old.module.PublishTask(
                account_id=1, platform="douyin", status="pending", title="held task"
            )
        )
        db.add(
            old.module.CommentRule(platform="douyin", name="held rule", enabled=True)
        )
        db.commit()
    source_root = tmp_path / "source"
    (source_root / "config.yaml").write_text(
        json.dumps(
            {
                "storage": {"db_path": old.module.cfg.db_path},
                "engine": {"profiles_dir": str(profiles)},
            }
        )
    )
    target = manager(tmp_path / "target")
    assert hasattr(target, "import_local"), (
        "Native account/session migration has not been implemented"
    )
    result = await target.import_local("default", source_root)
    assert result["accounts"] == 1
    new = await target.get("default", start=False)
    with new.module.get_session() as db:
        account = db.get(new.module.DouyinAccount, 1)
        assert account.nickname == "owner"
        assert Path(account.profile_dir).is_relative_to(new.directory)
        assert (
            Path(account.profile_dir) / "Preferences"
        ).read_text() == '{"fixture":true}'
        assert db.get(new.module.PublishTask, 1).status == "draft"
        assert db.get(new.module.CommentRule, 1).enabled is False
    with old.module.get_session() as db:
        assert db.get(old.module.PublishTask, 1).status == "pending"
    with pytest.raises(ValueError):
        await target.import_local("default", source_root)
    await target.close()
    await source.close()
