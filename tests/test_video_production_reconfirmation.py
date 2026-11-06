"""Expired unused plans need a new human decision; no scope or intent is reset."""
import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from easel.video_production_plans import VideoProductionPlanError
from easel.video_workshop import WorkshopError
from tests.test_video_production_plans import approved, plan, store
from tests.native_video_fixtures import setup as generation_setup


@pytest.fixture
def expired_plan(tmp_path):
    clock = [1000]
    value = store(tmp_path, clock=lambda: clock[0])
    original = approved(value)
    clock[0] = original['expiresAt'] + 10
    assert value.get(original['planId'])['state'] == 'expired'
    return value, original, clock


def reconfirm(value, record, approved=True):
    return value.reconfirm(record['planId'], record['scopeHash'], record['expiresAt'], approved,
                           workshop_id=record['workshopId'])


def test_expired_zero_intent_plan_reconfirmation_preserves_scope_and_audits_old_decision(expired_plan):
    value, original, clock = expired_plan
    restored = reconfirm(value, original)
    assert restored['state'] == 'approved'
    assert restored['expiresAt'] == clock[0] + 900000
    assert restored['decidedAt'] == clock[0]
    for key in ('planId', 'scopeHash', 'actorId', 'projectId', 'sessionId', 'workshopId',
                'title', 'modelId', 'aspectRatio', 'resolution', 'clips', 'maxGenerations', 'totalSeconds', 'createdAt'):
        assert restored[key] == original[key]
    assert restored['clipClaims'] == []
    assert restored['reconfirmations'] == [{'scopeHash': original['scopeHash'],
        'previousExpiresAt': original['expiresAt'], 'previousDecidedAt': original['decidedAt'],
        'decidedAt': clock[0], 'decision': 'approved', 'expiresAt': restored['expiresAt']}]
    assert value.claim_clip(original['planId'], 'story-01', 'one')['claimed'] is True
    with pytest.raises(VideoProductionPlanError):
        value.claim_clip(original['planId'], 'story-01', 'new-one')


def test_reconfirmation_preserves_original_ttl_across_multiple_unused_epochs(expired_plan):
    value, original, clock = expired_plan
    first = reconfirm(value, original)
    clock[0] = first['expiresAt'] + 500
    second = reconfirm(value, first)
    assert second['expiresAt'] == clock[0] + 900000
    assert second['reconfirmations'][0] == first['reconfirmations'][0]
    assert len(second['reconfirmations']) == 2
    assert second['maxGenerations'] == original['maxGenerations']


@pytest.mark.parametrize('answer', [True, False])
def test_old_reconfirmation_epoch_cannot_repeat_or_override_successor(expired_plan, answer):
    value, original, clock = expired_plan
    restored = reconfirm(value, original)
    clock[0] += 100
    with pytest.raises(VideoProductionPlanError) as exc:
        reconfirm(value, original, answer)
    assert exc.value.status_code == 409
    assert value.get(original['planId']) == restored
    clock[0] = restored['expiresAt'] + 1
    with pytest.raises(VideoProductionPlanError):
        reconfirm(value, original, answer)
    assert value.get(original['planId'])['reconfirmations'] == restored['reconfirmations']


def test_declined_reconfirmation_is_final_and_keeps_old_expiry(expired_plan):
    value, original, _ = expired_plan
    declined = reconfirm(value, original, False)
    assert declined['state'] == 'declined'
    assert declined['expiresAt'] == original['expiresAt']
    assert declined['reconfirmations'][0]['decision'] == 'declined'
    with pytest.raises(VideoProductionPlanError):
        reconfirm(value, original)
    assert value.get(original['planId']) == declined


@pytest.mark.parametrize('state', ['pending', 'approved', 'declined', 'started'])
def test_reconfirmation_rejects_nonexpired_or_started_unknown_intent(tmp_path, state):
    clock = [1000]
    value = store(tmp_path, clock=lambda: clock[0])
    record = value.prepare('1' * 32, plan())
    if state != 'pending':
        record = value.decide(record['planId'], record['scopeHash'], state != 'declined')
    if state == 'started':
        value.claim_clip(record['planId'], 'story-01', 'unknown-original-request')
        clock[0] = record['expiresAt'] + 1000
    before = value.get(record['planId'])
    with pytest.raises(VideoProductionPlanError):
        reconfirm(value, record)
    assert value.get(record['planId']) == before


@pytest.mark.parametrize('foreign', [{'actor': 'someone'}, {'ip': 'other-ip'}, {'session': 'other-chat'}])
def test_reconfirmation_cannot_cross_actor_ip_or_session(tmp_path, expired_plan, foreign):
    value, original, clock = expired_plan
    other = store(tmp_path, clock=lambda: clock[0], **foreign)
    with pytest.raises(VideoProductionPlanError) as exc:
        reconfirm(other, original)
    assert exc.value.status_code == 404
    assert value.get(original['planId'])['state'] == 'expired'


@pytest.mark.parametrize('change', [{'scopeHash': 'f' * 64}, {'expiresAt': 123}, {'workshopId': 'f' * 32}])
def test_reconfirmation_requires_original_scope_workshop_and_expiry(expired_plan, change):
    value, original, _ = expired_plan
    with pytest.raises(VideoProductionPlanError):
        reconfirm(value, {**original, **change})
    assert value.get(original['planId'])['state'] == 'expired'


def test_reconfirmation_rejects_changed_private_scope(expired_plan):
    value, original, _ = expired_plan
    with sqlite3.connect(value.path) as db:
        raw = json.loads(db.execute('SELECT data FROM video_production_plans WHERE id=?', (original['planId'],)).fetchone()[0])
        raw['clips'][0]['prompt'] += ' changed'
        db.execute('UPDATE video_production_plans SET data=? WHERE id=?', (json.dumps(raw), original['planId']))
    with pytest.raises(VideoProductionPlanError) as exc:
        reconfirm(value, original)
    assert exc.value.status_code == 503


def test_reconfirmation_refuses_conflicting_approved_plan_in_another_chat(tmp_path, expired_plan):
    value, original, clock = expired_plan
    other = store(tmp_path, session='other-chat', clock=lambda: clock[0])
    conflict = approved(other)
    with pytest.raises(VideoProductionPlanError) as exc:
        reconfirm(value, original)
    assert exc.value.status_code == 409
    assert other.get(conflict['planId'])['state'] == 'approved'
    assert value.get(original['planId'])['state'] == 'expired'


def test_simultaneous_decisions_settle_only_one_expired_epoch(tmp_path, expired_plan):
    value, original, clock = expired_plan
    barrier = threading.Barrier(2)
    def answer(accepted):
        connection = store(tmp_path, clock=lambda: clock[0])
        barrier.wait(timeout=5)
        try:
            return reconfirm(connection, original, accepted)['state']
        except VideoProductionPlanError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(answer, [True, False]))
    assert len([result for result in results if result is not None]) == 1
    result = value.get(original['planId'])
    assert len(result['reconfirmations']) == 1
    assert result['state'] in {'approved', 'declined'}


@pytest.fixture
def host_reconfirmation(generation_setup, monkeypatch):
    from PIL import Image
    from web import video_generation as bridge
    _, service, backend, outputs, project, payload, _ = generation_setup
    folder = outputs / 'film'; folder.mkdir()
    Image.new('RGB', (32, 32), 'red').save(folder / 'reference.png')
    (folder / 'production.json').write_text(json.dumps({'version': 1, 'title': '原范围恢复',
        **{key: payload[key] for key in ('modelId', 'aspectRatio', 'resolution')},
        'clips': [{'clipId': 'one', 'seconds': 5, 'prompt': 'Listen first.',
                   'references': [{'kind': 'image', 'path': 'film/reference.png'}]}]}))
    clock = [1000]
    service.plan_store.now_ms = lambda: clock[0]
    original = service.run({'action': 'plan', 'workshopId': project['id'], 'manifestPath': 'film/production.json'})
    original = service.plan_decision(project['id'], original['planId'], original['scopeHash'], True)
    clock[0] = original['expiresAt'] + 10
    request = {'action': 'approve_plan', 'workshopId': project['id'], 'planId': original['planId']}
    monkeypatch.setattr(bridge, '_service', lambda **scope: service)
    return service, backend, outputs, original, request, clock


@pytest.mark.parametrize('answers', [{}, None, {'video-production': ['暂不生成']},
                                    {'video-production': ['批准此方案生成']}])
def test_host_asks_fresh_human_question_before_reconfirming_original_scope(host_reconfirmation, answers):
    from web.video_generation import approve_video_production_plan
    service, backend, _, original, request, clock = host_reconfirmation
    questions = []
    async def ask(question):
        questions.append(question)
        assert service.plan_store.get(original['planId'])['state'] == 'expired'
        assert backend.submitted == []
        return answers
    result = asyncio.run(approve_video_production_plan(request, ask=ask, authorize=lambda: None))
    assert len(questions) == 1 and questions[0]['requiresHuman'] is True
    assert result['state'] == ('approved' if answers == {'video-production': ['批准此方案生成']} else 'declined')
    assert result['scopeHash'] == original['scopeHash']
    assert result['planId'] == original['planId']
    assert backend.submitted == []


@pytest.mark.parametrize('state', ['approved', 'declined', 'started'])
def test_host_does_not_ask_again_for_valid_declined_or_started_plan(host_reconfirmation, state):
    from web.video_generation import approve_video_production_plan
    service, _, _, original, request, clock = host_reconfirmation
    clock[0] = original['createdAt'] + 1
    if state == 'declined':
        workshop = service.store.create({'title': '拒绝的计划'})
        replacement = plan(); replacement['clips'][0]['references'] = []
        record = service.plan_store.prepare(workshop['id'], replacement)
        service.plan_store.decide(record['planId'], record['scopeHash'], False)
        request = {**request, 'planId': record['planId'], 'workshopId': workshop['id']}
    if state == 'started':
        service.plan_store.claim_clip(original['planId'], 'one', 'unknown')
        clock[0] = original['expiresAt'] + 1000
    async def ask(_):
        pytest.fail('An effective or consumed plan must not open another approval card')
    result = asyncio.run(approve_video_production_plan(request, ask=ask, authorize=lambda: None))
    assert result['state'] == ('declined' if state == 'declined' else 'approved')
    assert 'reconfirmations' not in result


def test_host_revalidates_references_after_human_answer(host_reconfirmation):
    from PIL import Image
    from web.video_generation import approve_video_production_plan
    service, backend, outputs, original, request, _ = host_reconfirmation
    async def ask(_):
        Image.new('RGB', (32, 32), 'blue').save(outputs / 'film/reference.png')
        return {'video-production': ['批准此方案生成']}
    with pytest.raises(WorkshopError) as exc:
        asyncio.run(approve_video_production_plan(request, ask=ask, authorize=lambda: None))
    assert exc.value.status == 409
    assert service.plan_store.get(original['planId'])['state'] == 'expired'
    assert backend.submitted == []


def test_host_revalidates_current_turn_after_human_answer(host_reconfirmation):
    from web.video_generation import approve_video_production_plan
    service, _, _, original, request, _ = host_reconfirmation
    allowed = [True]
    def authorize():
        if not allowed[0]:
            raise PermissionError('turn revoked')
    async def ask(_):
        allowed[0] = False
        return {'video-production': ['批准此方案生成']}
    with pytest.raises(PermissionError):
        asyncio.run(approve_video_production_plan(request, ask=ask, authorize=authorize))
    assert service.plan_store.get(original['planId'])['state'] == 'expired'
