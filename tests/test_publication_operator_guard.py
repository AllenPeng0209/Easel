"""A temporary Host-owned flag pauses only three scoped direct HTTP entrypoints."""
import asyncio
import importlib
import json
import os

import pytest
from fastapi import HTTPException

from easel.projects import project_context, project_id
from easel.request_identity import actor_context
from tests.test_publication_dispatch import dispatch_environment as _dispatch_environment
from tests.test_publication_question_handoff import native_callback, opened, cancelled
import web.app as web

dispatch_environment = _dispatch_environment
pytestmark = pytest.mark.access_integration
PAYLOAD = {'title': 'Scoped operator test', 'body': 'Prepared exact body', 'media': [], 'artifactIds': []}


def guard_module():
    assert importlib.util.find_spec('web.publication_operator_guard') is not None, 'Missing scoped operator HTTP guard'
    return importlib.import_module('web.publication_operator_guard')


@pytest.fixture
def environment(dispatch_environment, monkeypatch):
    root, actor, access, *_ = dispatch_environment
    private_root = root.parent / (root.name + '-operator-http-state')
    monkeypatch.setattr(web, '_publication_dir', lambda: private_root / project_id() / 'publications')
    sent = []

    async def dispatch(platform, request, **kwargs):
        sent.append((platform, request.title))
        return {'status': 'submitted'}

    monkeypatch.setattr(web, '_dispatch_publish', dispatch)
    return root, actor, access, private_root, sent


def pause(private_root, actor, *, project='default', platform='zhihu', content=None, mode=0o600):
    directory = private_root / project / 'publications'
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    flag = directory / 'operator-http-pause.json'
    flag.write_text(content if content is not None else json.dumps({'version': 1, 'actorId': actor['id'], 'platform': platform}))
    flag.chmod(mode)
    return flag


def test_same_actor_ip_and_platform_three_http_writes_are_paused_without_any_operation(environment):
    root, actor, _, private_root, sent = environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            record = await workflow.prepare('zhihu', PAYLOAD)
            pause(private_root, actor)
            calls = [
                web.api_prepare_publication('zhihu', web.PublishRequest(**PAYLOAD)),
                web.api_publication_decision(record['id'], web.PublicationDecisionRequest(approved=True, payloadHash=record['payloadHash'])),
                web.api_publish('zhihu', web.PublishRequest(**PAYLOAD, approvalId=record['id'], payloadHash=record['payloadHash'])),
            ]
            for call in calls:
                with pytest.raises(HTTPException) as blocked:
                    await call
                assert blocked.value.status_code == 409 and '排查中' in str(blocked.value.detail)
            current = workflow.call('get', record['id'])
            assert current['state'] == 'pending' and current['operationId'] is None and sent == []

    asyncio.run(scenario())


def test_other_actor_platform_and_ip_are_not_paused_and_scope_auth_still_precedes_flag(environment):
    root, actor, access, private_root, _ = environment
    other = access.create_user('operator-other', 'temporary-operator-guard-password')
    access.set_member('default', other['id'], 'operator')
    access.set_member('other-ip', actor['id'], 'owner')
    (root / 'projects').mkdir(exist_ok=True)
    (root / 'projects/_registry.json').write_text(json.dumps([{'id': 'default', 'name': 'Default'}, {'id': 'other-ip', 'name': 'Other'}]))
    (root / 'projects/other-ip/outputs').mkdir(parents=True)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            pause(private_root, actor)
            with actor_context(other):
                record = await web.api_prepare_publication('zhihu', web.PublishRequest(**PAYLOAD))
                assert record['actorId'] == other['id']
            await web.api_prepare_publication('toutiao', web.PublishRequest(**PAYLOAD))
            with project_context('other-ip', root):
                record = await web.api_prepare_publication('zhihu', web.PublishRequest(**PAYLOAD))
                assert record['projectId'] == 'other-ip'
            with actor_context(None):
                with pytest.raises(HTTPException) as unauthorized:
                    await web.api_prepare_publication('zhihu', web.PublishRequest(**PAYLOAD))
                assert unauthorized.value.status_code == 401
            access.set_member('default', other['id'], 'viewer')
            with actor_context(other):
                with pytest.raises(HTTPException) as forbidden:
                    await web.api_prepare_publication('zhihu', web.PublishRequest(**PAYLOAD))
                assert forbidden.value.status_code == 403

    asyncio.run(scenario())


def test_decision_looks_up_only_owned_approval_before_matching_pause_platform(environment):
    root, actor, access, private_root, _ = environment
    other = access.create_user('decision-other', 'temporary-decision-guard-password')
    access.set_member('default', other['id'], 'operator')

    async def scenario():
        with actor_context(actor), project_context('default', root):
            record = await web._publication_workflow().prepare('zhihu', PAYLOAD)
            pause(private_root, other)
            with actor_context(other):
                with pytest.raises(HTTPException) as hidden:
                    await web.api_publication_decision(record['id'], web.PublicationDecisionRequest(
                        approved=True, payloadHash=record['payloadHash']))
                assert hidden.value.status_code == 404
            assert web._publication_workflow().call('get', record['id'])['state'] == 'pending'

    asyncio.run(scenario())


def test_missing_flag_and_readonly_routes_keep_original_behavior_while_native_callback_bypasses_http_pause(environment, monkeypatch):
    root, actor, _, private_root, sent = environment
    from easel.request_identity import ActorMap
    monkeypatch.setattr(web, '_HARNESS_QUESTIONS', ActorMap())
    monkeypatch.setattr(web, '_HARNESS_QUESTION_RESULTS', ActorMap())
    monkeypatch.setenv('EASEL_AGENT_APPROVAL_WAIT_SECONDS', '2')

    async def scenario():
        with actor_context(actor), project_context('default', root):
            direct = await web.api_prepare_publication('zhihu', web.PublishRequest(**PAYLOAD))
            decided = await web.api_publication_decision(direct['id'], web.PublicationDecisionRequest(
                approved=True, payloadHash=direct['payloadHash']))
            assert decided['state'] == 'approved'
            pause(private_root, actor)
            assert (await web.api_publication_approval(direct['id']))['id'] == direct['id']
            assert (await web.api_refresh_publication_approval(direct['id']))['id'] == direct['id']
            events = []
            workflow = web._publication_workflow()
            task = asyncio.create_task(native_callback(monkeypatch, workflow, events)(
                {**PAYLOAD, 'platform': 'zhihu'}, 'native-pause-bypass'))
            try:
                question = await opened(events)
                response = await web.api_question_answer(web.QuestionAnswerRequest(questionId=question['id'],
                    sessionId='cat-chat', turnId='cat-turn', answers={'publication': ['批准此版本并发布']}))
                assert response['ok'] and response['receipt']['resolvedBy'] == 'human'
                final = await asyncio.wait_for(task, 1.5)
                assert final['state'] == 'approved' and final['result']['status'] == 'submitted'
                assert len(sent) == 1
            finally:
                await cancelled(task)

    asyncio.run(scenario())


def test_pause_activated_during_http_account_check_is_rechecked_before_claim(environment, monkeypatch):
    root, actor, _, private_root, sent = environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            record = await workflow.prepare('zhihu', PAYLOAD)
            workflow.decide(record['id'], True, record['payloadHash'])
            original_account = web._publication_account

            async def account(platform):
                value = await original_account(platform)
                pause(private_root, actor)
                return value

            monkeypatch.setattr(web, '_publication_account', account)
            with pytest.raises(HTTPException) as blocked:
                await web.api_publish('zhihu', web.PublishRequest(**PAYLOAD,
                    approvalId=record['id'], payloadHash=record['payloadHash']))
            assert blocked.value.status_code == 409 and '排查中' in str(blocked.value.detail)
            assert workflow.call('get', record['id'])['operationId'] is None and sent == []

    asyncio.run(scenario())


@pytest.mark.parametrize('content,mode', [
    ('{', 0o600), ('null', 0o600), ('[]', 0o600),
    ('{"version":true,"actorId":"actor","platform":"douyin"}', 0o600),
    ('{"version":2,"actorId":"actor","platform":"douyin"}', 0o600),
    ('{"version":1,"actorId":"actor","platform":"douyin","extra":true}', 0o600),
    ('{"version":1,"version":1,"actorId":"actor","platform":"douyin"}', 0o600),
    ('{"version":1,"actorId":"actor","platform":"douyin"}', 0o644),
    ('{"version":1,"actorId":"actor","platform":"douyin"}', 0o400),
    (' ' * 4097, 0o600),
    ('[' * 1100 + ']' * 1100, 0o600),
])
def test_malformed_or_unsafe_flag_does_not_guess_a_target_or_globally_pause(tmp_path, content, mode):
    guard = guard_module()
    pause(tmp_path, {'id': 'actor'}, content=content, mode=mode)
    guard.require_operator_http_available(tmp_path / 'default/publications', actor_id='actor', platform='douyin')


def test_valid_private_flag_is_exact_scoped_and_missing_or_linked_sources_are_ignored(tmp_path):
    guard = guard_module()
    directory = tmp_path / 'default/publications'
    guard.require_operator_http_available(directory, actor_id='actor', platform='douyin')
    flag = pause(tmp_path, {'id': 'actor'}, platform='douyin')
    with pytest.raises(HTTPException) as paused:
        guard.require_operator_http_available(directory, actor_id='actor', platform='douyin')
    assert paused.value.status_code == 409
    guard.require_operator_http_available(directory, actor_id='other-actor', platform='douyin')
    guard.require_operator_http_available(directory, actor_id='actor', platform='zhihu')
    target = tmp_path / 'linked-source.json'
    flag.rename(target)
    flag.symlink_to(target)
    guard.require_operator_http_available(directory, actor_id='actor', platform='douyin')
    flag.unlink()
    os.link(target, flag)
    guard.require_operator_http_available(directory, actor_id='actor', platform='douyin')


def test_linked_parent_directory_cannot_supply_an_operator_pause(tmp_path):
    guard = guard_module()
    pause(tmp_path / 'real', {'id': 'actor'}, platform='douyin')
    linked = tmp_path / 'link'
    linked.symlink_to(tmp_path / 'real', target_is_directory=True)
    guard.require_operator_http_available(linked / 'default/publications', actor_id='actor', platform='douyin')
