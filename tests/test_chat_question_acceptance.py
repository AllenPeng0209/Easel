"""B32 exact scoped approval receipts are server authoritative."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from easel.request_identity import ActorMap
import web.app as web


@pytest.fixture
def scoped_questions(monkeypatch):
    monkeypatch.setattr(web, '_HARNESS_QUESTIONS', ActorMap())
    monkeypatch.setattr(web, '_HARNESS_QUESTION_RESULTS', ActorMap())
    monkeypatch.setenv('EASEL_AGENT_APPROVAL_WAIT_SECONDS', '1')


async def open_question(events):
    task = asyncio.create_task(web._harness_ask('session', [{
        'questionId': 'permission', 'kind': 'approval', 'question': '允许发布？',
        'options': [{'label': '批准'}, {'label': '拒绝'}],
    }], lambda kind, payload: events.append((kind, payload)), turn_id='turn'))
    await asyncio.sleep(0)
    identifier = json.loads(next(value for kind, value in events if kind == 'question'))['id']
    return task, identifier


def answer(identifier, value, **scope):
    return web.QuestionAnswerRequest(questionId=identifier, answers={'permission': [value]}, **scope)


def test_exact_duplicate_replays_server_receipt_and_never_changes_decision(scoped_questions):
    async def scenario():
        events = []
        task, identifier = await open_question(events)
        first, duplicate = await asyncio.gather(
            web.api_question_answer(answer(identifier, '批准', sessionId='session', turnId='turn')),
            web.api_question_answer(answer(identifier, '批准', sessionId='session', turnId='turn')),
        )
        assert first['ok'] and duplicate['ok']
        assert first['receipt'] == duplicate['receipt'] == {
            'id': identifier, 'status': 'resolved', 'answers': {'permission': ['批准']}, 'resolvedBy': 'human',
        }
        assert first.get('replayed') is not True and duplicate['replayed'] is True
        conflict = await web.api_question_answer(answer(identifier, '拒绝'))
        assert not conflict['ok'] and conflict['receipt'] == first['receipt']
        assert await task == {'permission': ['批准']}
        assert sum(kind == 'question_closed' for kind, _ in events) == 1
    asyncio.run(scenario())


def test_wrong_turn_and_forged_option_do_not_consume_pending_question(scoped_questions):
    async def scenario():
        events = []
        task, identifier = await open_question(events)
        try:
            wrong = await web.api_question_answer(answer(identifier, '批准', sessionId='different', turnId='turn'))
            assert not wrong['ok'] and wrong['status'] == 'replaced'
            with pytest.raises(HTTPException) as error:
                await web.api_question_answer(answer(identifier, 'raw permission text'))
            assert error.value.status_code == 400
            assert not task.done()
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        cancelled = await web.api_question_answer(answer(identifier, '批准'))
        assert not cancelled['ok'] and cancelled['status'] == 'cancelled'
    asyncio.run(scenario())


def test_deadline_expired_before_runner_resumes_cannot_approve(scoped_questions):
    async def scenario():
        task, identifier = await open_question([])
        record = web._HARNESS_QUESTIONS[identifier]
        record['deadline'] = 0
        expired = await web.api_question_answer(answer(identifier, '批准'))
        assert not expired['ok'] and expired['status'] == 'expired'
        assert not record['future'].done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())


def test_revocation_before_runner_consumption_prevents_accepted_receipt(scoped_questions, monkeypatch):
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise HTTPException(403, 'revoked')

    monkeypatch.setattr(web, '_require_host_tools', check)
    async def scenario():
        events = []
        task, identifier = await open_question(events)
        result = await web.api_question_answer(answer(identifier, '批准'))
        assert not result['ok'] and result['status'] == 'revoked'
        with pytest.raises(HTTPException):
            await task
        closed = [json.loads(value) for kind, value in events if kind == 'question_closed']
        assert closed == [{'id': identifier, 'status': 'revoked'}]
    asyncio.run(scenario())


def test_gateway_ambiguous_ack_never_resends_and_requires_exact_readback(scoped_questions, monkeypatch):
    import threading

    record = {'id': 'gateway-question', 'session': 'session', 'turnId': 'turn', 'status': 'pending',
              'questions': [{'questionId': 'permission', 'options': [{'label': '批准'}]}],
              'lock': threading.Lock(), 'emit': lambda *_: None}
    calls = []

    class Client:
        def connect(self):
            pass

        def resolve(self, identifier, answers, source):
            calls.append(('resolve', identifier))
            return {'ok': True}

        def get_question(self, identifier):
            calls.append(('get', identifier))
            return {'id': identifier, 'sessionKey': 'agent:main:session', 'status': 'answered',
                    'answers': {'answers': {'permission': ['批准']}}, 'resolvedBy': 'human'}

        def close(self):
            pass

    monkeypatch.setattr(web, '_GATEWAY_QUESTION_POLICIES', ActorMap())
    web._GATEWAY_QUESTION_POLICIES[record['id']] = record
    monkeypatch.setattr(web, '_direct_api_enabled', lambda: False)
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'openclaw'})
    monkeypatch.setattr(web, 'GatewayClient', Client)
    request = answer(record['id'], '批准', sessionId='session', turnId='turn')
    first = asyncio.run(web.api_question_answer(request))
    assert not first['ok'] and first['status'] == 'unknown'
    replay = asyncio.run(web.api_question_answer(request))
    assert replay['ok'] and replay['receipt']['answers'] == {'permission': ['批准']}
    assert calls == [('resolve', record['id']), ('get', record['id'])]


def test_unowned_gateway_question_cannot_reach_provider(scoped_questions, monkeypatch):
    from easel.request_identity import actor_context

    monkeypatch.setattr(web, '_GATEWAY_QUESTION_POLICIES', ActorMap())
    monkeypatch.setattr(web, '_direct_api_enabled', lambda: False)
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'openclaw'})
    monkeypatch.setattr(web, '_require_host_tools', lambda: None)
    monkeypatch.setattr(web, 'GatewayClient', lambda: pytest.fail('Unowned ID cannot reach provider'))
    with actor_context({'id': 'operator'}):
        denied = asyncio.run(web.api_question_answer(answer('foreign', '批准')))
        status = asyncio.run(web.api_question_status(web.QuestionStatusRequest(questionIds=['foreign'])))
    assert not denied['ok'] and denied['status'] == 'expired'
    assert status == {'ok': True, 'questions': {'foreign': {'status': 'not_found'}}}
