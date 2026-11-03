"""A05 steering is scoped to an active Codex turn and requires a matching ack."""
import asyncio
import json
import os
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.codex import CodexAdapter


WIRE = '''
import json, os, sys

def send(message):
    print(json.dumps(message), flush=True)

def done():
    send({'method': 'turn/completed', 'params': {
        'threadId': 'steer-thread', 'turn': {'id': 'steer-turn', 'status': 'completed'}}})

mode = os.environ['STEER_FIXTURE_MODE']
for line in sys.stdin:
    message = json.loads(line)
    with open(os.environ['STEER_FIXTURE_LOG'], 'a') as audit:
        audit.write(json.dumps(message) + '\\n')
    method = message.get('method')
    if method == 'initialize':
        send({'id': message['id'], 'result': {}})
    elif method == 'thread/start':
        send({'id': message['id'], 'result': {'thread': {'id': 'steer-thread'}}})
    elif method == 'turn/start':
        if mode == 'start-rejected':
            send({'id': message['id'], 'error': {'code': -32602, 'message': 'invalid'}})
            continue
        if mode == 'already-completed':
            done()
        send({'id': message['id'], 'result': {'turn': {'id': 'steer-turn'}}})
        if mode in ('approval', 'user-input'):
            params = {'threadId': 'steer-thread', 'turnId': 'steer-turn', 'command': 'read file',
                      'questions': [{'id': 'native-question', 'question': 'Which title?'}]}
            send({'id': 'native-question', 'method':
                  'item/commandExecution/requestApproval' if mode == 'approval'
                  else 'item/tool/requestUserInput', 'params': params})
    elif method == 'turn/steer':
        if mode == 'disconnect':
            break
        if mode == 'reject':
            send({'id': message['id'], 'error': {'code': -32602, 'message': 'turn changed'}})
        else:
            turn = 'different-turn' if mode == 'wrong-ack' else 'steer-turn'
            send({'id': message['id'], 'result': {'turnId': turn}})
        done()
    elif message.get('id') == 'native-question':
        done()
    elif method == 'turn/interrupt':
        send({'id': message['id'], 'result': {}})
        done()
'''


def request_for(tmp_path, mode, bind):
    assert 'bind_steer' in RunRequest.__dataclass_fields__, 'The native turn needs a steering binder'
    script = tmp_path / 'wire.py'
    script.write_text(WIRE)
    return RunRequest('Initial prompt', tmp_path, {
        **os.environ, 'STEER_FIXTURE_MODE': mode, 'STEER_FIXTURE_LOG': str(tmp_path / 'wire.jsonl'),
    }, command=(sys.executable, str(script)), bind_steer=bind)


def messages(tmp_path, method):
    return [message for line in (tmp_path / 'wire.jsonl').read_text().splitlines()
            if (message := json.loads(line)).get('method') == method]


@pytest.mark.parametrize('mode', ['accepted', 'reject', 'wrong-ack', 'disconnect'])
def test_steer_sends_exact_turn_precondition_and_requires_matching_ack(tmp_path, mode):
    async def scenario():
        from easel.harnesses.base import SteerRejected

        available, bindings = asyncio.Event(), []

        def bind(callback):
            bindings.append(callback)
            if callback is not None:
                available.set()

        async def ask(_questions):
            pytest.fail('This fixture has no reverse request')

        request = request_for(tmp_path, mode, bind)
        task = asyncio.create_task(CodexAdapter().run(request, lambda *_: None, ask, lambda _: None))
        try:
            await asyncio.wait_for(available.wait(), 2)
            callback = bindings[0]
            if mode == 'accepted':
                assert await callback('继续整理选题') is None
            else:
                with pytest.raises(HarnessError) as caught:
                    await callback('继续整理选题')
                assert isinstance(caught.value, SteerRejected) is (mode == 'reject')
            if mode == 'disconnect':
                with pytest.raises(HarnessError):
                    await asyncio.wait_for(task, 2)
            else:
                await asyncio.wait_for(task, 2)
            assert bindings == [callback, None]
            with pytest.raises(SteerRejected):
                await callback('旧回合回调')
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert [message['params'] for message in messages(tmp_path, 'turn/steer')] == [{
        'threadId': 'steer-thread', 'expectedTurnId': 'steer-turn',
        'input': [{'type': 'text', 'text': '继续整理选题'}],
    }]


@pytest.mark.parametrize('mode', ['approval', 'user-input'])
def test_pending_native_question_prevents_steering(tmp_path, mode):
    async def scenario():
        from easel.harnesses.base import SteerRejected

        bindings, waiting, answer = [], asyncio.Event(), asyncio.Event()

        async def ask(questions):
            waiting.set()
            await answer.wait()
            return {question['questionId']: ['拒绝'] for question in questions}

        task = asyncio.create_task(CodexAdapter().run(
            request_for(tmp_path, mode, bindings.append), lambda *_: None, ask, lambda _: None))
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            with pytest.raises(SteerRejected):
                await bindings[0]('不要借此通过授权')
            answer.set()
            await asyncio.wait_for(task, 2)
            assert bindings[-1] is None
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert messages(tmp_path, 'turn/steer') == []


@pytest.mark.parametrize('mode', ['start-rejected', 'already-completed'])
def test_never_exposes_a_callback_when_no_active_turn_started(tmp_path, mode):
    bindings = []

    async def ask(_questions):
        pytest.fail('This fixture has no reverse request')

    request = request_for(tmp_path, mode, bindings.append)
    run = CodexAdapter().run(request, lambda *_: None, ask, lambda _: None)
    if mode == 'start-rejected':
        with pytest.raises(HarnessError):
            asyncio.run(asyncio.wait_for(run, 2))
    else:
        asyncio.run(asyncio.wait_for(run, 2))
    assert bindings == [None]
    assert messages(tmp_path, 'turn/steer') == []


def test_cancellation_clears_bound_steer_and_interrupts_its_turn(tmp_path):
    async def scenario():
        from easel.harnesses.base import SteerRejected

        available, bindings = asyncio.Event(), []

        def bind(callback):
            bindings.append(callback)
            if callback is not None:
                available.set()

        async def ask(_questions):
            pytest.fail('This fixture has no reverse request')

        task = asyncio.create_task(CodexAdapter().run(
            request_for(tmp_path, 'waiting', bind), lambda *_: None, ask, lambda _: None))
        try:
            await asyncio.wait_for(available.wait(), 2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
        assert bindings == [bindings[0], None]
        with pytest.raises(SteerRejected):
            await bindings[0]('旧回合回调')

    asyncio.run(scenario())
    assert messages(tmp_path, 'turn/steer') == []
    assert messages(tmp_path, 'turn/interrupt')[0]['params'] == {
        'threadId': 'steer-thread', 'turnId': 'steer-turn',
    }
