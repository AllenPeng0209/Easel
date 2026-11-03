"""Autonomous questions must continue deterministically without granting permissions."""
import asyncio
import json

import pytest

import web.app as web
from easel.request_identity import ActorMap


def test_unanswered_preference_uses_default_and_records_one_decision(monkeypatch):
    monkeypatch.setenv('EASEL_AGENT_QUESTION_WAIT_SECONDS', '0.01')
    monkeypatch.setattr(web, '_HARNESS_QUESTIONS', ActorMap())
    events = []

    async def scenario():
        answers = await asyncio.wait_for(web._harness_ask('pref', [{
            'questionId': 'tone', 'question': '口播语气？',
            'options': [{'label': '轻松'}, {'label': '专业', 'recommended': True}],
        }], lambda kind, payload: events.append((kind, payload))), 1)
        assert answers == {'tone': ['专业']}
        question = json.loads(next(value for kind, value in events if kind == 'question'))
        assert question['kind'] == 'preference'
        assert question['requiresHuman'] is False
        assert question['defaultAnswers'] == {'tone': ['专业']}
        assert question['autoResolveAtMs'] > 0
        decisions = [value for kind, value in events if kind == 'agent_event']
        assert len(decisions) == 1
        assert decisions[0]['kind'] == 'decision'
        assert decisions[0]['source'] == 'default'
        assert decisions[0]['answers'] == {'tone': ['专业']}
        closed = json.loads(next(value for kind, value in events if kind == 'question_closed'))
        assert closed['answers'] == {'tone': ['专业']}
        assert closed['resolvedBy'] == 'default'
        assert not web._HARNESS_QUESTIONS

    asyncio.run(scenario())


def test_manual_answer_wins_and_no_later_default_is_emitted(monkeypatch):
    monkeypatch.setenv('EASEL_AGENT_QUESTION_WAIT_SECONDS', '0.03')
    monkeypatch.setattr(web, '_HARNESS_QUESTIONS', ActorMap())
    events = []

    async def scenario():
        task = asyncio.create_task(web._harness_ask('manual', [{
            'questionId': 'tone', 'question': '口播语气？',
            'options': [{'label': '轻松'}, {'label': '专业'}],
        }], lambda kind, payload: events.append((kind, payload))))
        await asyncio.sleep(0)
        identifier = next(iter(web._HARNESS_QUESTIONS))
        result = await web.api_question_answer(web.QuestionAnswerRequest(
            questionId=identifier, answers={'tone': ['专业']}))
        assert result['ok']
        assert await task == {'tone': ['专业']}
        await asyncio.sleep(0.05)
        decisions = [value for kind, value in events if kind == 'agent_event']
        assert len(decisions) == 1
        assert decisions[0]['source'] == 'human'

    asyncio.run(scenario())


def test_unanswered_permission_is_declined_never_first_option(monkeypatch):
    monkeypatch.setenv('EASEL_AGENT_APPROVAL_WAIT_SECONDS', '0.01')
    monkeypatch.setattr(web, '_HARNESS_QUESTIONS', ActorMap())
    events = []

    async def scenario():
        answers = await asyncio.wait_for(web._harness_ask('approval', [{
            'questionId': 'permission', 'header': '执行授权', 'question': '允许本次？',
            'options': [{'label': '允许本次'}, {'label': '拒绝'}],
        }], lambda kind, payload: events.append((kind, payload))), 1)
        assert answers == {'permission': ['拒绝']}
        question = json.loads(next(value for kind, value in events if kind == 'question'))
        assert question['requiresHuman'] is True
        assert question['autoResolveAtMs'] is None
        assert question['defaultAnswers'] == {}
        decision = next(value for kind, value in events if kind == 'agent_event')
        assert decision['status'] == 'declined'
        assert decision['source'] == 'timeout'

    asyncio.run(scenario())


def test_risky_question_cannot_opt_itself_out_of_approval(monkeypatch):
    from easel.agent_policy import question_policy
    policy = question_policy([{'questionId': 'publish', 'requiresHuman': False,
        'question': '确认立即发布到抖音账号？', 'options': [{'label': '确认发布'}, {'label': '稍后'}]}])
    assert policy.requires_human
    assert policy.default_answers == {}


def test_autonomous_instruction_requires_completion_without_invented_facts(tmp_path):
    from easel.harnesses.base import RunRequest
    from easel.harnesses.workspace import harness_instructions
    text = harness_instructions(RunRequest('create', tmp_path, {}))
    assert '自主推进' in text
    assert '默认' in text
    assert '不得虚构' in text


def test_structured_agent_event_replay_is_one_json_object(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path)
    event = {'id': 'event-1', 'kind': 'tool', 'callId': 'cmd-1', 'name': 'bash',
             'status': 'completed', 'input': {'command': 'pwd'}, 'output': 'workspace', 'exitCode': 0}
    path = web._job_event_file('structured')
    path.parent.mkdir(parents=True)
    path.write_text('\n'.join(json.dumps(x) for x in [
        {'id': 1, 'event': 'agent_event', 'data': event},
        {'id': 2, 'event': 'done', 'data': {'sessionKey': 'session'}},
    ]))
    async def scenario():
        response = await web.api_chat_job_stream('structured')
        result = [item async for item in response.body_iterator]
        assert json.loads(result[0]['data']) == event
    asyncio.run(scenario())


def test_stop_does_not_claim_success_while_supervisor_still_owns_session(monkeypatch):
    import subprocess
    monkeypatch.setattr(web, '_RUNNING_CHAT', ActorMap())
    monkeypatch.setattr(web, '_STOPPED_CHAT', set())
    monkeypatch.setattr(web, '_CHAT_STOP_CONFIRM_SECONDS', 0.01, raising=False)

    class Process:
        def poll(self):
            return None
        def terminate(self):
            pass
        def kill(self):
            pass
        def wait(self, timeout):
            raise subprocess.TimeoutExpired('fixture', timeout)

    web._RUNNING_CHAT['still-running'] = Process()
    result = asyncio.run(web.api_chat_stop(web.StopRequest(sessionId='still-running')))
    assert result['stopped'] is False
    assert result['status'] == 'stopping'


def test_stop_reports_already_finished_without_fabricated_cancellation(monkeypatch):
    monkeypatch.setattr(web, '_RUNNING_CHAT', ActorMap())
    result = asyncio.run(web.api_chat_stop(web.StopRequest(sessionId='absent')))
    assert result['stopped'] is False
    assert result['status'] == 'already_finished'


@pytest.mark.parametrize('question', [
    {'questionId': 'next', 'kind': 'preference', 'question': 'What next?', 'options': [
        {'label': 'Publish now', 'recommended': True}, {'label': 'Cancel'}]},
    {'questionId': 'account', 'question': 'Provide the account password', 'defaultAnswer': 'placeholder'},
    {'questionId': 'cleanup', 'question': '清空全部历史后继续？', 'options': [{'label': '继续'}]},
])
def test_risky_options_credentials_and_erase_never_auto_approve(question):
    from easel.agent_policy import question_policy
    policy = question_policy([question])
    assert policy.requires_human
    assert policy.default_answers == {}


def test_numbered_native_reject_option_is_a_safe_timeout_choice():
    from easel.agent_policy import question_policy
    policy = question_policy([{'questionId': 'permission', 'question': 'Execution approval',
        'options': [{'label': '1. Allow'}, {'label': '2. Reject'}]}])
    assert policy.timeout_answers == {'permission': ['2. Reject']}


def test_secret_question_is_never_echoed_or_defaulted(monkeypatch):
    monkeypatch.setenv('EASEL_AGENT_APPROVAL_WAIT_SECONDS', '0.01')
    from easel.harnesses.base import HarnessError
    monkeypatch.setattr(web, '_HARNESS_QUESTIONS', ActorMap())
    events = []
    with pytest.raises(HarnessError, match='原生终端'):
        asyncio.run(web._harness_ask('secret', [{
            'questionId': 'key', 'question': 'Provide password', 'isSecret': True,
            'defaultAnswer': 'private-do-not-expose', 'options': [{'label': 'yes', 'apiKey': 'private-do-not-expose'}],
        }], lambda kind, value: events.append((kind, value))))
    assert events == []


def test_question_projection_drops_unknown_option_and_default_metadata(monkeypatch):
    monkeypatch.setenv('EASEL_AGENT_QUESTION_WAIT_SECONDS', '0.01')
    events = []
    asyncio.run(web._harness_ask('clean', [{
        'questionId': 'tone', 'question': '语气？', 'opaque': 'never-show-me',
        'options': [{'label': '专业', 'opaque': 'never-show-me'}],
    }], lambda kind, value: events.append((kind, value))))
    assert 'never-show-me' not in json.dumps(events, ensure_ascii=False)


def test_gateway_question_defaults_on_server_without_browser(monkeypatch):
    import time
    monkeypatch.setenv('EASEL_AGENT_QUESTION_WAIT_SECONDS', '0.01')
    monkeypatch.setattr(web, '_QBRIDGE_DISABLED', False)
    monkeypatch.setattr(web, 'question_bridge_supported', lambda: True)
    monkeypatch.setattr(web, '_GATEWAY_QUESTION_POLICIES', ActorMap(), raising=False)
    events = []

    class Client:
        resolved = None
        def connect(self):
            pass
        def list_questions(self, **kwargs):
            return [] if self.resolved is not None else [{'id': 'ordinary', 'questions': [{
                'questionId': 'tone', 'question': '语气？', 'options': [{'label': '专业'}]}]}]
        def resolve(self, identifier, answers, resolved_by=None):
            assert identifier == 'ordinary'
            self.resolved = answers
            return {'ok': True}
        def close(self):
            pass
    client = Client()
    class Process:
        def poll(self):
            return 0 if client.resolved is not None else None
    class Stop:
        def is_set(self):
            return False
        def wait(self, timeout):
            time.sleep(0.02)
            return False
    monkeypatch.setattr(web, 'GatewayClient', lambda: client)
    web._poll_gateway_questions('gateway-session', Process(), lambda kind, value: events.append((kind, value)), Stop(), 'autonomous')
    assert client.resolved == {'tone': ['专业']}
    question = json.loads(next(value for kind, value in events if kind == 'question'))
    assert question['defaultAnswers'] == {'tone': ['专业']}
    decision = next(value for kind, value in events if kind == 'agent_event')
    assert decision['source'] == 'default'
    assert decision['answers'] == {'tone': ['专业']}


def test_native_structured_events_survive_live_sse_replay_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    monkeypatch.setattr(web, '_RUNNING_CHAT', ActorMap())
    monkeypatch.setattr(web, '_session_locks', ActorMap())
    monkeypatch.setattr(web, '_chat_backend', lambda session: 'codex')
    monkeypatch.setattr(web, '_chat_message', lambda request: request.message)
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'read-only'})
    event = {'id': 'tool-result', 'kind': 'tool', 'callId': 'cmd', 'name': 'bash', 'status': 'completed',
             'input': {'command': 'pwd'}, 'output': 'workspace', 'exitCode': 0}
    plan = {'id': 'plan-update', 'kind': 'plan', 'turnId': 'native-turn', 'source': 'codex',
            'steps': [{'step': '检查素材', 'status': 'inProgress'}]}
    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('native-fixture')
            emit('agent_event', plan)
            emit('agent_event', event)
            emit('token', 'done')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    async def scenario():
        response = await web.api_chat_stream(web.ChatRequest(message='run', sessionId='structured-session', turnId='structured-turn'))
        received = [item async for item in response.body_iterator]
        assert [json.loads(item['data']) for item in received if item['event'] == 'agent_event'] == [plan, event]
        replay = await web.api_chat_job_stream('structured-turn')
        replayed = [item async for item in replay.body_iterator]
        assert [json.loads(item['data']) for item in replayed if item['event'] == 'agent_event'] == [plan, event]
        snapshot = await web.api_chat_last('structured-session', 'structured-turn')
        assert snapshot['text'] == 'done'
        assert snapshot['events'] == [plan, event]
    asyncio.run(scenario())


@pytest.mark.parametrize('label', ['发布', '发出去', '删除全部', '授权登录', '购买'])
def test_chinese_action_option_cannot_be_default_consent(label):
    from easel.agent_policy import question_policy
    policy = question_policy([{'questionId': 'next', 'question': '下一步？',
                              'options': [{'label': label, 'recommended': True}, {'label': '取消'}]}])
    assert policy.requires_human
    assert policy.default_answers == {}


def _native_chat_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    monkeypatch.setattr(web, '_RUNNING_CHAT', ActorMap())
    monkeypatch.setattr(web, '_session_locks', ActorMap())
    monkeypatch.setattr(web, '_chat_backend', lambda session: 'codex')
    monkeypatch.setattr(web, '_chat_message', lambda request: request.message)
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'read-only'})
    calls = []
    class Adapter:
        async def run(self, request, emit, ask, bind):
            calls.append(request.prompt)
            bind('native-fixture')
            await asyncio.sleep(0.02)
            emit('token', 'one-result')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    return calls


def test_two_posts_for_same_turn_execute_only_once_and_replay_identically(tmp_path, monkeypatch):
    calls = _native_chat_fixture(tmp_path, monkeypatch)
    async def scenario():
        request = web.ChatRequest(message='one-task', sessionId='queue-session', turnId='queue-item')
        first, second = await asyncio.gather(web.api_chat_stream(request), web.api_chat_stream(request))
        async def consume(response):
            return [(item['id'], item['event'], json.loads(item['data'])) async for item in response.body_iterator]
        live, repeated_post = await asyncio.gather(consume(first), consume(second))
        assert live == repeated_post
        assert live[0] == ('1', 'admitted', {'turnId': 'queue-item', 'sessionId': 'queue-session'})
        assert [item[2] for item in live if item[1] == 'token'] == ['one-result']
        assert await consume(await web.api_chat_job_stream('queue-item')) == live
        assert await consume(await web.api_chat_job_stream('queue-item', after=1)) == live[1:]
        assert calls == ['one-task']
    asyncio.run(scenario())


def test_invalid_chat_request_leaves_no_admission_or_execution(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from web.chat_history import ChatHistoryStore

    calls = _native_chat_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(web, '_chat_backend', lambda session: 'api')
    request = web.ChatRequest(message=' ', sessionId='queue-session', turnId='invalid-input')
    with pytest.raises(HTTPException) as error:
        asyncio.run(web.api_chat_stream(request))
    assert error.value.status_code == 400
    assert ChatHistoryStore(web._sessions_dir()).turn_claim('invalid-input') is None
    assert not web._job_event_file('invalid-input').exists()
    assert calls == []


def test_unwritable_admission_journal_never_starts_or_retries_the_turn(tmp_path, monkeypatch):
    from pathlib import Path
    from fastapi import HTTPException
    from web.chat_history import ChatHistoryStore

    calls = _native_chat_fixture(tmp_path, monkeypatch)
    path = web._job_event_file('journal-failure')
    original = Path.open

    def fail_admission(self, mode='r', *args, **kwargs):
        if self == path and mode == 'a':
            raise OSError('fixture journal write failure')
        return original(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail_admission)
    request = web.ChatRequest(message='one-task', sessionId='queue-session', turnId='journal-failure')
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            asyncio.run(web.api_chat_stream(request))
        assert error.value.status_code == 409
    assert ChatHistoryStore(web._sessions_dir()).turn_claim('journal-failure')['sessionId'] == 'queue-session'
    assert path.read_bytes() == b''
    assert calls == []


def test_turn_id_cannot_be_reused_for_different_payload(tmp_path, monkeypatch):
    from fastapi import HTTPException
    calls = _native_chat_fixture(tmp_path, monkeypatch)
    async def scenario():
        first = await web.api_chat_stream(web.ChatRequest(message='first', sessionId='session', turnId='same-id'))
        _ = [item async for item in first.body_iterator]
        with pytest.raises(HTTPException) as error:
            await web.api_chat_stream(web.ChatRequest(message='different', sessionId='session', turnId='same-id'))
        assert error.value.status_code == 409
        assert calls == ['first']
    asyncio.run(scenario())


def test_persisted_uncertain_claim_is_not_reexecuted_after_runtime_loss(tmp_path, monkeypatch):
    import hashlib
    from fastapi import HTTPException
    from web.chat_history import ChatHistoryStore
    calls = _native_chat_fixture(tmp_path, monkeypatch)
    request = web.ChatRequest(message='uncertain-action', sessionId='lost-session', turnId='lost-turn')
    payload_hash = hashlib.sha256(json.dumps({'request': request.model_dump(), 'runtime': 'codex'},
        sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    ChatHistoryStore(web._sessions_dir()).claim_turn('lost-turn', 'lost-session', payload_hash, web._job_event_file('lost-turn'))
    with pytest.raises(HTTPException) as error:
        asyncio.run(web.api_chat_stream(request))
    assert error.value.status_code == 409
    assert calls == []
