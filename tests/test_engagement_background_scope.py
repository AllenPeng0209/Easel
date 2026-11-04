"""Interaction-only runtimes cannot revive legacy social or webhook work."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from easel.operations import OperationsManager


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
async def runtime(tmp_path):
    manager = OperationsManager(tmp_path)
    value = await manager.get('default', start=False)
    yield value
    await manager.close()


def add_action(runtime, action, *, status='pending'):
    module = runtime.module
    with module.get_session() as session:
        task = module.AccountActionTask(account_id=1, platform='douyin', action=action,
                                        target_uid='fixture-peer', content='fixture reply', status=status)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['follow', 'unfollow'])
async def test_legacy_social_task_is_rejected_before_account_or_network_guards(runtime, monkeypatch, action):
    engine = runtime.module.MonitorEngine(runtime.module.cfg, SimpleNamespace())
    task_id = add_action(runtime, action)

    @asynccontextmanager
    async def forbidden_guard(*_args, **_kwargs):
        pytest.fail('An excluded action reached the account/network boundary')
        yield

    monkeypatch.setattr(engine, '_operation_guard', forbidden_guard)
    result = await engine.execute_action_task(task_id)
    assert result['ok'] is False
    assert result['reason'] == 'interaction_scope_only'
    with runtime.module.get_session() as session:
        assert session.get(runtime.module.AccountActionTask, task_id).status == 'pending'


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['follow', 'unfollow'])
async def test_locked_execution_cannot_run_a_legacy_social_action(runtime, action):
    engine = runtime.module.MonitorEngine(runtime.module.cfg, SimpleNamespace())
    task_id = add_action(runtime, action)
    result = await engine._execute_action_task_locked(task_id)
    assert result['ok'] is False
    assert result['reason'] == 'interaction_scope_only'
    with runtime.module.get_session() as session:
        assert session.get(runtime.module.AccountActionTask, task_id).status == 'pending'


@pytest.mark.anyio
async def test_periodic_action_selection_only_dispatches_direct_messages(runtime, monkeypatch):
    engine = runtime.module.MonitorEngine(runtime.module.cfg, SimpleNamespace())
    for action in ('follow', 'unfollow'):
        add_action(runtime, action)
    direct_message = add_action(runtime, 'send_dm')
    dispatched = []

    async def execute(task_id):
        dispatched.append(task_id)
        return {'ok': True}

    monkeypatch.setattr(engine, '_task_due', lambda *_args: True)
    monkeypatch.setattr(engine, '_action_gap_ok', lambda *_args: True)
    monkeypatch.setattr(engine, '_action_cap_ok', lambda *_args: True)
    monkeypatch.setattr(engine, 'execute_action_task', execute)
    await engine._process_action_tasks()
    assert dispatched == [direct_message]


@pytest.mark.anyio
@pytest.mark.parametrize('action', ['follow', 'unfollow'])
async def test_saved_social_draft_cannot_be_approved(runtime, action):
    task_id = add_action(runtime, action, status='draft')
    with pytest.raises(runtime.module.HTTPException) as error:
        await runtime.module.approve_account_action(task_id)
    assert error.value.status_code == 400
    with runtime.module.get_session() as session:
        assert session.get(runtime.module.AccountActionTask, task_id).status == 'draft'


@pytest.mark.anyio
async def test_direct_action_creation_also_rejects_social_operations(runtime):
    body = runtime.module.ActionIn(account_id=1, action='follow', target_uid='fixture-peer', run_now=False)
    with pytest.raises(runtime.module.HTTPException) as error:
        await runtime.module.create_account_action(body)
    assert error.value.status_code == 400


@pytest.mark.anyio
@pytest.mark.parametrize('interaction_only', [True, False])
async def test_webhook_delivery_obeys_runtime_scope_and_shuts_down(runtime, monkeypatch, interaction_only):
    import asyncio

    module = runtime.module
    module.cfg.engine.interaction_only = interaction_only
    dispatched = asyncio.Event()

    class Browser:
        def __init__(self, *_args, **_kwargs):
            self.started = False
            self.stopped = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.stopped = True

    class Engine:
        def __init__(self, *_args):
            self.stopped = False

        def _prune_risk_events_if_due(self, *_args):
            return 0

        def recover_interrupted_tasks(self):
            return 0

        def start(self):
            pass

        def set_dm_event_sink(self, _publisher):
            pass

        async def stop(self):
            self.stopped = True

    async def dispatch(_limit):
        dispatched.set()
        return []

    monkeypatch.setattr(module, 'BrowserManager', Browser)
    monkeypatch.setattr(module, 'MonitorEngine', Engine)
    monkeypatch.setattr(module, '_seed_browser_runtimes', lambda: [])
    monkeypatch.setattr(module, 'dispatch_pending', dispatch)
    async with module.lifespan(module.app):
        await asyncio.sleep(0)
        if interaction_only:
            assert module._webhook_delivery_task is None
            assert not dispatched.is_set()
        else:
            assert module._webhook_delivery_task is not None
            assert dispatched.is_set()
    assert module._webhook_delivery_task is None
    assert module.browser.stopped and module.engine.stopped
