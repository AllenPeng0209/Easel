import importlib

import pytest

from easel.operations import OperationsManager


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_native_runtime_selects_interaction_only_scheduling(tmp_path):
    manager = OperationsManager(tmp_path)
    runtime = await manager.get("default", start=False)
    assert getattr(runtime.module.cfg.engine, "interaction_only", False) is True
    await manager.close()


@pytest.mark.access_integration
def test_full_workbench_and_noninteraction_routes_are_retired(tmp_path):
    module = importlib.import_module("easel.operations_web")
    assert hasattr(module, "interaction_path"), (
        "Embedded API still exposes the full operations workbench"
    )
    for method, path in [
        ("GET", "/workbench"),
        ("GET", "/static/app.js"),
        ("POST", "/api/publish"),
        ("GET", "/api/monitors"),
        ("POST", "/api/collections"),
        ("POST", "/api/share-download"),
    ]:
        assert module.interaction_path(method, path) is False
    assert module.interaction_path("GET", "/api/accounts")
    assert module.interaction_path("POST", "/api/accounts/1/dm/sync")


@pytest.mark.anyio
async def test_interaction_loop_does_not_run_collection_download_or_publish_jobs(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    manager = OperationsManager(tmp_path)
    runtime = await manager.get("default", start=False)
    engine = runtime.module.MonitorEngine(runtime.module.cfg, SimpleNamespace())
    runtime.module.cfg.engine.interaction_only = True
    seen = []

    def async_step(name):
        async def action(*_args):
            seen.append(name)
            if name == "_process_action_tasks":
                engine._running = False

        return action

    for name in (
        "_collect_idle_browser_sessions",
        "_scan_once",
        "_scan_comment_watches",
        "_scan_danmaku_watches",
        "_retry_failed",
        "_process_risk_recovery",
        "_check_accounts",
        "_check_work_health",
        "_process_xhs_dm_automation",
        "_process_publish",
        "_process_comment_rules",
        "_process_comment_tasks",
        "_process_action_tasks",
        "_process_collection_jobs",
    ):
        monkeypatch.setattr(engine, name, async_step(name))
    monkeypatch.setattr(engine, "_prune_risk_events_if_due", lambda *_args: None)
    monkeypatch.setattr(engine, "_monitor_poll_seconds", lambda: 0)
    engine._running = True
    await engine._loop()
    assert not set(seen) & {
        "_scan_once",
        "_scan_danmaku_watches",
        "_process_publish",
        "_process_collection_jobs",
        "_process_comment_rules",
    }
    assert {
        "_check_accounts",
        "_process_xhs_dm_automation",
        "_process_comment_tasks",
        "_process_action_tasks",
    } <= set(seen)
    await manager.close()
