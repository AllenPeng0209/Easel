"""B27 trusted native lineage and immutable actor/IP-scoped child handles."""
import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from easel.harnesses.base import RunRequest
from easel.harnesses.codex import CodexAdapter
from easel.harnesses.events import EventProjector


SCOPE = {'actor_id': 'actor-one', 'project_id': 'ip-one', 'session_id': 'browser-root'}
NATIVE_ROOT = 'private-native-root'
NATIVE_CHILD = 'private-native-child'
NATIVE_GRANDCHILD = 'private-native-grandchild'


def registry(tmp_path):
    from web.chat_subagents import ChatSubagentStore
    return ChatSubagentStore(tmp_path)


def update(child=NATIVE_CHILD, *, parent=NATIVE_ROOT, root=NATIVE_ROOT, action='spawn', status='running'):
    return {'rootThreadId': root, 'parentThreadId': parent, 'childThreadId': child,
            'action': action, 'status': status}


def record(store, value, turn='browser-turn', **scope):
    return store.record(**{**SCOPE, **scope}, turn_id=turn, update=value)


def list_root(store, **scope):
    return store.list(**{**SCOPE, 'root_thread_id': NATIVE_ROOT, **scope})


def item(child=NATIVE_CHILD, *, sender=NATIVE_ROOT, tool='spawnAgent', status='completed', child_status='running'):
    return {'id': 'collab-call-' + child, 'type': 'collabAgentToolCall', 'tool': tool, 'status': status,
            'senderThreadId': sender, 'receiverThreadIds': [child],
            'agentsStates': {child: {'status': child_status, 'message': 'private agent message ' + child}},
            'prompt': 'private prompt ' + sender}


def test_spawn_and_nested_edges_have_opaque_public_handles_and_scoped_resolution(tmp_path):
    store = registry(tmp_path)
    first = record(store, update())
    nested = record(store, update(NATIVE_GRANDCHILD, parent=NATIVE_CHILD))
    assert first['parentId'] is None and nested['parentId'] == first['id']
    assert first['id'] != nested['id'] and len(first['id']) == len(nested['id']) == 32
    assert first['rootSessionId'] == SCOPE['session_id'] and first['originTurnId'] == 'browser-turn'
    assert first['title'] == '子 Agent'
    listing = list_root(registry(tmp_path))
    assert {child['id'] for child in listing} == {first['id'], nested['id']}
    assert all(value not in json.dumps(listing) for value in (NATIVE_ROOT, NATIVE_CHILD, NATIVE_GRANDCHILD))
    private = store.resolve(**SCOPE, root_thread_id=NATIVE_ROOT, handle=nested['id'])
    assert private['childThreadId'] == NATIVE_GRANDCHILD and private['parentThreadId'] == NATIVE_CHILD
    for different_scope in ({'actor_id': 'other'}, {'project_id': 'other'}, {'session_id': 'other'},
                            {'root_thread_id': 'other'}):
        assert store.resolve(**{**SCOPE, 'root_thread_id': NATIVE_ROOT, **different_scope},
                             handle=nested['id']) is None
        assert list_root(store, **different_scope) == []


def test_edges_cannot_be_reparented_reused_in_another_root_or_invented_by_updates(tmp_path):
    store = registry(tmp_path)
    assert record(store, update(action='update')) is None
    assert record(store, update(parent='unseen-parent')) is None
    first = record(store, update())
    nested = record(store, update(NATIVE_GRANDCHILD, parent=NATIVE_CHILD))
    assert record(store, update(NATIVE_GRANDCHILD)) is None
    assert record(store, update(NATIVE_ROOT, parent=NATIVE_CHILD)) is None
    assert record(store, update(root='other-root', parent='other-root')) is None
    assert record(store, update('new-child'), session_id='other-browser-session') is None
    assert record(store, update('new-child', root=NATIVE_CHILD, parent=NATIVE_CHILD)) is None
    assert record(store, update(NATIVE_GRANDCHILD, parent='unknown', action='update', status='completed')) is None
    assert {child['id'] for child in list_root(store)} == {first['id'], nested['id']}


def test_duplicate_spawn_cannot_revert_current_status_or_change_origin_turn(tmp_path):
    store = registry(tmp_path)
    first = record(store, update())
    completed = record(store, update(action='update', status='completed'), turn='another-turn')
    assert completed['id'] == first['id'] and completed['originTurnId'] == first['originTurnId']
    assert completed['status'] == 'completed'
    assert record(store, update()) is None
    assert record(store, update(action='update', status='completed')) is None
    assert list_root(store) == [completed]


def test_concurrent_duplicate_spawn_keeps_one_opaque_edge(tmp_path):
    store = registry(tmp_path)

    def spawn(_):
        return record(registry(tmp_path), update())

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(spawn, range(8)))
    assert sum(value is not None for value in results) == 1
    assert len(list_root(store)) == 1


@pytest.mark.parametrize('change', [{'childThreadId': '../private'}, {'status': 'not-a-native-state'},
                                    {'action': 'invent'}, {'childThreadId': True}, {'title': 'untrusted'}])
def test_private_registry_descriptors_fail_closed_on_malformed_values(tmp_path, change):
    store = registry(tmp_path)
    assert record(store, {**update(), **change}) is None
    assert list_root(store) == []


def test_native_projection_only_uses_confirmed_edges_and_no_private_tool_output():
    from easel.harnesses.events import native_subagent_descriptors

    assert native_subagent_descriptors(item(status='inProgress')) == []
    assert native_subagent_descriptors(item(status='failed')) == []
    assert native_subagent_descriptors(item(), parent_id='unrelated') == []
    assert native_subagent_descriptors(item(tool='wait'), parent_id=NATIVE_ROOT) == []
    assert native_subagent_descriptors(item()) == [
        {'parentThreadId': NATIVE_ROOT, 'childThreadId': NATIVE_CHILD, 'action': 'spawn', 'status': 'running'},
    ]
    assert native_subagent_descriptors(item(tool='wait', child_status='errored'))[0]['status'] == 'failed'
    assert native_subagent_descriptors(item(tool='wait', child_status='unknown-provider-state')) == []
    assert native_subagent_descriptors(item(child_status='unknown-provider-state'))[0]['status'] == 'unknown'
    output = []
    projector = EventProjector(lambda *event: output.append(event), {})
    projector.codex('item/started', {'item': item(status='inProgress')})
    projector.codex('item/completed', {'item': item()})
    assert len(output) == 2 and all(value[1]['name'] == 'spawnAgent' for value in output)
    assert output[0][1]['callId'] == output[1][1]['callId']
    assert all(secret not in json.dumps(output) for secret in (NATIVE_CHILD, NATIVE_ROOT, 'private prompt', 'private agent message'))


WIRE = r'''
import json, os, sys
def send(value): print(json.dumps(value), flush=True)
root = os.environ['ROOT_THREAD']
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if method == 'initialize':
        send({'id': request['id'], 'result': {}})
    elif method == 'thread/start':
        send({'id': request['id'], 'result': {'thread': {'id': root}}})
    elif method == 'turn/start':
        for notification in json.loads(os.environ.get('NATIVE_BEFORE_START', '[]')):
            send(notification)
        send({'id': request['id'], 'result': {'turn': {'id': 'native-turn'}}})
        for notification in json.loads(os.environ['NATIVE_NOTIFICATIONS']):
            send(notification)
        send({'method': 'turn/completed', 'params': {'threadId': root, 'turnId': 'native-turn',
            'turn': {'id': 'native-turn', 'status': 'completed'}}})
'''


def test_native_run_records_only_bound_root_lineage_and_keeps_child_output_off_parent(tmp_path):
    script = tmp_path / 'subagent_wire.py'
    script.write_text(WIRE)
    store = registry(tmp_path / 'scoped-store')
    emitted, projected = [], []

    def notify_item(value, thread=NATIVE_ROOT, turn='native-turn'):
        return {'method': 'item/completed', 'params': {'threadId': thread, 'turnId': turn, 'item': value}}

    notifications = [
        notify_item(item('stale-child'), turn='old-turn'),
        notify_item(item()),
        notify_item(item(NATIVE_GRANDCHILD, sender=NATIVE_CHILD), thread=NATIVE_CHILD, turn='child-turn'),
        notify_item(item('foreign-child', sender='foreign-root'), thread='foreign-root', turn='other-turn'),
        notify_item(item(tool='wait', child_status='completed')),
        {'method': 'item/agentMessage/delta', 'params': {'threadId': NATIVE_CHILD,
            'turnId': 'child-turn', 'itemId': 'child-message', 'delta': 'PRIVATE CHILD TEXT'}},
    ]

    def observed(value):
        result = record(store, value)
        if result is not None:
            projected.append(result)

    request = RunRequest('delegate', tmp_path, {**os.environ, 'ROOT_THREAD': NATIVE_ROOT,
        'NATIVE_NOTIFICATIONS': json.dumps(notifications)}, command=(sys.executable, str(script)),
        timeout=5, record_subagent=observed)

    async def unexpected_question(_):
        pytest.fail('A lineage notification cannot ask for approval')

    asyncio.run(CodexAdapter().run(request, lambda *event: emitted.append(event), unexpected_question, lambda _: None))
    assert len(projected) == 3
    assert [value['status'] for value in projected] == ['running', 'running', 'completed']
    assert projected[1]['parentId'] == projected[0]['id']
    assert projected[-1]['id'] == projected[0]['id']
    assert all(value not in json.dumps(emitted) for value in (
        NATIVE_ROOT, NATIVE_CHILD, NATIVE_GRANDCHILD, 'PRIVATE CHILD TEXT', 'foreign-child', 'stale-child'))


def test_real_subagent_activity_shape_buffered_before_turn_start_result(tmp_path):
    script = tmp_path / 'subagent_wire.py'
    script.write_text(WIRE)
    store = registry(tmp_path / 'scoped-store')
    observed, emitted = [], []

    def activity(child, kind, thread=NATIVE_ROOT, turn='native-turn'):
        return {'method': 'item/completed', 'params': {'threadId': thread, 'turnId': turn, 'item': {
            'type': 'subAgentActivity', 'id': 'activity-' + child + kind, 'agentThreadId': child,
            'agentPath': '/root/private-agent-path', 'kind': kind}}}

    early = [activity('stale-native-child', 'started', turn='wrong-turn'),
             activity('missing-turn-child', 'started', turn=None),
             activity('foreign-native-child', 'started', thread='foreign-root'),
             activity(NATIVE_CHILD, 'started')]
    live = [activity(NATIVE_GRANDCHILD, 'started', thread=NATIVE_CHILD, turn='child-turn'),
            activity(NATIVE_CHILD, 'interacted'), activity(NATIVE_CHILD, 'completed')]

    def on_edge(value):
        if result := record(store, value):
            observed.append(result)

    request = RunRequest('delegate', tmp_path, {**os.environ, 'ROOT_THREAD': NATIVE_ROOT,
        'NATIVE_BEFORE_START': json.dumps(early), 'NATIVE_NOTIFICATIONS': json.dumps(live)},
        command=(sys.executable, str(script)), timeout=5, record_subagent=on_edge)
    asyncio.run(CodexAdapter().run(request, lambda *event: emitted.append(event), None, lambda _: None))
    assert [node['status'] for node in observed] == ['running', 'running', 'completed']
    assert observed[1]['parentId'] == observed[0]['id']
    assert observed[2]['id'] == observed[0]['id']
    assert all(value not in json.dumps(emitted) for value in (
        'private-agent-path', NATIVE_CHILD, NATIVE_GRANDCHILD, 'missing-turn-child', 'stale-native-child'))
