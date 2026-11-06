"""The actual native Host waits for the current-turn human production decision."""
import json
import httpx
import pytest
from easel.video_provider import NativeVideoClient
from easel.video_workshop import WorkshopStore
from tests.test_chat_artifacts import artifact_http_environment
pytestmark = pytest.mark.access_integration

@pytest.mark.access_integration
@pytest.mark.parametrize('expired_reconfirmation', [False, True])
@pytest.mark.parametrize(('manual_answer', 'resolved_by'), [
    (True, None), (False, None), (True, 'user'), (True, 'human'),
    (True, 'default'), (True, 'timeout'), (True, 'model'), (True, 'publication_host'),
])
def test_actual_native_host_callback_requires_current_turn_human_answer(
        artifact_http_environment, monkeypatch, manual_answer, resolved_by, expired_reconfirmation):
    import ast
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace
    import web.app as web
    from web import video_generation as bridge
    from easel.projects import project_context
    from easel.request_identity import ActorMap, actor_context
    root, owner, _, _, _ = artifact_http_environment
    actor = owner.get('/api/auth/me').json()
    submitted = []
    workshop = WorkshopStore(root / 'outputs').create(
        {'title': '人工批准多片计划', 'aspect_ratio': '9:16'})
    directory = root / 'outputs' / 'family-case'; directory.mkdir()
    (directory / 'production.json').write_text(json.dumps({'version': 1, 'title': '完整母子剧情',
        'modelId': 'easel-ppu-h3-local::minimax-h3-dense8', 'aspectRatio': '9:16', 'resolution': '720p',
        'clips': [{'clipId': 'one', 'seconds': 2, 'prompt': 'A genuine new dramatic performance.', 'references': []}]}))
    client = NativeVideoClient(api_key='test-only', transport=httpx.MockTransport(lambda request: pytest.fail('Approval must not submit generation')))
    monkeypatch.setattr(bridge, 'NativeVideoClient', lambda **kwargs: client)
    private = root.parent / (root.name + '-production-private')
    monkeypatch.setattr(bridge, 'generation_state_directory', lambda *args: private / 'video-generations')
    clock = [1000]
    if expired_reconfirmation:
        import easel.video_production_plans as plans
        original_store = plans.VideoProductionPlanStore
        monkeypatch.setattr(plans, 'VideoProductionPlanStore',
            lambda *args, **kwargs: original_store(*args, **kwargs, now_ms=lambda: clock[0]))
    monkeypatch.setattr(web, '_HARNESS_QUESTIONS', ActorMap())
    monkeypatch.setattr(web, '_HARNESS_QUESTION_RESULTS', ActorMap())
    monkeypatch.setenv('EASEL_AGENT_APPROVAL_WAIT_SECONDS', '2' if manual_answer else '.02')
    node = next(node for node in ast.walk(ast.parse(Path(web.__file__).read_text()))
                if isinstance(node, ast.AsyncFunctionDef) and node.name == 'video_generation'
                and [arg.arg for arg in node.args.args] == ['payload', 'call_id'])
    events = []
    async def observe(*args): return {'items': []}
    namespace = dict(vars(web), interactive=True, sk='production-chat', turn_id='production-turn',
        req=SimpleNamespace(autonomyMode='autonomous'), business_objects=observe,
        receive=lambda kind, value: events.append((kind, value)))
    exec(compile(ast.Module(body=[node], type_ignores=[]), web.__file__, 'exec'), namespace)
    callback = namespace['video_generation']
    async def scenario():
        with actor_context(actor), project_context('default', root):
            plan = await callback({'action': 'plan', 'workshopId': workshop['id'],
                                   'manifestPath': 'family-case/production.json'}, 'plan-call')
            if expired_reconfirmation:
                service = bridge._service(repository=web.PROJECT_ROOT, workspace=root, outputs=root / 'outputs',
                    actor_id=actor['id'], ip='default', session_id='production-chat', authorize=lambda: None)
                service.plan_decision(workshop['id'], plan['planId'], plan['scopeHash'], True)
                clock[0] = plan['expiresAt'] + 10
            task = asyncio.create_task(callback({'action': 'approve_plan', 'workshopId': workshop['id'],
                                                  'planId': plan['planId']}, 'approval-call'))
            try:
                for _ in range(100):
                    questions = [json.loads(value) for kind, value in events if kind == 'question']
                    if questions: break
                    await asyncio.sleep(.005)
                assert questions, 'The actual Host must expose a concrete production approval question'
                question = questions[-1]
                assert question['requiresHuman'] is True and question['defaultAnswers'] == {}
                assert submitted == []
                if manual_answer:
                    original = web._HARNESS_QUESTIONS[question['id']]
                    deadline = original['deadline']
                    answer = await web.api_question_answer(web.QuestionAnswerRequest(
                        questionId=question['id'], sessionId='production-chat', turnId='production-turn',
                        answers={'video-production': ['批准此方案生成']}, resolvedBy=resolved_by))
                    if resolved_by not in (None, 'user', 'human'):
                        assert answer['ok'] is False and answer['status'] == 'pending'
                        assert web._HARNESS_QUESTIONS[question['id']] is original
                        assert original['deadline'] == deadline and not original['future'].done()
                        assert not task.done() and submitted == []
                        current = bridge.run_video_generation(
                            {'action': 'plan_status', 'workshopId': workshop['id'], 'planId': plan['planId']},
                            repository=web.PROJECT_ROOT, workspace=root, outputs=root / 'outputs',
                            actor_id=actor['id'], ip='default', session_id='production-chat', authorize=lambda: None)
                        assert current['state'] == ('expired' if expired_reconfirmation else 'pending')
                        assert current['clipClaims'] == []
                        # A rejected automatic answer must leave the original
                        # question available to its real current-turn user.
                        answer = await web.api_question_answer(web.QuestionAnswerRequest(
                            questionId=question['id'], sessionId='production-chat', turnId='production-turn',
                            answers={'video-production': ['批准此方案生成']}, resolvedBy='human'))
                    assert answer['ok'] is True
                result = await asyncio.wait_for(task, 2)
                assert result['state'] == ('approved' if manual_answer else 'declined')
                if expired_reconfirmation:
                    assert result['scopeHash'] == plan['scopeHash']
                    assert len(result['reconfirmations']) == 1
                    if manual_answer:
                        assert result['expiresAt'] == clock[0] + 900000
                assert submitted == []
            finally:
                if not task.done():
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(scenario())
