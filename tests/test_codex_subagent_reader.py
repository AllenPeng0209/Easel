"""B27 reads a bounded, authenticated native child transcript without running it."""
import asyncio
import json
import os
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest


WIRE = """
import json, os, sys
for line in sys.stdin:
    message = json.loads(line)
    with open(os.environ['CHILD_AUDIT'], 'a') as audit:
        audit.write(json.dumps(message) + '\\n')
    method = message.get('method')
    if method == 'initialize':
        result = {}
    elif method == 'thread/read':
        result = {'thread': json.loads(os.environ['CHILD_THREAD'])}
    elif method == 'thread/items/list':
        result = json.loads(os.environ['CHILD_PAGE'])
        if result is None:
            print(json.dumps({'id': message['id'], 'error': {'code': -32603, 'message': 'fixture-secret-error'}}), flush=True)
            continue
    else:
        continue
    print(json.dumps({'id': message['id'], 'result': result}), flush=True)
"""


def fixture_request(tmp_path, *, thread=None, page=None):
    script = tmp_path / 'child_wire.py'
    script.write_text(WIRE)
    return RunRequest('', tmp_path, {
        **os.environ, 'CHILD_AUDIT': str(tmp_path / 'wire.jsonl'),
        'CHILD_THREAD': json.dumps(thread if thread is not None else {'id': 'child', 'parentThreadId': 'parent'}),
        'CHILD_PAGE': json.dumps(page),
        'EXAMPLE_SERVICE_TOKEN': 'native-fixture-credential',
    }, command=(sys.executable, str(script)), sandbox='read-only')


def calls(tmp_path):
    return [json.loads(line) for line in (tmp_path / 'wire.jsonl').read_text().splitlines()]


def entry(item):
    return {'turnId': 'turn', 'item': item}


def test_reads_recent_page_in_order_and_filters_and_scrubs_native_items(tmp_path):
    from easel.harnesses.codex_subagents import read_subagent

    page = {'nextCursor': 'native-opaque-cursor', 'data': [
        entry({'id': 'a', 'type': 'agentMessage', 'text': 'answer native-fixture-credential'}),
        entry({'id': 'tool', 'type': 'commandExecution', 'aggregatedOutput': 'private tool output'}),
        entry({'id': 'user', 'type': 'userMessage', 'content': [
            {'type': 'text', 'text': 'first'}, {'type': 'image', 'url': 'https://private.invalid'},
            {'type': 'text', 'text': 'Bearer abc.def.ghi'},
        ]}),
        entry({'id': 'thought', 'type': 'reasoning', 'text': 'private reasoning'}),
    ]}
    result = asyncio.run(read_subagent(fixture_request(tmp_path, page=page), parent_id='parent', child_id='child'))
    assert result == {'messages': [
        {'role': 'user', 'content': 'first\n\nBearer [REDACTED]'},
        {'role': 'assistant', 'content': 'answer [REDACTED]'},
    ], 'hasMore': True, 'observedChildren': []}
    requests = calls(tmp_path)
    assert [call['method'] for call in requests] == ['initialize', 'initialized', 'thread/read', 'thread/items/list']
    assert requests[2]['params'] == {'threadId': 'child', 'includeTurns': False}
    assert requests[3]['params'] == {'threadId': 'child', 'limit': 50, 'sortDirection': 'desc'}


def test_returns_nested_spawn_observations_separately_from_visible_messages(tmp_path):
    from easel.harnesses.codex_subagents import read_subagent

    spawn = {'id': 'spawn', 'type': 'collabAgentToolCall', 'tool': 'spawnAgent', 'status': 'completed',
             'senderThreadId': 'child', 'receiverThreadIds': ['grandchild'],
             'agentsStates': {'grandchild': {'status': 'running', 'message': 'private native payload'}}}
    page = {'data': [entry(spawn), entry({**spawn, 'senderThreadId': 'foreign'}),
                     entry({**spawn, 'tool': 'wait', 'receiverThreadIds': ['unrelated']}),
                     entry({**spawn, 'status': 'inProgress'})]}
    result = asyncio.run(read_subagent(fixture_request(tmp_path, page=page), parent_id='parent', child_id='child'))
    assert result == {'messages': [], 'hasMore': False, 'observedChildren': [
        {'parentThreadId': 'child', 'childThreadId': 'grandchild', 'action': 'spawn', 'status': 'running'},
    ]}


def test_nested_history_recognizes_the_observed_native_subagent_activity_shape(tmp_path):
    from easel.harnesses.codex_subagents import read_subagent

    activity = {'type': 'subAgentActivity', 'id': 'activity', 'agentThreadId': 'grandchild',
                'agentPath': '/private/native/path', 'kind': 'started'}
    page = {'data': [entry({**activity, 'kind': 'completed'}), entry(activity)]}
    result = asyncio.run(read_subagent(fixture_request(tmp_path, page=page), parent_id='parent', child_id='child'))
    assert result['observedChildren'] == [
        {'parentThreadId': 'child', 'childThreadId': 'grandchild', 'action': 'spawn', 'status': 'running'},
    ]
    assert result['messages'] == []


@pytest.mark.parametrize('thread', [
    {'id': 'unrelated', 'parentThreadId': 'parent'},
    {'id': 'child', 'parentThreadId': 'other'},
    {'id': 'child'},
    {'id': 'child', 'parentThreadId': None},
    ['bad'],
])
def test_rejects_mismatched_or_missing_native_identity_before_reading_items(tmp_path, thread):
    from easel.harnesses.codex_subagents import read_subagent

    with pytest.raises(HarnessError):
        asyncio.run(read_subagent(fixture_request(tmp_path, thread=thread), parent_id='parent', child_id='child'))
    assert [call['method'] for call in calls(tmp_path)] == ['initialize', 'initialized', 'thread/read']


@pytest.mark.parametrize('parent,child', [('same', 'same'), ('parent', ''), ('parent', '../history'),
    ('parent\n', 'child'), ('parent', 'c' * 257)])
def test_invalid_thread_ids_fail_before_a_process_starts(tmp_path, parent, child):
    from easel.harnesses.codex_subagents import read_subagent

    with pytest.raises(HarnessError):
        asyncio.run(read_subagent(fixture_request(tmp_path), parent_id=parent, child_id=child))
    assert not (tmp_path / 'wire.jsonl').exists()


@pytest.mark.parametrize('page', [None, {'data': 'invalid'}, {'data': [], 'nextCursor': 1}])
def test_native_read_errors_are_generic_and_return_no_transcript(tmp_path, page):
    from easel.harnesses.codex_subagents import read_subagent

    with pytest.raises(HarnessError) as caught:
        asyncio.run(read_subagent(fixture_request(tmp_path, page=page), parent_id='parent', child_id='child'))
    assert 'fixture-secret-error' not in str(caught.value)


def test_bounds_large_pages_and_message_text(tmp_path):
    from easel.harnesses.codex_subagents import read_subagent

    page = {'data': [entry({'id': str(i), 'type': 'agentMessage', 'text': 'x' * 5000}) for i in range(51)]}
    result = asyncio.run(read_subagent(fixture_request(tmp_path, page=page), parent_id='parent', child_id='child'))
    assert result['hasMore'] is True
    assert len(result['messages']) == 50
    assert all(len(message['content']) <= 4000 for message in result['messages'])
