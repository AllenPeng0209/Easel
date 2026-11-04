"""D30: native status reads existing requests without creating or executing one."""
import asyncio
import json
import os
import sys
from dataclasses import replace

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.codex import CodexAdapter


WIRE = '''import json,os,sys
def send(value): print(json.dumps(value),flush=True)
thread=os.environ.get("STATUS_NATIVE_ID") or "status-thread"
turn="status-turn"
remaining=int(os.environ.get("STATUS_REPEAT","1"))
index=0
def completed():
 send({"method":"turn/completed","params":{"threadId":thread,"turnId":turn,"turn":{"id":turn,"status":"completed"}}})
def call():
 global index
 index+=1
 params={"threadId":thread,"turnId":turn,"callId":"status-call-"+str(index),"tool":"easel_publication_status","arguments":json.loads(os.environ["STATUS_ARGS"])}
 params.update(json.loads(os.environ.get("STATUS_OVERRIDES","{}")))
 send({"id":"status-reverse-"+str(index),"method":"item/tool/call","params":params})
for line in sys.stdin:
 m=json.loads(line)
 with open(os.environ["STATUS_AUDIT"],"a",encoding="utf-8") as f: f.write(json.dumps({"fixturePid":os.getpid(),**m})+"\\n")
 method=m.get("method")
 if method=="initialize": send({"id":m["id"],"result":{}})
 elif method in ("thread/start","thread/resume"):
  if method=="thread/resume":thread=m["params"]["threadId"]
  else:
   with open(os.environ["STATUS_REGISTRATION"],"w",encoding="utf-8") as f:json.dump({"threadId":thread,"tools":[v["name"] for v in m["params"].get("dynamicTools",[])]},f)
  send({"id":m["id"],"result":{"thread":{"id":thread}}})
 elif method=="turn/start":
  send({"id":m["id"],"result":{"turn":{"id":turn}}})
  registered=True
  if os.environ.get("STATUS_RESTORE_ROUTING"):
   try:
    with open(os.environ["STATUS_REGISTRATION"],encoding="utf-8") as f:state=json.load(f)
    registered=state.get("threadId")==thread and "easel_publication_status" in state.get("tools",[])
   except FileNotFoundError:registered=False
  if os.environ.get("STATUS_NO_CALL") or not registered:completed()
  else:call()
 elif str(m.get("id","")).startswith("status-reverse-"):
  send({"method":"item/completed","params":{"threadId":thread,"turnId":turn,"item":{
   "id":"status-call-"+str(index),"type":"dynamicToolCall","tool":"easel_publication_status","status":"completed"}}})
  if index<remaining:call()
  else:completed()
 elif method=="turn/interrupt":send({"id":m["id"],"result":{}})
'''

APPROVAL_ID = 'a' * 32
STATUS = {'approvalId': APPROVAL_ID, 'state': 'submitted', 'confirmed': False,
          'simulation': True, 'message': 'Existing mocked request; no new publication'}


def request_for(tmp_path, arguments, callback, *, native_id='', no_call=False,
                overrides=None, repeat=1, restore_routing=False):
    assert 'publication_status' in RunRequest.__dataclass_fields__, 'A read-only host callback is required'
    script = tmp_path/'status_wire.py'
    script.write_text(WIRE, encoding='utf-8')
    async def forbidden(*args):
        pytest.fail('A status lookup must never publish, present or ask for approval')
    return RunRequest('Read the existing publication status without submitting anything', tmp_path,
        {**os.environ, 'STATUS_AUDIT':str(tmp_path/'status-audit.jsonl'),
         'STATUS_REGISTRATION':str(tmp_path/'status-native-state.json'), 'STATUS_ARGS':json.dumps(arguments),
         'STATUS_NATIVE_ID':native_id, 'STATUS_NO_CALL':'1' if no_call else '',
         'STATUS_OVERRIDES':json.dumps(overrides or {}), 'STATUS_REPEAT':str(repeat),
         'STATUS_RESTORE_ROUTING':'1' if restore_routing else ''},
        native_id=native_id, command=(sys.executable,str(script)), timeout=5,
        publication_status=callback, publish=forbidden, present=forbidden)


def audit(tmp_path):
    return [json.loads(line) for line in (tmp_path/'status-audit.jsonl').read_text().splitlines()]


def run_status(tmp_path, arguments, callback, **options):
    request=request_for(tmp_path, arguments, callback, **options)
    ids=[]
    async def no_questions(*args):
        pytest.fail('Status reads cannot request or synthesize authorization')
    error=None
    try:
        asyncio.run(CodexAdapter().run(request,lambda *args:None,no_questions,ids.append))
    except HarnessError as caught:
        error=caught
    return {'messages':audit(tmp_path),'ids':ids,'error':error}


def results(value):
    return [message['result'] for message in value['messages']
            if str(message.get('id','')).startswith('status-reverse-') and 'result' in message]


def test_scoped_status_callback_matches_native_tool_event_call_id(tmp_path):
    calls, events = [], []

    async def read(approval_id, call_id):
        calls.append((approval_id, call_id))
        return STATUS

    async def no_questions(*args):
        pytest.fail('Status lookup must not ask for approval')

    request = replace(request_for(tmp_path, {'approvalId': APPROVAL_ID}, None),
                      publication_status_with_call=read)
    asyncio.run(CodexAdapter().run(request, lambda kind, data: events.append((kind, data)),
                                  no_questions, lambda _: None))
    [tool] = [data for kind, data in events if kind == 'agent_event' and data.get('kind') == 'tool']
    assert tool['nativePublicationTool'] is True
    assert calls == [(APPROVAL_ID, tool['callId'])]


@pytest.mark.parametrize('enabled',[True,False])
def test_only_an_enabled_read_callback_registers_the_eager_status_tool(tmp_path, enabled):
    async def read(identifier):return STATUS
    value=run_status(tmp_path,{'approvalId':APPROVAL_ID},read if enabled else None,no_call=True)
    assert value['error'] is None
    start=next(message['params'] for message in value['messages'] if message.get('method')=='thread/start')
    tools={tool['name']:tool for tool in start['dynamicTools']}
    assert ('easel_publication_status' in tools) is enabled
    if enabled:
        tool=tools['easel_publication_status']
        assert tool['type']=='function' and tool['deferLoading'] is False
        schema=tool['inputSchema']
        assert schema['additionalProperties'] is False
        assert schema['required']==['approvalId']
        assert set(schema['properties'])=={'approvalId'}
        assert schema['properties']['approvalId']['pattern']=='^[a-f0-9]{32}$'
        assert 'easel_publication_status' in start['developerInstructions']


def test_status_queries_only_the_existing_approval_and_returns_the_read_only_host_result(tmp_path):
    seen=[]
    async def read(identifier):seen.append(identifier);return STATUS
    value=run_status(tmp_path,{'approvalId':APPROVAL_ID},read,repeat=2)
    assert value['error'] is None
    assert seen==[APPROVAL_ID,APPROVAL_ID]
    responses=results(value)
    assert len(responses)==2
    assert all(item['success'] is True and json.loads(item['contentItems'][0]['text'])==STATUS for item in responses)


@pytest.mark.parametrize('arguments',[
    None,[],{},'query',{'approvalId':None},{'approvalId':False},{'approvalId':1},
    {'approvalId':'A'*32},{'approvalId':'a'*31},{'approvalId':'a'*33},
    {'approvalId':'outputs/report.txt'},{'approvalId':'https://private.test/status'},
    {'approvalId':APPROVAL_ID,'command':'publish now'}, {'approvalId':APPROVAL_ID,'path':'/private/token'},
    {'approvalId':APPROVAL_ID,'url':'https://private.test'}, {'approvalId':APPROVAL_ID,'actorId':'other'},
    {'approvalId':APPROVAL_ID,'payloadHash':'b'*64}, {'approvalId':APPROVAL_ID,'approved':True},
])
def test_invalid_read_arguments_never_reach_the_host(tmp_path,arguments):
    seen=[]
    async def read(identifier):seen.append(identifier);return STATUS
    value=run_status(tmp_path,arguments,read)
    assert value['error'] is None
    assert seen==[] and results(value)[0]['success'] is False
    assert '/private/' not in json.dumps(results(value))
    assert 'private.test' not in json.dumps(results(value))


@pytest.mark.parametrize('overrides',[
    {'threadId':'foreign'},{'turnId':'foreign'},{'turnId':None},
    {'namespace':'foreign'},{'callId':''},{'callId':None},{'callId':'bad\ncall'},
])
def test_read_authority_is_confined_to_the_current_native_scope(tmp_path,overrides):
    seen=[]
    async def read(identifier):seen.append(identifier);return STATUS
    value=run_status(tmp_path,{'approvalId':APPROVAL_ID},read,overrides=overrides)
    assert seen==[]
    assert value['error'] is not None or results(value)[0]['success'] is False


@pytest.mark.parametrize('outcome',[None,[],{}, {'value':float('nan')},
    ValueError('/private/secret-token'),HarnessError('/private/secret-token'),OSError('/private/secret-token')])
def test_failed_status_reads_do_not_echo_sensitive_errors_or_fabricate_success(tmp_path,outcome):
    async def read(identifier):
        if isinstance(outcome,BaseException):raise outcome
        return outcome
    value=run_status(tmp_path,{'approvalId':APPROVAL_ID},read)
    assert value['error'] is None
    assert results(value)[0]['success'] is False
    assert 'secret-token' not in json.dumps(results(value))


def test_a_persisted_status_tool_resumes_on_a_fresh_process_without_registering_again(tmp_path):
    seen=[]
    async def read(identifier):seen.append(identifier);return STATUS
    first=run_status(tmp_path,{'approvalId':APPROVAL_ID},read,no_call=True,restore_routing=True)
    native_id=first['ids'][0]
    second=run_status(tmp_path,{'approvalId':APPROVAL_ID},read,native_id=native_id,restore_routing=True)
    assert second['error'] is None and second['ids']==[native_id]
    assert seen==[APPROVAL_ID] and results(second)[0]['success'] is True
    resumes=[m['params'] for m in second['messages'] if m.get('method')=='thread/resume']
    assert len(resumes)==1 and not {'dynamicTools','history','path'}&set(resumes[0])
    assert len({m['fixturePid'] for m in second['messages'] if m.get('method')=='initialize'})==2
    assert sum(m.get('method')=='thread/start' for m in second['messages'])==1


def test_an_old_thread_without_status_keeps_its_id_and_does_not_publish_as_a_query(tmp_path):
    seen=[]
    async def read(identifier):seen.append(identifier);return STATUS
    value=run_status(tmp_path,{'approvalId':APPROVAL_ID},read,native_id='old-thread',restore_routing=True)
    assert value['error'] is None and value['ids']==['old-thread'] and seen==[]
    assert not any(m.get('method')=='thread/start' for m in value['messages'])
    assert results(value)==[]


def test_a_restored_status_definition_does_not_enable_a_missing_host_callback(tmp_path):
    async def read(identifier):return STATUS
    first=run_status(tmp_path,{'approvalId':APPROVAL_ID},read,no_call=True,restore_routing=True)
    second=run_status(tmp_path,{'approvalId':APPROVAL_ID},None,native_id=first['ids'][0],restore_routing=True)
    assert second['error'] is None and results(second)[0]['success'] is False


def test_cancelling_a_pending_read_cancels_its_callback_without_any_submission(tmp_path):
    async def scenario():
        entered,cleaned=asyncio.Event(),asyncio.Event()
        async def read(identifier):
            entered.set()
            try:await asyncio.Future()
            finally:cleaned.set()
        async def no_questions(*args):pytest.fail('No approval may be requested by a read')
        request=request_for(tmp_path,{'approvalId':APPROVAL_ID},read)
        task=asyncio.create_task(CodexAdapter().run(request,lambda *args:None,no_questions,lambda value:None))
        try:
            await asyncio.wait_for(entered.wait(),2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,3)
            assert cleaned.is_set()
            assert not any(m.get('result',{}).get('success') for m in audit(tmp_path)
                           if str(m.get('id','')).startswith('status-reverse-'))
        finally:
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(scenario())
