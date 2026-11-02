"""B30: a native tool delegates exact publication intent only to its typed host."""
import asyncio
from dataclasses import replace
import json
import os
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.codex import CodexAdapter
from easel.harnesses.rpc import close_sessions


WIRE = '''import json,os,sys
def send(value): print(json.dumps(value),flush=True)
thread=os.environ.get("PUBLICATION_NATIVE_ID") or "publication-thread"
turn="publication-turn"
for line in sys.stdin:
 m=json.loads(line)
 with open(os.environ["PUBLICATION_AUDIT"],"a",encoding="utf-8") as f: f.write(json.dumps(m)+"\\n")
 method=m.get("method")
 if method=="initialize": send({"id":m["id"],"result":{}})
 elif method in ("thread/start","thread/resume"):
  if method=="thread/resume": thread=m["params"]["threadId"]
  send({"id":m["id"],"result":{"thread":{"id":thread}}})
 elif method=="turn/start":
  send({"id":m["id"],"result":{"turn":{"id":turn}}})
  if os.environ.get("PUBLICATION_NO_CALL"):
   send({"method":"turn/completed","params":{"threadId":thread,"turnId":turn,"turn":{"id":turn,"status":"completed"}}})
  else:
   params={"threadId":thread,"turnId":turn,"callId":"publication-call-1","tool":"easel_publish",
    "arguments":json.loads(os.environ["PUBLICATION_ARGS"])}
   params.update(json.loads(os.environ.get("PUBLICATION_OVERRIDES","{}")))
   send({"id":"publication-reverse","method":"item/tool/call","params":params})
 elif m.get("id")=="publication-reverse":
  send({"method":"item/completed","params":{"threadId":thread,"turnId":turn,"item":{"id":"final","type":"agentMessage","text":"Workflow result received"}}})
  send({"method":"turn/completed","params":{"threadId":thread,"turnId":turn,"turn":{"id":turn,"status":"completed"}}})
 elif method=="turn/interrupt": send({"id":m["id"],"result":{}})
'''

PLATFORMS = {'xiaohongshu', 'douyin', 'kuaishou', 'weixin-channels', 'zhihu', 'bilibili', 'wechat-oa'}
PAYLOAD = {'platform': 'zhihu', 'title': '审核后的标题', 'body': '第一段\n\n第二段',
           'artifactIds': ['a' * 32], 'tags': '知识,分享', 'persona': '创作者'}
RECEIPT = {'requestId': 'publication-request-1', 'status': 'awaiting_approval',
           'message': '等待指定内容的真实人工批准'}


def request_for(tmp_path, payload, publish, *, no_call=False, native_id='', overrides=None, session_key=''):
    assert 'publish' in RunRequest.__dataclass_fields__, 'The host needs a typed publication callback'
    script = tmp_path / 'publication_wire.py'
    script.write_text(WIRE, encoding='utf-8')
    return RunRequest('Prepare public content through its host workflow', tmp_path, {
        **os.environ, 'PUBLICATION_AUDIT': str(tmp_path / 'publication_audit.jsonl'),
        'PUBLICATION_ARGS': json.dumps(payload), 'PUBLICATION_NATIVE_ID': native_id,
        'PUBLICATION_NO_CALL': '1' if no_call else '', 'PUBLICATION_OVERRIDES': json.dumps(overrides or {}),
    }, native_id=native_id, command=(sys.executable, str(script)), timeout=5, publish=publish,
        session_key=session_key)


def audit(tmp_path):
    return [json.loads(line) for line in (tmp_path / 'publication_audit.jsonl').read_text().splitlines()]


def run_tool(tmp_path, payload, publish, **options):
    request = request_for(tmp_path, payload, publish, **options)
    events, identifiers = [], []

    async def no_generic_approval(questions):
        pytest.fail('The native publication bridge must not manufacture approval through ask_user')

    error = None
    try:
        asyncio.run(CodexAdapter().run(request, lambda *event: events.append(event),
                                     no_generic_approval, identifiers.append))
    except HarnessError as caught:
        error = caught
    return {'messages': audit(tmp_path), 'events': events, 'identifiers': identifiers, 'error': error}


def tool_result(result):
    return next(message['result'] for message in result['messages'] if message.get('id') == 'publication-reverse')


@pytest.mark.parametrize('available', [True, False])
def test_only_a_host_callback_advertises_the_eager_typed_publication_tool(tmp_path, available):
    async def publish(payload, call_id):
        return RECEIPT
    result = run_tool(tmp_path, PAYLOAD, publish if available else None, no_call=True)
    assert result['error'] is None
    start = next(message['params'] for message in result['messages'] if message.get('method') == 'thread/start')
    tools = {tool['name']: tool for tool in start['dynamicTools']}
    assert ('easel_publish' in tools) is available
    if available:
        publication = tools['easel_publish']
        assert publication['type'] == 'function' and publication['deferLoading'] is False
        schema = publication['inputSchema']
        assert schema['additionalProperties'] is False
        assert set(schema['properties']) == {'platform', 'title', 'body', 'tags', 'persona', 'artifactIds'}
        assert set(schema['properties']['platform']['enum']) == PLATFORMS
        assert schema['properties']['artifactIds']['items']['pattern'] == '^[a-f0-9]{32}$'
        assert 'easel_publish' in start['developerInstructions']
        assert 'shell' in start['developerInstructions'].lower()


@pytest.mark.parametrize('platform', sorted(PLATFORMS))
def test_a_valid_publication_is_bound_to_call_id_and_returns_only_the_host_receipt(tmp_path, platform):
    seen = []
    payload = {**PAYLOAD, 'platform': platform}
    async def publish(value, call_id):
        seen.append((value, call_id))
        return RECEIPT
    result = run_tool(tmp_path, payload, publish)
    assert result['error'] is None
    assert seen == [(payload, 'publication-call-1')]
    response = tool_result(result)
    assert response['success'] is True
    assert json.loads(response['contentItems'][0]['text']) == RECEIPT
    assert not any(kind == 'agent_event' and value.get('status') == 'published' for kind, value in result['events'])


def test_missing_optional_fields_are_normalized_without_inventing_content_or_account_identity(tmp_path):
    seen = []
    async def publish(payload, call_id):
        seen.append(payload)
        return RECEIPT
    result = run_tool(tmp_path, {'platform':'zhihu', 'body':'A text-only draft'}, publish)
    assert result['error'] is None
    assert seen == [{'platform':'zhihu', 'title':'', 'body':'A text-only draft',
                     'tags':'', 'persona':'', 'artifactIds':[]}]


@pytest.mark.parametrize('invalid', [
    {}, None, [], 'publish now', {'platform':'unknown', 'title':'Title'},
    {'platform':'zhihu'}, {'platform':'zhihu', 'title':'   ', 'body':'\n\t'},
    {**PAYLOAD, 'platform':False}, {**PAYLOAD, 'title':1}, {**PAYLOAD, 'body':None},
    {**PAYLOAD, 'tags':[]}, {**PAYLOAD, 'persona':{}},
    {**PAYLOAD, 'artifactIds':'a' * 32}, {**PAYLOAD, 'artifactIds':[1]},
    {**PAYLOAD, 'artifactIds':['A' * 32]}, {**PAYLOAD, 'artifactIds':['a' * 31]},
    {**PAYLOAD, 'artifactIds':['outputs/private.png']}, {**PAYLOAD, 'artifactIds':['https://private.test/file']},
    {**PAYLOAD, 'artifactIds':['a' * 32, 'a' * 32]}, {**PAYLOAD, 'artifactIds':['a' * 32] * 33},
    {**PAYLOAD, 'command':'biliup upload anything'}, {**PAYLOAD, 'path':'/private/credential'},
    {**PAYLOAD, 'url':'https://private.test/endpoint'}, {**PAYLOAD, 'accountId':'another-account'},
    {**PAYLOAD, 'approved':True}, {**PAYLOAD, 'approvalId':'forged-grant'},
    {**PAYLOAD, 'title':'x' * 1001}, {**PAYLOAD, 'body':'x' * 100001},
    {**PAYLOAD, 'tags':'x' * 2001}, {**PAYLOAD, 'persona':'x' * 121},
])
def test_untrusted_tool_arguments_never_reach_the_publication_host(tmp_path, invalid):
    seen = []
    async def publish(payload, call_id):
        seen.append(payload)
        return RECEIPT
    result = run_tool(tmp_path, invalid, publish)
    assert result['error'] is None
    assert seen == []
    assert tool_result(result)['success'] is False
    assert 'private.test' not in json.dumps(tool_result(result))
    assert '/private/' not in json.dumps(tool_result(result))


@pytest.mark.parametrize('overrides', [
    {'callId':''}, {'callId':None}, {'callId':'bad\ncall'}, {'namespace':'foreign'},
])
def test_invalid_tool_authority_is_rejected_before_the_host(tmp_path, overrides):
    seen = []
    async def publish(payload, call_id):
        seen.append(payload)
        return RECEIPT
    result = run_tool(tmp_path, PAYLOAD, publish, overrides=overrides)
    assert seen == []
    assert result['error'] is None
    assert tool_result(result)['success'] is False


@pytest.mark.parametrize('overrides', [{'threadId':'foreign-thread'}, {'turnId':'foreign-turn'}, {'turnId':None}])
def test_publication_calls_cannot_cross_the_verified_native_thread_or_turn(tmp_path, overrides):
    seen = []
    async def publish(payload, call_id):
        seen.append(payload)
        return RECEIPT
    result = run_tool(tmp_path, PAYLOAD, publish, overrides=overrides)
    assert seen == []
    assert result['error'] is not None or tool_result(result)['success'] is False


@pytest.mark.parametrize('outcome', [None, [], {}, {'value':float('nan')}, {'bad':object()},
                                    ValueError('/private/secret-key'), OSError('/private/secret-key'),
                                    HarnessError('/private/secret-key'), RuntimeError('/private/secret-key')])
def test_invalid_host_results_and_errors_fail_closed_without_echoing_sensitive_details(tmp_path, outcome):
    async def publish(payload, call_id):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    result = run_tool(tmp_path, PAYLOAD, publish)
    assert result['error'] is None
    assert tool_result(result)['success'] is False
    assert 'secret-key' not in json.dumps(tool_result(result))


def test_no_host_callback_is_an_explicit_failed_tool_result(tmp_path):
    result = run_tool(tmp_path, PAYLOAD, None)
    assert result['error'] is None
    assert tool_result(result)['success'] is False


def test_existing_threads_keep_history_and_do_not_claim_a_new_dynamic_tool(tmp_path):
    async def publish(payload, call_id):
        pytest.fail('An unregistered retained thread must not execute a publication callback')
    result = run_tool(tmp_path, PAYLOAD, publish, native_id='original-thread')
    assert result['error'] is None
    assert result['identifiers'] == ['original-thread']
    assert not any(message.get('method') == 'thread/start' for message in result['messages'])
    resume = next(message['params'] for message in result['messages'] if message.get('method') == 'thread/resume')
    assert 'dynamicTools' not in resume
    answer = tool_result(result)
    assert answer['success'] is False
    assert 'new conversation' in answer['contentItems'][0]['text'].lower()


def test_a_tool_registered_on_a_pooled_thread_remains_usable_on_its_next_turn(tmp_path):
    async def scenario():
        seen, identifiers = [], []
        async def publish(payload, call_id):
            seen.append((payload,call_id))
            return RECEIPT
        async def no_questions(questions):
            pytest.fail('Typed publication decisions belong to the host')
        request = request_for(tmp_path, PAYLOAD, publish, session_key='pooled-publication')
        try:
            await CodexAdapter().run(request, lambda *event:None, no_questions, identifiers.append)
            await CodexAdapter().run(replace(request,native_id=identifiers[-1]), lambda *event:None,
                                     no_questions, identifiers.append)
            assert len(seen) == 2
            assert identifiers == ['publication-thread','publication-thread']
            assert sum(message.get('method') == 'thread/start' for message in audit(tmp_path)) == 1
        finally:
            await close_sessions(request.session_key)
    asyncio.run(scenario())


def test_cancelling_the_turn_cancels_pending_publication_without_reporting_success(tmp_path):
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def publish(payload, call_id):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()
        async def no_questions(questions):
            pytest.fail('No generic question expected')
        request = request_for(tmp_path, PAYLOAD, publish)
        task = asyncio.create_task(CodexAdapter().run(request, lambda *args:None, no_questions, lambda value:None))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert cleaned.is_set()
            assert not any(message.get('id') == 'publication-reverse' and message.get('result',{}).get('success')
                           for message in audit(tmp_path))
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
    asyncio.run(scenario())
