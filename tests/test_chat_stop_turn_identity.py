"""An old Stop request may never stop a replacement turn."""
import asyncio
import json

import httpx
import pytest

from web import app as web


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name, path in {
        'PROJECT_ROOT': tmp_path,
        'OUTPUTS_DIR': tmp_path / 'outputs',
        'ENV_FILE': tmp_path / '.env',
        'SESSIONS_DIR': tmp_path / 'sessions',
        'DEBUG_DIR': tmp_path / 'debug',
        'OPENCLAW_SESSIONS_DIR': tmp_path / 'openclaw-sessions',
        'SHARED_RAW_STREAM': tmp_path / 'absent.jsonl',
    }.items():
        monkeypatch.setattr(web, name, path)
    for name in ('_RUNNING_CHAT', '_session_locks'):
        monkeypatch.setattr(web, name, {})
    for name in ('_STOPPED_CHAT', '_BG_TASKS', '_DIRECT_CHAT_TASKS', '_HARNESS_TASKS', '_ACTIVE_TURN_IDS'):
        monkeypatch.setattr(web, name, set())


def stop(turn_id):
    return web.api_chat_stop(web.StopRequest(sessionId='session', expectedTurnId=turn_id))


@pytest.mark.parametrize('runtime', ['codex', 'api', 'openclaw'])
def test_streamed_replacement_rejects_old_stop_and_confirms_its_own(isolated, monkeypatch, runtime):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        class Adapter:
            async def run(self, request, emit, ask, bind):
                bind('native')
                started.set()
                await release.wait()

        async def direct_stream(*args, **kwargs):
            started.set()
            await release.wait()
            yield 'finish', 'stop'

        async def gateway_response(request):
            started.set()
            await release.wait()
            return httpx.Response(200, text=(
                'data: {"choices":[{"delta":{"content":"finished"},"finish_reason":"stop"}]}\n\n'
                'data: [DONE]\n\n'))

        real_client = httpx.AsyncClient
        monkeypatch.setattr(web, '_chat_backend', lambda session: runtime)
        monkeypatch.setattr(web.harnesses, 'adapter', lambda runtime: Adapter())
        monkeypatch.setattr(web, 'stream_chat', direct_stream)
        monkeypatch.setattr(web, '_chat_message', lambda req: req.message)
        monkeypatch.setattr(web, '_direct_chat_content', lambda req: (req.message, ''))
        monkeypatch.setattr(web, '_resolve_transport', lambda session: 'http')
        monkeypatch.setattr(web, '_heal_openclaw_session', lambda session: None)
        monkeypatch.setattr(web, 'question_bridge_supported', lambda: False)
        monkeypatch.setattr(web, 'GatewayClient', None)
        monkeypatch.setattr(web, 'openclaw_base_cmd', lambda: ['unused-openclaw'])
        monkeypatch.setattr(web, '_agent_thinking_level', lambda: 'off')
        monkeypatch.setattr(web, '_proxy_env', dict)
        monkeypatch.setattr(web, 'chat_completions_url', lambda: 'http://gateway.test/chat')
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
            transport=httpx.MockTransport(gateway_response), **kwargs))

        await web.api_chat_stream(web.ChatRequest(
            message='new work', sessionId='session', turnId='turn-b'))
        try:
            await asyncio.wait_for(started.wait(), 3)
            proc = web._RUNNING_CHAT['session']
            assert await stop('turn-a') == {'stopped': False, 'status': 'turn_mismatch'}
            assert web._RUNNING_CHAT['session'] is proc
            assert proc.poll() is None
            assert 'session' not in web._STOPPED_CHAT
            if runtime == 'openclaw':
                assert await stop('turn-b') == {'stopped': False, 'status': 'unconfirmed'}
                assert proc.poll() is None
                assert web._RUNNING_CHAT['session'] is proc
                assert 'session' not in web._STOPPED_CHAT
                release.set()
                await asyncio.wait_for(asyncio.gather(*web._BG_TASKS), 5)
                assert await stop('turn-b') == {'stopped': False, 'status': 'already_finished'}
                return
            assert await stop('turn-b') == {'stopped': True, 'status': 'stopped'}
            assert 'session' not in web._RUNNING_CHAT
            assert not web._session_lock('session').locked()
            assert await stop('turn-b') == {'stopped': True, 'status': 'stopped'}
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*web._BG_TASKS, return_exceptions=True), 5)

    asyncio.run(exercise())


def test_nonstream_cannot_reuse_a_stream_stop_target(isolated, monkeypatch):
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def fake_stream(*args, **kwargs):
            started.set()
            await release.wait()
            yield 'finish', 'stop'

        monkeypatch.setattr(web, 'stream_chat', fake_stream)
        req = web.ChatRequest(message='nonstream work', sessionId='session', turnId='turn-a')
        task = asyncio.create_task(web._run_direct_chat(req, 'session', req.message, '', lambda *args: None))
        try:
            await asyncio.wait_for(started.wait(), 3)
            assert await stop('turn-a') == {'stopped': False, 'status': 'turn_mismatch'}
            assert not task.done()
            assert 'session' not in web._STOPPED_CHAT
        finally:
            release.set()
            await asyncio.wait_for(task, 5)
        # A nonstream snapshot's caller-supplied turn ID is not a confirmed stream receipt.
        assert await stop('turn-a') == {'stopped': False, 'status': 'unconfirmed'}

    asyncio.run(exercise())


def test_bound_stop_without_local_owner_requires_matching_terminal_receipt(isolated):
    assert asyncio.run(stop('turn-a')) == {'stopped': False, 'status': 'unconfirmed'}
    web._save_turn('web:session', 'running', '', {'turn_id': 'turn-a', 'stop_turn_id': 'turn-a'})
    assert asyncio.run(stop('turn-a')) == {'stopped': False, 'status': 'unconfirmed'}
    web._save_turn('web:session', 'done', 'finished', {
        'turn_id': 'turn-a', 'stop_turn_id': 'turn-a', 'outcome': 'completed',
    })
    assert asyncio.run(stop('turn-a')) == {'stopped': False, 'status': 'unconfirmed'}
    path = web._job_event_file('turn-a')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'id': 1, 'event': 'error', 'data': 'failed'}) + '\n')
    assert asyncio.run(stop('turn-a')) == {'stopped': False, 'status': 'unconfirmed'}
    with path.open('a') as journal:
        journal.write(json.dumps({'id': 2, 'event': 'done',
                                  'data': {'sessionKey': 'session', 'outcome': 'completed'}}) + '\n')
    assert asyncio.run(stop('turn-a')) == {'stopped': False, 'status': 'already_finished'}
    assert asyncio.run(stop('turn-b')) == {'stopped': False, 'status': 'unconfirmed'}
