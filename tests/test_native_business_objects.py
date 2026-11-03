"""D25: native business-object queries stay typed, read-only and host-scoped."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.codex import CodexAdapter


WIRE = '''import json,os,sys
def send(value): print(json.dumps(value),flush=True)
thread=os.environ.get("BUSINESS_NATIVE_ID") or "business-thread"
turn="business-turn"
calls=json.loads(os.environ["BUSINESS_CALLS"])
index=0
answered=set()
def completed():
 send({"method":"turn/completed","params":{"threadId":thread,"turnId":turn,"turn":{"id":turn,"status":"completed"}}})
def call(i):
 value=calls[i]
 params={"threadId":thread,"turnId":turn,"callId":"business-call-"+str(i),"tool":"easel_business_objects","arguments":value["arguments"]}
 params.update(value.get("overrides",{}))
 send({"id":"business-reverse-"+str(i),"method":"item/tool/call","params":params})
for line in sys.stdin:
 m=json.loads(line)
 with open(os.environ["BUSINESS_AUDIT"],"a",encoding="utf-8") as f:f.write(json.dumps({"fixturePid":os.getpid(),**m})+"\\n")
 method=m.get("method")
 if method=="initialize":send({"id":m["id"],"result":{}})
 elif method in ("thread/start","thread/resume"):
  if method=="thread/resume":thread=m["params"]["threadId"]
  else:
   with open(os.environ["BUSINESS_REGISTRATION"],"w",encoding="utf-8") as f:json.dump({"threadId":thread,"tools":[v["name"] for v in m["params"].get("dynamicTools",[])]},f)
  send({"id":m["id"],"result":{"thread":{"id":thread}}})
 elif method=="turn/start":
  send({"id":m["id"],"result":{"turn":{"id":turn}}})
  registered=True
  if os.environ.get("BUSINESS_RESTORE_ROUTING"):
   try:
    with open(os.environ["BUSINESS_REGISTRATION"],encoding="utf-8") as f:state=json.load(f)
    registered=state.get("threadId")==thread and "easel_business_objects" in state.get("tools",[])
   except FileNotFoundError:registered=False
  if os.environ.get("BUSINESS_NO_CALL") or not registered or not calls:completed()
  elif os.environ.get("BUSINESS_CONCURRENT"):
   for i in range(len(calls)):call(i)
  else:call(0)
 elif str(m.get("id","")).startswith("business-reverse-"):
  answered.add(m["id"])
  if len(answered)==len(calls):completed()
  elif not os.environ.get("BUSINESS_CONCURRENT"):call(len(answered))
 elif method=="turn/interrupt":send({"id":m["id"],"result":{}})
'''

PAYLOAD = {'action': 'list', 'kind': 'idea', 'query': '真实选题'}
HOST_DTO = {'schemaVersion': 1, 'actorId': 'host-actor', 'projectId': 'host-ip', 'kind': 'idea',
            'items': [{'id': 'a' * 12, 'title': 'Host-owned title'}], 'nextCursor': None}


def request_for(tmp_path, arguments, callback, *, no_call=False, native_id='', overrides=None,
                calls=None, concurrent=False, restore_routing=False):
    assert 'business_objects' in RunRequest.__dataclass_fields__, 'A read-only typed host callback is required'
    script = tmp_path / 'business_wire.py'
    script.write_text(WIRE, encoding='utf-8')
    async def forbidden(*args):
        pytest.fail('Business-object reads must not publish, present or ask for authority')
    return RunRequest('Read existing typed business objects; never infer or create business state', tmp_path,
        {**os.environ, 'BUSINESS_AUDIT': str(tmp_path / 'business-audit.jsonl'),
         'BUSINESS_REGISTRATION': str(tmp_path / 'business-native-state.json'),
         'BUSINESS_NATIVE_ID': native_id, 'BUSINESS_NO_CALL': '1' if no_call else '',
         'BUSINESS_CALLS': json.dumps(calls if calls is not None else [{'arguments': arguments, 'overrides': overrides or {}}]),
         'BUSINESS_CONCURRENT': '1' if concurrent else '',
         'BUSINESS_RESTORE_ROUTING': '1' if restore_routing else ''},
        native_id=native_id, command=(sys.executable, str(script)), timeout=5,
        business_objects=callback, publish=forbidden, present=forbidden)


def audit(tmp_path):
    return [json.loads(line) for line in (tmp_path / 'business-audit.jsonl').read_text().splitlines()]


def run_query(tmp_path, arguments, callback, **options):
    request = request_for(tmp_path, arguments, callback, **options)
    ids = []
    async def no_questions(*args):
        pytest.fail('Object reads cannot request or synthesize authority')
    error = None
    try:
        asyncio.run(CodexAdapter().run(request, lambda *args: None, no_questions, ids.append))
    except HarnessError as caught:
        error = caught
    return {'messages': audit(tmp_path), 'ids': ids, 'error': error}


def results(value):
    return [message['result'] for message in value['messages']
            if str(message.get('id', '')).startswith('business-reverse-') and 'result' in message]


@pytest.mark.parametrize('enabled', [True, False])
def test_only_an_enabled_callback_registers_the_eager_business_object_tool(tmp_path, enabled):
    async def read(payload, call_id): return HOST_DTO
    value = run_query(tmp_path, PAYLOAD, read if enabled else None, no_call=True)
    assert value['error'] is None
    start = next(message['params'] for message in value['messages'] if message.get('method') == 'thread/start')
    tools = {tool['name']: tool for tool in start['dynamicTools']}
    assert ('easel_business_objects' in tools) is enabled
    if enabled:
        tool = tools['easel_business_objects']
        assert tool['type'] == 'function' and tool['deferLoading'] is False
        schema = tool['inputSchema']
        assert schema['additionalProperties'] is False
        assert schema['required'] == ['action', 'kind']
        assert set(schema['properties']) == {'action', 'kind', 'id', 'cursor', 'query'}
        assert schema['properties']['action']['enum'] == ['list', 'inspect']
        assert set(schema['properties']['kind']['enum']) == {'idea', 'draft', 'workshop'}
        assert schema['properties']['cursor']['maxLength'] == 2048
        assert schema['properties']['query']['maxLength'] == 500
        assert 'easel_business_objects' in start['developerInstructions']


@pytest.mark.parametrize('payload', [
    {'action': 'list', 'kind': 'idea'},
    {'action': 'list', 'kind': 'draft', 'cursor': '', 'query': ''},
    {'action': 'list', 'kind': 'workshop', 'cursor': 'opaque-token_-', 'query': '来源 https://example.test/选题'},
    {'action': 'list', 'kind': 'idea', 'cursor': 'c' * 2048, 'query': '题' * 500},
    {'action': 'inspect', 'kind': 'idea', 'id': 'a' * 12},
    {'action': 'inspect', 'kind': 'idea', 'id': 'x'},
    {'action': 'inspect', 'kind': 'idea', 'id': 'A' * 160},
    {'action': 'inspect', 'kind': 'draft', 'id': 'b' * 32},
    {'action': 'inspect', 'kind': 'workshop', 'id': 'c' * 32},
])
def test_valid_queries_forward_exact_typed_input_and_return_the_host_dto(tmp_path, payload):
    seen = []
    async def read(value, call_id):
        seen.append(value)
        return HOST_DTO
    value = run_query(tmp_path, payload, read)
    assert value['error'] is None
    assert seen == [payload]
    response = results(value)[0]
    assert response['success'] is True
    assert json.loads(response['contentItems'][0]['text']) == HOST_DTO


def test_the_host_receives_the_real_native_call_id_without_argument_pollution(tmp_path):
    seen = []
    async def read(value, call_id):
        seen.append((value, call_id))
        return HOST_DTO
    result = run_query(tmp_path, PAYLOAD, read, overrides={'callId': 'native-business-identity'})
    assert result['error'] is None
    assert seen == [(PAYLOAD, 'native-business-identity')]
    assert 'callId' not in seen[0][0]


@pytest.mark.parametrize('arguments', [
    None, [], {}, 'list everything', {'action': 'list'}, {'kind': 'idea'},
    {'action': 'delete', 'kind': 'idea'}, {'action': True, 'kind': 'idea'},
    {'action': 'list', 'kind': 'project'}, {'action': 'list', 'kind': ['idea']},
    {'action': 'list', 'kind': 'idea', 'id': 'a' * 12},
    {'action': 'list', 'kind': 'idea', 'id': None},
    {'action': 'inspect', 'kind': 'idea'},
    {'action': 'inspect', 'kind': 'draft', 'id': 'a' * 12},
    {'action': 'inspect', 'kind': 'workshop', 'id': 'A' * 32},
    {'action': 'inspect', 'kind': 'workshop', 'id': True},
    {'action': 'inspect', 'kind': 'idea', 'id': 'a' * 12, 'query': ''},
    {'action': 'inspect', 'kind': 'draft', 'id': 'a' * 32, 'cursor': ''},
    *[{'action': 'inspect', 'kind': 'idea', 'id': value} for value in ('', 'a' * 161, '../private', 'https://private.test/id', '/tmp/object', 'a' * 11 + '\n', '选题一')],
    *[{'action': 'list', 'kind': 'idea', 'query': value} for value in (None, False, [], 'q' * 501, 'line\nnext', 'zero\x00byte')],
    *[{'action': 'list', 'kind': 'idea', 'cursor': value} for value in (None, False, [], 'c' * 2049, 'bad\ttoken')],
    *[{'action': 'list', 'kind': 'idea', key: value} for key, value in (
        ('actorId', 'other-user'), ('projectId', 'other-ip'), ('ip', 'other-ip'),
        ('path', '/private/data'), ('url', 'https://private.test/query'), ('state', 'published'),
        ('status', 'approved'), ('approved', True), ('callId', 'forged-call'), ('limit', 100), ('extra', {}))],
])
def test_invalid_or_authority_bearing_queries_never_reach_the_host(tmp_path, arguments):
    seen = []
    async def read(value, call_id):
        seen.append(value)
        return HOST_DTO
    value = run_query(tmp_path, arguments, read)
    assert value['error'] is None
    assert seen == []
    assert results(value)[0]['success'] is False


@pytest.mark.parametrize('overrides', [
    {'namespace': 'foreign'}, {'callId': None}, {'callId': ''}, {'callId': '  '},
    {'callId': False}, {'callId': 'c' * 257}, {'callId': 'unsafe\ncall'},
    {'callId': 'unsafe\x7fcall'}, {'callId': 'unsafe\x85call'},
])
def test_invalid_namespace_and_call_identity_cannot_execute_a_callback(tmp_path, overrides):
    seen = []
    async def read(value, call_id):
        seen.append(value)
        return HOST_DTO
    value = run_query(tmp_path, PAYLOAD, read, overrides=overrides)
    assert value['error'] is None and seen == []
    assert results(value)[0]['success'] is False


@pytest.mark.parametrize('overrides', [{'threadId': 'other-thread'}, {'turnId': 'other-turn'}, {'turnId': None}])
def test_queries_from_other_threads_or_turns_are_not_routed_to_the_host(tmp_path, overrides):
    seen = []
    async def read(value, call_id):
        seen.append(value)
        return HOST_DTO
    value = run_query(tmp_path, PAYLOAD, read, overrides=overrides)
    assert value['error'] is not None
    assert seen == [] and results(value) == []


def test_a_native_tool_definition_does_not_enable_a_disabled_host_callback(tmp_path):
    value = run_query(tmp_path, PAYLOAD, None)
    assert value['error'] is None
    assert results(value)[0]['success'] is False


@pytest.mark.parametrize('result', [None, [], {}, 'model says the object was found', {'bad': float('nan')}])
def test_only_a_json_host_dto_is_returned_as_success(tmp_path, result):
    async def read(value, call_id): return result
    value = run_query(tmp_path, PAYLOAD, read)
    assert value['error'] is None
    assert results(value)[0]['success'] is False


def test_host_exceptions_do_not_leak_private_diagnostics(tmp_path):
    async def read(value, call_id): raise RuntimeError('secret-token-value /private/data')
    value = run_query(tmp_path, PAYLOAD, read)
    response = results(value)[0]
    assert response['success'] is False
    assert 'secret-token-value' not in json.dumps(response)
    assert '/private/data' not in json.dumps(response)


@pytest.mark.parametrize('concurrent', [False, True])
def test_replaying_one_call_id_shares_one_host_read_even_when_requests_overlap(tmp_path, concurrent):
    seen = []
    async def read(value, call_id):
        seen.append(value)
        await asyncio.sleep(0.03)
        return HOST_DTO
    calls = [{'arguments': PAYLOAD, 'overrides': {'callId': 'same-call'}}] * 2
    value = run_query(tmp_path, None, read, calls=calls, concurrent=concurrent)
    assert value['error'] is None and seen == [PAYLOAD]
    assert len(results(value)) == 2
    assert all(item['success'] and json.loads(item['contentItems'][0]['text']) == HOST_DTO for item in results(value))


def test_a_call_id_cannot_be_rebound_to_different_query_arguments(tmp_path):
    seen = []
    async def read(value, call_id):
        seen.append(value)
        return HOST_DTO
    calls = [{'arguments': PAYLOAD, 'overrides': {'callId': 'same-call'}},
             {'arguments': {'action': 'inspect', 'kind': 'idea', 'id': 'a' * 12}, 'overrides': {'callId': 'same-call'}}]
    value = run_query(tmp_path, None, read, calls=calls)
    assert value['error'] is None and seen == [PAYLOAD]
    assert [item['success'] for item in results(value)] == [True, False]


def test_callback_failures_are_not_retried_by_replaying_the_same_call_id(tmp_path):
    seen = []
    async def read(value, call_id):
        seen.append(value)
        raise RuntimeError('private host error')
    value = run_query(tmp_path, None, read, calls=[{'arguments': PAYLOAD, 'overrides': {'callId': 'same-call'}}] * 2)
    assert value['error'] is None and seen == [PAYLOAD]
    assert [item['success'] for item in results(value)] == [False, False]


def test_an_invalid_query_seals_its_call_id_without_poisoning_a_new_call(tmp_path):
    seen = []
    async def read(value, call_id):
        seen.append((value, call_id))
        return HOST_DTO
    calls = [{'arguments': {**PAYLOAD, 'state': 'published'}, 'overrides': {'callId': 'invalid-call'}},
             {'arguments': PAYLOAD, 'overrides': {'callId': 'invalid-call'}},
             {'arguments': PAYLOAD, 'overrides': {'callId': 'new-valid-call'}}]
    value = run_query(tmp_path, None, read, calls=calls)
    assert value['error'] is None and seen == [(PAYLOAD, 'new-valid-call')]
    assert [item['success'] for item in results(value)] == [False, False, True]


def test_a_later_turn_with_the_same_call_id_queries_fresh_host_data(tmp_path):
    seen = []
    async def read(value, call_id):
        seen.append((value, call_id))
        return {**HOST_DTO, 'items': [{'id': 'a' * 12, 'title': value['query']}]}
    alpha = {**PAYLOAD, 'query': 'Alpha'}
    beta = {**PAYLOAD, 'query': 'Beta'}
    first = run_query(tmp_path, alpha, read, restore_routing=True)
    second = run_query(tmp_path, beta, read, native_id=first['ids'][0], restore_routing=True)
    assert first['error'] is second['error'] is None
    assert seen == [(alpha, 'business-call-0'), (beta, 'business-call-0')]
    assert json.loads(results(second)[-1]['contentItems'][0]['text'])['items'][0]['title'] == 'Beta'


def test_fresh_rpc_process_restores_the_registered_tool_without_appending_it(tmp_path):
    seen = []
    async def read(value, call_id):
        seen.append(value)
        return HOST_DTO
    first = run_query(tmp_path, PAYLOAD, read, no_call=True, restore_routing=True)
    second = run_query(tmp_path, PAYLOAD, read, native_id=first['ids'][0], restore_routing=True)
    assert first['error'] is second['error'] is None
    assert second['ids'] == first['ids'] and seen == [PAYLOAD]
    starts = [message for message in second['messages'] if message.get('method') == 'thread/start']
    resumes = [message for message in second['messages'] if message.get('method') == 'thread/resume']
    assert len(starts) == len(resumes) == 1
    assert starts[0]['fixturePid'] != resumes[0]['fixturePid']
    assert not {'dynamicTools', 'path', 'history'} & set(resumes[0]['params'])
    assert results(second)[0]['success'] is True


def test_an_opaque_legacy_idea_id_returned_by_list_can_be_inspected_after_resume(tmp_path):
    legacy = 'Legacy_idea-2026'
    item = {'id': legacy, 'title': 'Stored legacy idea'}
    dto = {**HOST_DTO, 'items': [item]}
    seen = []

    async def read(payload, call_id):
        seen.append((payload, call_id))
        return dto

    listing = {'action': 'list', 'kind': 'idea'}
    first = run_query(tmp_path, listing, read, restore_routing=True)
    listed = json.loads(results(first)[0]['contentItems'][0]['text'])
    inspect = {'action': 'inspect', 'kind': 'idea', 'id': listed['items'][0]['id']}
    second = run_query(tmp_path, inspect, read, native_id=first['ids'][0], restore_routing=True)

    assert first['error'] is second['error'] is None
    assert first['ids'] == second['ids']
    assert seen == [(listing, 'business-call-0'), (inspect, 'business-call-0')]
    response = results(second)[-1]
    assert response['success'] is True
    assert json.loads(response['contentItems'][0]['text'])['items'] == [item]


def test_an_old_thread_without_this_tool_keeps_its_identity_and_does_not_invent_objects(tmp_path):
    seen = []
    async def read(value, call_id):
        seen.append(value)
        return HOST_DTO
    value = run_query(tmp_path, PAYLOAD, read, native_id='old-thread', restore_routing=True)
    assert value['error'] is None and value['ids'] == ['old-thread'] and seen == []
    assert not any(message.get('method') == 'thread/start' for message in value['messages'])
    assert results(value) == []


def test_restored_tools_still_require_an_enabled_callback(tmp_path):
    async def read(value, call_id): return HOST_DTO
    first = run_query(tmp_path, PAYLOAD, read, no_call=True, restore_routing=True)
    second = run_query(tmp_path, PAYLOAD, None, native_id=first['ids'][0], restore_routing=True)
    assert second['error'] is None and results(second)[0]['success'] is False


def test_cancelling_a_pending_read_cancels_the_callback_and_does_not_return_success(tmp_path):
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def read(value, call_id):
            entered.set()
            try: await asyncio.Future()
            finally: cleaned.set()
        async def no_questions(*args): pytest.fail('No authority request is allowed')
        request = request_for(tmp_path, PAYLOAD, read)
        task = asyncio.create_task(CodexAdapter().run(request, lambda *args: None, no_questions, lambda value: None))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 3)
            assert cleaned.is_set()
            assert not any(result['success'] for result in results({'messages': audit(tmp_path)}))
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
