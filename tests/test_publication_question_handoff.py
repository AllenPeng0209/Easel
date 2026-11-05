"""Exact Host approval decisions close their original chat question; no live platform is used."""
import ast
import asyncio
import copy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from easel.projects import project_context
from easel.request_identity import ActorMap, actor_context
from tests.test_publication_dispatch import dispatch_environment as _dispatch_environment
import web.app as web

pytestmark = pytest.mark.access_integration
dispatch_environment = _dispatch_environment

PAYLOAD = {'platform': 'zhihu', 'title': '猫咪今天也在认真上班', 'body': '先伸懒腰，再认真吃饭。',
           'tags': '猫咪,AI创作', 'persona': '', 'artifactIds': []}


@pytest.fixture
def environment(dispatch_environment, monkeypatch):
    monkeypatch.setattr(web, '_HARNESS_QUESTIONS', ActorMap())
    monkeypatch.setattr(web, '_HARNESS_QUESTION_RESULTS', ActorMap())
    monkeypatch.setenv('EASEL_AGENT_APPROVAL_WAIT_SECONDS', '2')
    monkeypatch.setattr(web, '_PUBLICATION_QUESTION_POLL_SECONDS', .01, raising=False)
    sent = []

    async def dispatch(platform, request, **kwargs):
        sent.append({'platform': platform, 'title': request.title, 'body': request.body, **kwargs})
        return {'status': 'submitted', 'message': 'Offline platform accepted fixture; public visibility is unverified'}

    monkeypatch.setattr(web, '_dispatch_publish', dispatch)
    return dispatch_environment, sent


def native_callback(monkeypatch, workflow, events):
    """Execute the actual nested native callback, with only its surrounding adapter faked."""
    tree = ast.parse(Path(web.__file__).read_text())
    node = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
                and node.name == 'publish' and [item.arg for item in node.args.args] == ['payload', 'call_id'])
    namespace = dict(vars(web), interactive=True, sk='cat-chat', turn_id='cat-turn',
        req=SimpleNamespace(autonomyMode='autonomous'),
        receive=lambda kind, payload: events.append((kind, payload)),
        publication_event=lambda record, **_: record)
    monkeypatch.setattr(web, '_publication_workflow', lambda: workflow)
    namespace['_publication_workflow'] = lambda: workflow
    exec(compile(ast.Module(body=[node], type_ignores=[]), web.__file__, 'exec'), namespace)
    return namespace['publish']


async def opened(events):
    for _ in range(100):
        values = [json.loads(value) for kind, value in events if kind == 'question']
        if values:
            return values[-1]
        await asyncio.sleep(.005)
    pytest.fail('Native publication did not expose its exact approval question')


async def cancelled(task):
    if not task.done():
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def closed(events):
    return [json.loads(value) for kind, value in events if kind == 'question_closed']


def test_publication_center_approval_closes_original_turn_and_concurrent_same_intent_dispatches_once(environment, monkeypatch):
    (root, actor, *_), sent = environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            events = []
            callback = native_callback(monkeypatch, workflow, events)
            task = asyncio.create_task(callback(dict(PAYLOAD), 'publish-call'))
            try:
                question = await opened(events)
                record = question['publication']
                decided = await web.api_publication_decision(record['id'], web.PublicationDecisionRequest(
                    approved=True, payloadHash=record['payloadHash']))
                assert decided['state'] == 'approved'
                subject = record['subject']
                page_result = await web.api_publish('zhihu', web.PublishRequest(
                    **{key: subject[key] for key in ('title', 'body', 'tags', 'persona')},
                    media=[], artifactIds=[], approvalId=record['id'], payloadHash=record['payloadHash']))
                await asyncio.sleep(.1)
                assert closed(events), 'The AI turn still waits for a second approval after the publishing center decided'
                result = await asyncio.wait_for(task, 1.5)
                assert result['id'] == record['id']
                assert result['payloadHash'] == record['payloadHash']
                assert result['operationId'] == page_result['operationId']
                assert result['result']['status'] == 'submitted'
                assert len(sent) == 1
                assert sent[0]['title'] == subject['title'] and sent[0]['body'] == subject['body']
                assert closed(events) == [{'id': question['id'], 'status': 'resolved',
                    'answers': {'publication': ['批准此版本并发布']}, 'resolvedBy': 'publication_host'}]
                assert question['id'] not in web._HARNESS_QUESTIONS
                decisions = [event for kind, event in events if kind == 'agent_event' and event.get('id') == 'decision_' + question['id']]
                assert len(decisions) == 1 and decisions[0]['publication']['id'] == record['id']
                assert decisions[0]['publication']['state'] == 'approved'
            finally:
                await cancelled(task)

    asyncio.run(scenario())


def test_publication_center_rejection_closes_original_question_without_preparing_or_dispatching_again(environment, monkeypatch):
    (root, actor, *_), sent = environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            events = []
            task = asyncio.create_task(native_callback(monkeypatch, workflow, events)(dict(PAYLOAD), 'reject-call'))
            try:
                question = await opened(events)
                record = question['publication']
                await web.api_publication_decision(record['id'], web.PublicationDecisionRequest(
                    approved=False, payloadHash=record['payloadHash']))
                await asyncio.sleep(.1)
                assert task.done(), 'A publishing-center rejection must close its original AI question'
                result = await task
                assert result['state'] == 'rejected' and result['id'] == record['id']
                assert result['operationId'] is None and result['result'] is None
                assert sent == []
                assert closed(events) == [{'id': question['id'], 'status': 'declined',
                    'answers': {'publication': ['暂不执行']}, 'resolvedBy': 'publication_host'}]
            finally:
                await cancelled(task)

    asyncio.run(scenario())


@pytest.mark.parametrize('changed', ['actorId', 'projectId', 'id', 'payloadHash'])
def test_observer_never_resolves_a_question_with_foreign_or_rebound_host_record(environment, changed):
    assert 'publication_observer' in inspect.signature(web._harness_ask).parameters, 'Missing exact Host publication observer'
    (root, actor, *_), sent = environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            record = await workflow.prepare('zhihu', PAYLOAD)
            forged = {**copy.deepcopy(record), 'state': 'approved', changed: 'f' * (64 if changed == 'payloadHash' else 32)}
            events = []
            task = asyncio.create_task(web._harness_ask('chat', [{
                'questionId': 'publication', 'kind': 'approval', 'question': '核对此固定版本？',
                'options': [{'label': '批准此版本并发布'}, {'label': '暂不执行'}],
            }], lambda kind, value: events.append((kind, value)), publication=record,
                publication_observer=lambda _: forged, turn_id='turn'))
            try:
                await opened(events)
                await asyncio.sleep(.05)
                assert not task.done() and not closed(events)
                assert sent == []
            finally:
                await cancelled(task)
            assert closed(events)[0]['status'] == 'cancelled'
            assert not [task for task in asyncio.all_tasks() if not task.done() and task.get_name().startswith('publication-question:')]

    asyncio.run(scenario())


def test_unrelated_publication_and_generic_human_question_are_not_auto_answered(environment):
    (root, actor, *_), sent = environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            events = []
            task = asyncio.create_task(web._harness_ask('chat', [{
                'questionId': 'permission', 'kind': 'approval', 'question': '另一个授权问题？',
                'options': [{'label': '批准'}, {'label': '拒绝'}],
            }], lambda kind, value: events.append((kind, value)), turn_id='turn'))
            try:
                question = await opened(events)
                workflow = web._publication_workflow()
                other = await workflow.prepare('zhihu', PAYLOAD)
                await web.api_publication_decision(other['id'], web.PublicationDecisionRequest(
                    approved=True, payloadHash=other['payloadHash']))
                await asyncio.sleep(.05)
                assert not task.done() and not closed(events)
                answer = await web.api_question_answer(web.QuestionAnswerRequest(questionId=question['id'],
                    sessionId='chat', turnId='turn', answers={'permission': ['拒绝']}))
                assert answer['ok']
                assert await task == {'permission': ['拒绝']}
                assert closed(events)[0]['resolvedBy'] == 'human'
                assert sent == []
            finally:
                await cancelled(task)

    asyncio.run(scenario())


def test_an_already_consumed_publication_is_observed_without_another_start_or_decision(environment, monkeypatch):
    (root, actor, *_), sent = environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            original_get, original_start, original_decide = workflow.get, workflow.start, workflow.decide
            release = False
            pending = None
            starts, decisions = [], []

            def read(identifier):
                return original_get(identifier) if release or pending is None else copy.deepcopy(pending)

            async def start(platform, payload, **kwargs):
                starts.append(payload['approvalId'])
                return await original_start(platform, payload, **kwargs)

            def decide(identifier, approved, payload_hash):
                decisions.append(identifier)
                return original_decide(identifier, approved, payload_hash)

            workflow.get, workflow.start, workflow.decide = read, start, decide
            events = []
            task = asyncio.create_task(native_callback(monkeypatch, workflow, events)(dict(PAYLOAD), 'consumed-call'))
            try:
                question = await opened(events)
                pending = question['publication']
                await web.api_publication_decision(pending['id'], web.PublicationDecisionRequest(
                    approved=True, payloadHash=pending['payloadHash']))
                subject = pending['subject']
                await web.api_publish('zhihu', web.PublishRequest(
                    **{key: subject[key] for key in ('title', 'body', 'tags', 'persona')},
                    media=[], artifactIds=[], approvalId=pending['id'], payloadHash=pending['payloadHash']))
                from web.publication_workflow import _TASKS
                await asyncio.gather(*list(_TASKS))
                release = True
                result = await asyncio.wait_for(task, .5)
                assert result['result']['status'] == 'submitted'
                assert starts == decisions == [pending['id']]
                assert len(sent) == 1
                assert closed(events)[0]['resolvedBy'] == 'publication_host'
            finally:
                await cancelled(task)

    asyncio.run(scenario())


@pytest.mark.parametrize('terminal', ['expired', 'invalidated'])
def test_host_expiration_or_invalidated_hash_closes_only_the_bound_publication_without_dispatch(environment, monkeypatch, terminal):
    (root, actor, *_), sent = environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            events = []
            task = asyncio.create_task(native_callback(monkeypatch, workflow, events)(dict(PAYLOAD), terminal + '-call'))
            try:
                question = await opened(events)
                record = question['publication']
                if terminal == 'expired':
                    import easel.publication_approvals as approvals
                    monkeypatch.setattr(approvals, '_now_ms', lambda: record['expiresAt'] + 1)
                else:
                    with pytest.raises(HTTPException) as invalid:
                        await web.api_publication_decision(record['id'], web.PublicationDecisionRequest(
                            approved=True, payloadHash='f' * 64))
                    assert invalid.value.status_code == 409
                result = await asyncio.wait_for(task, .5)
                assert result['state'] == terminal
                assert result['id'] == record['id'] and result['operationId'] is None and sent == []
                assert closed(events)[0]['status'] == 'declined'
                assert closed(events)[0]['resolvedBy'] == 'publication_host'
            finally:
                await cancelled(task)

    asyncio.run(scenario())


def test_question_timeout_still_declines_and_releases_its_publication_observer(environment, monkeypatch):
    (root, actor, *_), sent = environment
    monkeypatch.setenv('EASEL_AGENT_APPROVAL_WAIT_SECONDS', '.03')

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            events = []
            task = asyncio.create_task(native_callback(monkeypatch, workflow, events)(dict(PAYLOAD), 'timeout-call'))
            try:
                question = await opened(events)
                result = await asyncio.wait_for(task, .5)
                assert result['state'] == 'rejected' and result['id'] == question['publication']['id']
                assert sent == []
                assert closed(events)[0]['status'] == 'declined' and closed(events)[0]['resolvedBy'] == 'timeout'
                assert not [task for task in asyncio.all_tasks() if not task.done() and task.get_name().startswith('publication-question:')]
            finally:
                await cancelled(task)

    asyncio.run(scenario())


def test_permission_revocation_during_cross_page_wait_cannot_consume_a_previous_approval(environment, monkeypatch):
    (root, actor, access, *_), sent = environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            events = []
            task = asyncio.create_task(native_callback(monkeypatch, workflow, events)(dict(PAYLOAD), 'revocation-call'))
            try:
                await opened(events)
                access.set_member('default', actor['id'], 'viewer')
                with pytest.raises(web.HarnessError):
                    await asyncio.wait_for(task, .5)
                assert sent == [] and closed(events)[0]['status'] == 'revoked'
                assert not [task for task in asyncio.all_tasks() if not task.done() and task.get_name().startswith('publication-question:')]
            finally:
                await cancelled(task)

    asyncio.run(scenario())
