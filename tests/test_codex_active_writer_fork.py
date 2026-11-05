"""A narrowly proven writer conflict forks history without replacing Host authority."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.codex import CodexAdapter
from easel.harnesses.rpc import RPC, RpcRejected
from easel.harnesses import store


OLD = '11111111-1111-7111-8111-111111111111'
NEW = '22222222-2222-7222-8222-222222222222'
PEER = r'''
import json, os, sys
old, new = os.environ['OLD'], os.environ['NEW']
mode = os.environ.get('MODE', 'resume')
error = json.loads(os.environ['REJECTION'])
calls = json.loads(os.environ.get('TOOLS', '[]'))
thread, turn, index = old, 'continued-turn', 0
def send(value): print(json.dumps(value), flush=True)
def next_item():
 global index
 if index == len(calls):
  send({'method':'turn/completed','params':{'threadId':thread,'turnId':turn,'turn':{'id':turn,'status':'completed'}}})
  return
 item=calls[index]; index+=1
 send({'id':'host-call-'+str(index),'method':'item/tool/call','params':{'threadId':thread,'turnId':turn,
       'callId':'continued-call-'+str(index),'tool':item['tool'],'arguments':item['arguments']}})
for line in sys.stdin:
 m=json.loads(line)
 with open(os.environ['AUDIT'],'a') as f: f.write(json.dumps(m)+'\n')
 method=m.get('method')
 if method=='initialize':
  send({'id':m['id'],'error':error} if mode=='initialize' else {'id':m['id'],'result':{}})
 elif method=='thread/resume':
  send({'id':m['id'],'error':error} if mode=='resume' else {'id':m['id'],'result':{'thread':{'id':old}}})
 elif method=='thread/fork':
  if os.environ.get('FORK_FAIL'): send({'id':m['id'],'error':error})
  else:
   thread=new
   send({'id':m['id'],'result':{'thread':{'id':new,'forkedFromId':os.environ.get('FORK_FROM',old)}}})
 elif method=='turn/start':
  if mode=='turn': send({'id':m['id'],'error':error})
  else:
   send({'id':m['id'],'result':{'turn':{'id':turn}}}); next_item()
 elif str(m.get('id','')).startswith('host-call-'): next_item()
 elif method=='turn/interrupt': send({'id':m['id'],'result':{}})
'''


def request(tmp_path, *, rejection=None, mode='resume', callback=True, tools=(), **overrides):
    script = tmp_path / 'writer-peer.py'
    script.write_text(PEER)
    directory = tmp_path / 'sessions'
    store.bind(directory, 'original-host-session', 'codex', OLD)
    transitions = []
    def rebind(old, new):
        store.rebind_active_writer(directory, 'original-host-session', old, new)
        transitions.append((old, new))
    env = {**os.environ, 'OLD': OLD, 'NEW': NEW, 'AUDIT': str(tmp_path / 'audit.jsonl'),
        'MODE': mode, 'REJECTION': json.dumps(rejection or {'code': -32600,
            'message': f'thread {OLD} already has an active writer'}), 'TOOLS': json.dumps(list(tools))}
    env.update(overrides.pop('env', {}))
    value = RunRequest('Continue this exact original input once', tmp_path, env, native_id=OLD,
        model='approved-model', sandbox='workspace-write', session_key='original-host-session',
        command=(sys.executable, str(script)), rebind_active_writer=rebind if callback else None, **overrides)
    return value, directory, transitions


def run(value, directory):
    async def forbidden(*_args):
        pytest.fail('Fork recovery must never approve or ask a new production task')
    bound, events = [], []
    def bind(identifier):
        store.bind(directory, 'original-host-session', 'codex', identifier)
        bound.append(identifier)
    error = None
    try:
        asyncio.run(CodexAdapter().run(value, lambda kind, data: events.append((kind, data)), forbidden, bind))
    except HarnessError as caught:
        error = caught
    audit = [json.loads(line) for line in (value.cwd / 'audit.jsonl').read_text().splitlines()]
    return error, audit, bound, events


def test_exact_original_resume_writer_rejection_forks_history_and_continues_input_once(tmp_path):
    value, directory, transitions = request(tmp_path)
    error, audit, bound, _ = run(value, directory)
    assert error is None
    assert [m['method'] for m in audit if m.get('method')] == ['initialize', 'initialized', 'thread/resume', 'thread/fork', 'turn/start']
    fork = next(m['params'] for m in audit if m.get('method') == 'thread/fork')
    resumed = next(m['params'] for m in audit if m.get('method') == 'thread/resume')
    assert fork['threadId'] == OLD and fork['deferGoalContinuation'] is True
    assert {k: fork[k] for k in resumed} == resumed
    assert not {'dynamicTools', 'history', 'path', 'lastTurnId', 'beforeTurnId'} & set(fork)
    turn = next(m['params'] for m in audit if m.get('method') == 'turn/start')
    assert turn['threadId'] == NEW and turn['input'] == [{'type': 'text', 'text': value.prompt}]
    assert turn['model'] == 'approved-model' and turn['sandboxPolicy']['networkAccess'] is False
    assert transitions == [(OLD, NEW)] and bound == [NEW]
    assert value.native_id == OLD and value.session_key == 'original-host-session'
    assert store.binding(directory, value.session_key)['native_id'] == NEW


@pytest.mark.parametrize('mode,rejection', [
    ('initialize', {'code': -32600, 'message': f'thread {OLD} already has an active writer'}),
    ('turn', {'code': -32600, 'message': f'thread {OLD} already has an active writer'}),
    ('resume', {'code': -32602, 'message': f'thread {OLD} already has an active writer'}),
    ('resume', {'code': -32600, 'message': f'thread {NEW} already has an active writer'}),
    ('resume', {'code': -32600, 'message': f'thread {OLD} already has an active writer extra'}),
    ('resume', {'code': -32600, 'message': 'unrelated private error'}),
])
def test_other_rejections_never_fork_or_replace_binding(tmp_path, mode, rejection):
    value, directory, transitions = request(tmp_path, mode=mode, rejection=rejection)
    error, audit, _, _ = run(value, directory)
    assert error is not None
    assert not any(m.get('method') in ('thread/fork', 'thread/start') for m in audit)
    assert transitions == [] and store.binding(directory, value.session_key)['native_id'] == OLD


@pytest.mark.parametrize('variation', ['no-capability', 'fork-rejected', 'same-id', 'missing-id', 'wrong-parent', 'cas-drift'])
def test_failed_fork_or_unproven_host_transition_never_starts_a_turn(tmp_path, variation):
    env = {'FORK_FAIL': '1'} if variation == 'fork-rejected' else {}
    if variation == 'same-id': env['NEW'] = OLD
    if variation == 'missing-id': env['NEW'] = ''
    if variation == 'wrong-parent': env['FORK_FROM'] = NEW
    value, directory, transitions = request(tmp_path, callback=variation != 'no-capability', env=env)
    if variation == 'cas-drift':
        store.rebind_active_writer(directory, value.session_key, OLD, 'third-native')
    error, audit, _, _ = run(value, directory)
    assert error is not None and transitions == []
    assert not any(m.get('method') in ('thread/start', 'turn/start') for m in audit)


def test_rpc_retains_only_structured_same_request_writer_conflict_without_echoing_payload(tmp_path):
    value, _, _ = request(tmp_path)
    async def scenario():
        async with RPC(value, list(value.command), lambda *_: None, lambda *_: None) as rpc:
            await rpc.call('initialize', {})
            with pytest.raises(RpcRejected) as caught:
                await rpc.call('thread/resume', {'threadId': OLD})
            assert caught.value.failure_code == 'session_conflict'
            assert getattr(caught.value, 'method', None) == 'thread/resume'
            assert getattr(caught.value, 'active_writer_thread_id', None) == OLD
            assert OLD not in str(caught.value)
    asyncio.run(scenario())


def test_internal_native_fork_does_not_reset_host_video_budget_or_confirmed_approval(tmp_path):
    from tests.native_video_fixtures import setup, action
    from easel.publication_approvals import PublicationApprovalStore
    _, service, backend, _, _, payload, _ = setup.__wrapped__(tmp_path / 'budget')
    service.run(payload)
    service.run(action(payload, 'submit'))
    scope = {'actor_id': service.actor_id, 'project_id': service.ip}
    approvals = PublicationApprovalStore(tmp_path / 'private-approvals')
    approval = approvals.create({'platform': {'id': 'douyin'}, 'project': {'id': service.ip},
        'account': {'id': 'uid:owner'}, 'mode': 'publish_now', 'body': 'approved before fork', 'media': []}, **scope)
    approval = approvals.decide(approval['id'], True, approval['payloadHash'], **scope)
    approval = approvals.claim(approval['id'], approval['subject'], **scope)
    approval = approvals.finish(approval['id'], approval['operationId'],
        {'status': 'confirmed', 'externalId': 'original-public-post'}, **scope)
    seen = []
    async def video(arguments, call_id):
        seen.append((service.session_id, call_id))
        return service.run(arguments)
    async def status(identifier, call_id):
        assert identifier == approval['id']
        return approvals.get(identifier, **scope)
    value, directory, transitions = request(tmp_path, video_generation=video,
        publication_status_with_call=status, tools=[
            {'tool': 'easel_video_generation', 'arguments': {**payload, 'clipId': 'unapproved-second'}},
            {'tool': 'easel_publication_status', 'arguments': {'approvalId': approval['id']}}])
    error, audit, _, _ = run(value, directory)
    assert error is None and transitions == [(OLD, NEW)]
    replies = [m['result'] for m in audit if str(m.get('id', '')).startswith('host-call-')]
    assert replies[0]['success'] is False and replies[1]['success'] is True
    assert len(backend.submitted) == 1 and seen[0][0] == 'host-session'
    assert approvals.get(approval['id'], **scope) == approval
