"""AI declaration is part of the approved subject, never an unchecked dispatch override."""
import asyncio
import copy
import hashlib
import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_publication_dispatch import dispatch_environment as _dispatch_environment, fake_runner
import web.app as web

dispatch_environment = _dispatch_environment
pytestmark = pytest.mark.access_integration
PAYLOAD = {'title': '猫咪上班日记', 'body': '先认真伸个懒腰。', 'tags': '猫咪,AI创作', 'persona': '', 'media': [], 'artifactIds': []}


def test_http_dto_has_a_strict_boolean_ai_declaration_with_false_as_legacy_default():
    assert 'aiGenerated' in web.PublishRequest.model_fields, 'Missing reviewed AI declaration'
    assert web.PublishRequest().aiGenerated is False
    assert web.PublishRequest(aiGenerated=True).aiGenerated is True
    for value in [None, 'true', 'false', 0, 1, [], {}]:
        with pytest.raises(ValidationError):
            web.PublishRequest(aiGenerated=value)


def test_ai_declaration_is_frozen_in_each_new_subject_and_payload_hash(dispatch_environment):
    root, actor, *_ = dispatch_environment

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            ai = await workflow.prepare('zhihu', {**PAYLOAD, 'aiGenerated': True})
            ordinary = await workflow.prepare('zhihu', PAYLOAD)
            assert ai['subject'].get('aiGenerated') is True, 'prepare dropped the AI declaration'
            assert ordinary['subject'].get('aiGenerated') is False
            assert ai['payloadHash'] != ordinary['payloadHash']
            expected = hashlib.sha256(json.dumps(ai['subject'], sort_keys=True,
                ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            assert ai['payloadHash'] == expected
            assert workflow.get(ai['id'])['subject']['aiGenerated'] is True
            for value in [None, 'true', 0, 1, [], {}]:
                with pytest.raises(HTTPException) as error:
                    await workflow.prepare('zhihu', {**PAYLOAD, 'aiGenerated': value})
                assert error.value.status_code == 400

    asyncio.run(scenario())


@pytest.mark.parametrize('initial,replacement', [(True, False), (False, True)])
def test_changing_ai_declaration_after_approval_invalidates_without_dispatch(dispatch_environment, monkeypatch, initial, replacement):
    root, actor, *_ = dispatch_environment
    sent = []

    async def dispatch(*args, **kwargs):
        sent.append(args)
        return {'status': 'submitted'}

    monkeypatch.setattr(web, '_dispatch_publish', dispatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            record = await workflow.prepare('zhihu', {**PAYLOAD, 'aiGenerated': initial})
            workflow.decide(record['id'], True, record['payloadHash'])
            with pytest.raises(HTTPException) as error:
                await workflow.start('zhihu', {**PAYLOAD, 'aiGenerated': replacement,
                    'approvalId': record['id'], 'payloadHash': record['payloadHash']})
            assert error.value.status_code == 409
            assert workflow.get(record['id'])['state'] == 'invalidated'
            assert sent == []

    asyncio.run(scenario())


def test_legacy_subject_missing_ai_field_keeps_its_original_hash_and_executes_as_false(dispatch_environment, monkeypatch):
    root, actor, *_ = dispatch_environment
    sent = []

    async def dispatch(platform, request, **kwargs):
        sent.append(request)
        return {'status': 'submitted'}

    monkeypatch.setattr(web, '_dispatch_publish', dispatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            fresh = await workflow.prepare('zhihu', PAYLOAD)
            legacy_subject = copy.deepcopy(fresh['subject'])
            legacy_subject.pop('aiGenerated', None)
            legacy = workflow.call('create', legacy_subject)
            original_hash = legacy['payloadHash']
            workflow.decide(legacy['id'], True, original_hash)
            await workflow.start('zhihu', {**PAYLOAD, 'aiGenerated': False,
                'approvalId': legacy['id'], 'payloadHash': original_hash})
            from web.publication_workflow import _TASKS
            await asyncio.gather(*list(_TASKS))
            final = workflow.get(legacy['id'])
            assert final['payloadHash'] == original_hash and final['subject'] == legacy_subject
            assert 'aiGenerated' not in final['subject']
            assert len(sent) == 1 and sent[0].aiGenerated is False

    asyncio.run(scenario())


@pytest.mark.parametrize('platform', ['douyin', 'xiaohongshu'])
@pytest.mark.parametrize('ai_generated', [None, False, True])
def test_exact_subject_transports_ai_declaration_to_platform_runner_argv_once(dispatch_environment, monkeypatch, platform, ai_generated):
    root, actor, _, _, video = dispatch_environment
    calls = fake_runner(monkeypatch, platform)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            payload = {**PAYLOAD, 'media': [video.relative_to(web.OUTPUTS_DIR).as_posix()]}
            if ai_generated is not None:
                payload['aiGenerated'] = ai_generated
            record = await workflow.prepare(platform, payload)
            workflow.decide(record['id'], True, record['payloadHash'])
            await workflow.start(platform, {**payload, 'approvalId': record['id'], 'payloadHash': record['payloadHash']})
            from web.publication_workflow import _TASKS
            await asyncio.gather(*list(_TASKS))
            return workflow.get(record['id'])

    result = asyncio.run(scenario())
    assert result['subject'].get('aiGenerated') is (ai_generated is True)
    assert len(calls) == 1
    assert calls[0]['command'].count('--ai-generated') == int(ai_generated is True)


def test_native_host_callback_executes_the_exact_ai_declaration_after_cross_page_approval(dispatch_environment, monkeypatch):
    from tests.test_publication_question_handoff import native_callback, opened, cancelled
    from easel.request_identity import ActorMap
    root, actor, *_ = dispatch_environment
    monkeypatch.setattr(web, '_HARNESS_QUESTIONS', ActorMap())
    monkeypatch.setattr(web, '_HARNESS_QUESTION_RESULTS', ActorMap())
    monkeypatch.setattr(web, '_PUBLICATION_QUESTION_POLL_SECONDS', .01)
    sent = []

    async def dispatch(platform, request, **kwargs):
        sent.append(request.aiGenerated)
        return {'status': 'submitted'}

    monkeypatch.setattr(web, '_dispatch_publish', dispatch)

    async def scenario():
        with actor_context(actor), project_context('default', root):
            workflow = web._publication_workflow()
            events = []
            task = asyncio.create_task(native_callback(monkeypatch, workflow, events)(
                {**PAYLOAD, 'platform': 'zhihu', 'aiGenerated': True}, 'ai-declaration-call'))
            try:
                question = await opened(events)
                approved = question['publication']
                assert approved['subject'].get('aiGenerated') is True
                await web.api_publication_decision(approved['id'], web.PublicationDecisionRequest(
                    approved=True, payloadHash=approved['payloadHash']))
                final = await asyncio.wait_for(task, 1.5)
                assert final['payloadHash'] == approved['payloadHash']
                assert final['subject']['aiGenerated'] is True and sent == [True]
            finally:
                await cancelled(task)

    asyncio.run(scenario())
