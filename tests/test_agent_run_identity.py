"""B01 actual native protocol to Host SSE, journal replay and saved events."""
import asyncio
import json
import re
import sys

import pytest

from easel.harnesses import acp, codex, rpc
from web import app as web


WIRE = """
import json, sys
runtime = sys.argv[1]

def send(value):
    if runtime == 'acp':
        value = {'jsonrpc': '2.0', **value}
    print(json.dumps(value), flush=True)

def notify(method, params):
    send({'method': method, 'params': params})

def tool_update(update, **fields):
    notify('session/update', {'sessionId': 'native-session',
        'update': {'sessionUpdate': update, 'toolCallId': 'native-call', **fields}})

for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if 'id' not in message:
        continue
    if method == 'initialize':
        result = {'protocolVersion': 1, 'agentCapabilities': {'loadSession': True}}
    elif method in ('thread/start', 'thread/resume'):
        result = {'thread': {'id': 'native-thread'}}
    elif method in ('session/new', 'session/load'):
        result = {'sessionId': 'native-session'}
    elif method == 'turn/start':
        common = {'threadId': 'native-thread', 'turnId': 'native-turn'}
        item = {'id': 'native-call', 'parentCallId': 'native-parent', 'type': 'commandExecution',
                'command': 'read fixture', 'cwd': '/fixture', 'status': 'inProgress'}
        notify('item/started', {**common, 'item': item})
        notify('item/commandExecution/outputDelta', {**common, 'itemId': item['id'], 'delta': 'partial'})
        notify('item/completed', {**common, 'item': {**item, 'status': 'completed',
               'exitCode': 0, 'aggregatedOutput': 'partial result'}})
        notify('turn/completed', {**common, 'turn': {'id': 'native-turn', 'status': 'completed'}})
        result = {'turn': {'id': 'native-turn'}}
    elif method == 'session/prompt':
        tool_update('tool_call', parentCallId='native-parent', kind='read', status='in_progress',
                    rawInput={'path': 'fixture.txt'})
        tool_update('tool_call_update', status='in_progress', rawOutput={'text': 'partial'})
        tool_update('tool_call_update', status='completed', rawOutput={'text': 'partial result'})
        result = {'stopReason': 'end_turn'}
    else:
        result = {}
    send({'id': message['id'], 'result': result})
"""


@pytest.fixture(params=('codex', 'acp'))
def native(request, tmp_path, monkeypatch):
    runtime = request.param
    script = tmp_path / 'readonly_wire.py'
    script.write_text(WIRE, encoding='utf-8')
    command = [sys.executable, str(script), runtime]
    monkeypatch.setattr(codex, 'codex_override', lambda _env: command)
    monkeypatch.setattr(acp, 'acp_command', lambda _cwd: command)
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': runtime, 'models': {}, 'sandbox': 'read-only'})
    return runtime


async def collect(response):
    return [(event['id'], event['event'], json.loads(event['data'])) async for event in response.body_iterator]


def check_native_events(events, runtime):
    assert len(events) == 3
    run_ids = {event['runId'] for event in events}
    assert len(run_ids) == 1
    assert re.fullmatch(r'[a-f0-9]{32}', next(iter(run_ids)))
    assert len({event['id'] for event in events}) == 3
    assert all(event['callId'] == 'native-call' and event['parentCallId'] == 'native-parent' for event in events)
    assert [event['status'] for event in events] == ['running', 'running', 'completed']
    if runtime == 'codex':
        assert all(event['name'] == 'bash' and event['input']['command'] == 'read fixture' for event in events)
        assert events[1]['output'] == 'partial'
        assert events[2]['output'] == 'partial result' and events[2]['exitCode'] == 0
    else:
        assert all(event['name'] == 'read' and event['input'] == {'path': 'fixture.txt'} for event in events)
        assert events[1]['output'] == {'text': 'partial'}
        assert events[2]['output'] == {'text': 'partial result'}
    return next(iter(run_ids))


def test_host_identity_survives_live_sse_replay_and_final_snapshot(native):
    async def scenario():
        run_ids = []
        try:
            for turn in ('first-turn', 'second-turn'):
                request = web.ChatRequest(message='Read-only fixture', sessionId='same-session', turnId=turn)
                live = await collect(await web.api_chat_stream(request))
                assert live[0] == ('1', 'admitted', {'turnId': turn, 'sessionId': 'same-session'})
                events = [event for _, kind, event in live if kind == 'agent_event' and event['kind'] == 'tool']
                run_ids.append(check_native_events(events, native))
                last = await web.api_chat_last('same-session', turn)
                assert last['events'] == events
                assert await collect(await web.api_chat_stream(request)) == live
                assert await collect(await web.api_chat_job_stream(turn, after=1)) == live[1:]
            assert run_ids[0] != run_ids[1]
        finally:
            await rpc.close_sessions()
    asyncio.run(scenario())


def test_nonstream_runs_get_distinct_host_ids_even_when_client_turn_id_is_reused(native):
    async def scenario():
        runs = []
        try:
            for message in ('First read', 'Second read'):
                request = web.ChatRequest(message=message, sessionId='same-session', turnId='reused-turn')
                await web.api_chat(request)
                last = await web.api_chat_last('same-session', 'reused-turn')
                runs.append(check_native_events(last['events'], native))
            assert runs[0] != runs[1]
        finally:
            await rpc.close_sessions()
    asyncio.run(scenario())
