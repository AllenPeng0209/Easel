"""User-visible Codex diagnostics through the real adapter and stdio RPC reader."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.codex import CodexAdapter


# A deterministic protocol peer, not a model invocation. Errors use the same
# nested JSON-in-message shape returned by a real Codex account/model failure.
PEER = r'''
import json, os, sys
payload = json.loads(os.environ['FAKE_CODEX_ERROR'])
mode = os.environ['FAKE_CODEX_MODE']
def send(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialize':
        send({'id':message['id'], 'result':{}})
    elif method == 'thread/start':
        send({'id':message['id'], 'result':{'thread':{'id':'thread-a'}}})
    elif method == 'turn/start':
        common = {'threadId':'thread-a', 'turnId':'turn-a'}
        if mode == 'filtered':
            send({'method':'error','params':{**common,'threadId':'another-thread','willRetry':False,'error':payload}})
            send({'method':'error','params':{**common,'willRetry':True,'error':payload}})
            send({'method':'item/agentMessage/delta','params':{**common,'itemId':'reply','delta':'valid reply'}})
            send({'method':'turn/completed','params':{**common,'turn':{'id':'turn-a','status':'completed'}}})
        elif mode == 'completed':
            send({'method':'turn/completed','params':{**common,'turn':{'id':'turn-a','status':'failed','error':payload}}})
        else:
            send({'method':'error','params':{**common,'willRetry':False,'error':payload}})
        send({'id':message['id'], 'result':{'turn':{'id':'turn-a'}}})
'''


def run_adapter(tmp_path, error, mode='fatal'):
    peer = tmp_path / 'codex-diagnostic-peer.py'
    peer.write_text(PEER, encoding='utf-8')
    env = {**os.environ, 'FAKE_CODEX_ERROR': json.dumps(error), 'FAKE_CODEX_MODE': mode}
    request = RunRequest('hello', tmp_path, env, sandbox='read-only',
                         command=(sys.executable, str(peer)))
    events = []
    async def reject(questions):
        raise AssertionError('No tool interaction is part of this diagnostic')
    asyncio.run(asyncio.wait_for(CodexAdapter().run(
        request, lambda kind, text: events.append((kind, text)), reject, lambda identifier: None), timeout=3))
    return events


@pytest.mark.parametrize('mode', ['fatal', 'completed'])
@pytest.mark.parametrize('error,expected', [
    ({'message': json.dumps({'type':'error','status':400,'error':{
        'type':'invalid_request_error', 'message':"The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.",
        'code':'model_not_found'}}), 'codexErrorInfo':'other'}, '模型覆盖'),
    ({'message':json.dumps({'status':401,'error':{'code':'invalid_api_key',
        'message':'invalid credential sk-secret-not-from-environment'}})}, 'codex login'),
    ({'message':json.dumps({'status':429,'error':{'code':'rate_limit_exceeded',
        'message':'usage exhausted; token=private-oauth-value'}})}, '用量'),
    ({'message':'connection failed at https://private.example/path?token=private-oauth-value',
      'codexErrorInfo':{'httpConnectionFailed':{'httpStatusCode':503}}}, '网络'),
])
def test_upstream_failure_becomes_an_actionable_safe_diagnostic(tmp_path, error, expected, mode):
    with pytest.raises(HarnessError) as caught:
        run_adapter(tmp_path, error, mode)
    message = str(caught.value)
    assert expected in message
    for secret in ('sk-secret-not-from-environment', 'private-oauth-value', 'private.example', 'https://'):
        assert secret not in message


@pytest.mark.parametrize('mode', ['fatal', 'completed'])
def test_unknown_error_never_echoes_upstream_tokens_urls_or_payloads(tmp_path, mode):
    error = {'message':'unexpected Bearer private-oauth-value https://private.example/?token=private-oauth-value'}
    with pytest.raises(HarnessError) as caught:
        run_adapter(tmp_path, error, mode)
    assert '执行失败' in str(caught.value)
    assert 'private-oauth-value' not in str(caught.value)
    assert 'private.example' not in str(caught.value)


def test_empty_fatal_error_preserves_the_generic_fallback(tmp_path):
    with pytest.raises(HarnessError, match='Codex 执行失败，请检查原生登录与模型配置'):
        run_adapter(tmp_path, {})


def test_retrying_and_foreign_thread_failures_do_not_override_current_success(tmp_path):
    assert run_adapter(tmp_path, {'message':'model_not_found'}, 'filtered') == [('token', 'valid reply')]


@pytest.mark.skipif(os.name == 'nt', reason='Executable fixture uses a POSIX shebang')
def test_default_adapter_command_honors_the_easel_codex_executable(tmp_path, monkeypatch):
    import easel.harnesses.codex as codex_module
    peer = tmp_path / 'selected native codex'
    peer.write_text(f'#!{sys.executable}\n' + PEER, encoding='utf-8')
    peer.chmod(0o755)
    env = {**os.environ, 'FAKE_CODEX_ERROR':'{}', 'FAKE_CODEX_MODE':'filtered',
           'EASEL_CODEX_EXECUTABLE': str(peer)}
    monkeypatch.setattr(codex_module, 'base_command', lambda name: pytest.fail('The old PATH CLI was selected'))
    request = RunRequest('hello', tmp_path, env, sandbox='read-only')
    events = []
    async def reject(questions):
        raise AssertionError('No tool interaction is expected')
    asyncio.run(asyncio.wait_for(CodexAdapter().run(
        request, lambda kind, text: events.append((kind, text)), reject, lambda identifier: None), timeout=3))
    assert events == [('token', 'valid reply')]
