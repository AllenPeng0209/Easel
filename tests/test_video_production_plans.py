"""Host-private, human-approved bounded video production plans."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import sqlite3
import threading

import pytest


def module():
    return importlib.import_module('easel.video_production_plans')


def plan():
    return {
        'version': 1, 'title': '先听孩子说完',
        'modelId': 'h3-ref11::minimax-h3-ref2va-ref11-8step',
        'aspectRatio': '9:16', 'resolution': '720p',
        'clips': [
            {'clipId': 'story-01', 'seconds': 12, 'prompt': 'First original mother and child scene.',
             'references': [{'kind': 'image', 'path': 'new-film/assets/mother-son.png',
                             'sha256': 'a' * 64, 'bytes': 2048, 'width': 768, 'height': 1344,
                             'mimeType': 'image/png'}]},
            {'clipId': 'story-02', 'seconds': 13, 'prompt': 'The child provides new information.',
             'references': []},
            {'clipId': 'story-03', 'seconds': 13, 'prompt': 'The mother makes one small change.',
             'references': []},
            {'clipId': 'close-01', 'seconds': 7, 'prompt': 'The mother reflects and asks a question.',
             'references': []},
        ],
    }


def store(tmp_path, *, actor='owner', ip='laobai', session='host-chat', clock=None, authorize=None):
    workspace = tmp_path / 'repository' / 'projects' / 'laobai'
    workspace.mkdir(parents=True, exist_ok=True)
    return module().VideoProductionPlanStore(
        tmp_path / 'host-state', workspace=workspace, repository=tmp_path / 'repository',
        actor_id=actor, ip=ip, session_id=session,
        now_ms=clock or (lambda: 1000), authorize=authorize or (lambda: None))


def approved(value):
    record = value.prepare('1' * 32, plan())
    return value.decide(record['planId'], record['scopeHash'], True)


def test_frozen_plan_requires_an_exact_manual_decision_before_any_clip_intent(tmp_path):
    value = store(tmp_path)
    source = plan()
    record = value.prepare('1' * 32, source)
    assert record['state'] == 'pending'
    assert record['maxGenerations'] == 4 and record['totalSeconds'] == 45
    assert record['estimatedCost'] == 'unknown' and record['clipClaims'] == []
    assert record['actorId'] == 'owner' and record['projectId'] == 'laobai'
    assert record['sessionId'] == 'host-chat' and record['workshopId'] == '1' * 32
    with pytest.raises(module().VideoProductionPlanError):
        value.claim_clip(record['planId'], 'story-01', 'original-op')
    source['clips'][0]['prompt'] = 'Changed after preparing.'
    source['clips'][0]['references'][0]['sha256'] = 'b' * 64
    assert value.get(record['planId'])['clips'][0]['prompt'] == 'First original mother and child scene.'
    granted = value.decide(record['planId'], record['scopeHash'], True)
    assert granted['state'] == 'approved'
    clip = value.approved_clip(record['planId'], 'story-01')
    assert clip['seconds'] == 12 and clip['modelId'] == 'h3-ref11::minimax-h3-ref2va-ref11-8step'
    assert clip['references'][0]['sha256'] == 'a' * 64


def test_each_fixed_clip_is_admitted_once_across_restarts_and_unknown_results(tmp_path):
    value = store(tmp_path)
    record = approved(value)
    first = value.claim_clip(record['planId'], 'story-01', 'easel-video:original')
    assert first['claimed'] is True and first['operationId'] == 'easel-video:original'
    restarted = store(tmp_path)
    replay = restarted.claim_clip(record['planId'], 'story-01', 'easel-video:original')
    assert replay['claimed'] is False and replay['operationId'] == first['operationId']
    with pytest.raises(module().VideoProductionPlanError):
        restarted.claim_clip(record['planId'], 'story-01', 'easel-video:new-purchase')
    for clip, operation in [('story-02', 'second'), ('story-03', 'third'), ('close-01', 'fourth')]:
        assert restarted.claim_clip(record['planId'], clip, operation)['claimed'] is True
    with pytest.raises(module().VideoProductionPlanError):
        restarted.claim_clip(record['planId'], 'unplanned-fifth', 'fifth')
    result = restarted.get(record['planId'])
    assert len(result['clipClaims']) == 4
    assert result['maxGenerations'] == 4
    assert [item['clipId'] for item in result['clipClaims']] == ['story-01', 'story-02', 'story-03', 'close-01']


def test_concurrent_independent_connections_admit_only_one_original_clip(tmp_path):
    value = store(tmp_path)
    record = approved(value)
    barrier = threading.Barrier(2)
    def claim(_):
        other = store(tmp_path)
        barrier.wait(timeout=5)
        return other.claim_clip(record['planId'], 'story-01', 'stable-operation')['claimed']
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]
    assert len(value.get(record['planId'])['clipClaims']) == 1


@pytest.mark.parametrize('scope', [{'actor': 'other'}, {'ip': 'other-ip'}, {'session': 'other-chat'}])
@pytest.mark.parametrize('method', ['get', 'decide', 'approved_clip', 'claim_clip'])
def test_another_actor_ip_or_host_session_cannot_use_the_plan(tmp_path, scope, method):
    value = store(tmp_path)
    record = approved(value)
    foreign = store(tmp_path, **scope)
    with pytest.raises(module().VideoProductionPlanError) as caught:
        if method == 'get':
            foreign.get(record['planId'])
        elif method == 'decide':
            foreign.decide(record['planId'], record['scopeHash'], True)
        elif method == 'approved_clip':
            foreign.approved_clip(record['planId'], 'story-01')
        else:
            foreign.claim_clip(record['planId'], 'story-01', 'operation')
    assert caught.value.status_code == 404


def test_same_canonical_scope_is_idempotent_but_changed_prompt_or_workshop_is_not_rebound(tmp_path):
    value = store(tmp_path)
    original = value.prepare('1' * 32, plan())
    reordered = dict(reversed(list(plan().items())))
    assert value.prepare('1' * 32, reordered)['planId'] == original['planId']
    changed = plan(); changed['clips'][0]['prompt'] += ' New direction.'
    replacement = value.prepare('1' * 32, changed)
    different_workshop = value.prepare('2' * 32, plan())
    assert len({original['planId'], replacement['planId'], different_workshop['planId']}) == 3
    assert value.get(original['planId'])['clips'][0]['prompt'] == 'First original mother and child scene.'
    with pytest.raises(module().VideoProductionPlanError):
        value.decide(original['planId'], replacement['scopeHash'], True)
    assert value.get(original['planId'])['state'] == 'pending'


def test_decline_is_idempotent_and_cannot_turn_into_approval(tmp_path):
    value = store(tmp_path)
    record = value.prepare('1' * 32, plan())
    assert value.decide(record['planId'], record['scopeHash'], False)['state'] == 'declined'
    assert value.decide(record['planId'], record['scopeHash'], False)['state'] == 'declined'
    with pytest.raises(module().VideoProductionPlanError):
        value.decide(record['planId'], record['scopeHash'], True)
    with pytest.raises(module().VideoProductionPlanError):
        value.approved_clip(record['planId'], 'story-01')


def test_unstarted_plan_expires_but_started_fixed_batch_can_finish_after_waiting(tmp_path):
    clock = [1000]
    value = store(tmp_path, clock=lambda: clock[0])
    stale = value.prepare('1' * 32, plan())
    clock[0] = stale['expiresAt']
    assert value.get(stale['planId'])['state'] == 'expired'
    with pytest.raises(module().VideoProductionPlanError):
        value.decide(stale['planId'], stale['scopeHash'], True)
    clock[0] += 1
    running = value.prepare('2' * 32, plan())
    value.decide(running['planId'], running['scopeHash'], True)
    value.claim_clip(running['planId'], 'story-01', 'one')
    clock[0] = running['expiresAt'] + 3600000
    assert value.get(running['planId'])['state'] == 'approved'
    assert value.claim_clip(running['planId'], 'story-02', 'two')['claimed'] is True


@pytest.mark.parametrize('change', [
    lambda p: p.update(clips=[]),
    lambda p: p.update(clips=[{**p['clips'][0], 'clipId': f'clip-{i}'} for i in range(9)]),
    lambda p: p['clips'][0].update(seconds=True),
    lambda p: p['clips'][0].update(seconds=float('nan')),
    lambda p: p['clips'][0].update(seconds=0),
    lambda p: p['clips'][0].update(seconds=16),
    lambda p: p['clips'][1].update(clipId='story-01'),
    lambda p: p['clips'][0].update(prompt=''),
    lambda p: p.update(estimatedCost=0),
    lambda p: p['clips'][0]['references'][0].update(path='../../other-ip/key.png'),
    lambda p: p['clips'][0]['references'][0].update(path='/private/key.png'),
    lambda p: p['clips'][0]['references'][0].update(bytes=True),
    lambda p: p['clips'][0]['references'][0].update(sha256='forged'),
])
def test_invalid_or_unbounded_scope_never_produces_a_plan(tmp_path, change):
    value = store(tmp_path)
    payload = plan(); change(payload)
    with pytest.raises(module().VideoProductionPlanError):
        value.prepare('1' * 32, payload)
    with sqlite3.connect(value.path) as db:
        assert db.execute('SELECT count(*) FROM video_production_plans').fetchone()[0] == 0


@pytest.mark.parametrize('path', ['workspace', 'repository'])
def test_authority_cannot_live_in_any_native_writable_repository_area(tmp_path, path):
    repository = tmp_path / 'repository'; workspace = repository / 'projects' / 'laobai'
    workspace.mkdir(parents=True)
    target = workspace if path == 'workspace' else repository
    with pytest.raises(ValueError):
        module().VideoProductionPlanStore(target / 'plans', workspace=workspace, repository=repository,
            actor_id='owner', ip='laobai', session_id='host-chat')


@pytest.mark.parametrize('repository_argument', [{}, {'repository': None}])
def test_repository_authority_root_cannot_be_omitted_or_none(tmp_path, repository_argument):
    repository = tmp_path / 'repository'
    workspace = repository / 'projects' / 'laobai'
    workspace.mkdir(parents=True)
    target = repository / 'shared-authority'
    with pytest.raises((TypeError, ValueError)):
        module().VideoProductionPlanStore(target, workspace=workspace,
            actor_id='owner', ip='laobai', session_id='host-chat', **repository_argument)
    assert not target.exists()


def test_repository_ancestor_cannot_be_used_as_the_private_authority_directory(tmp_path):
    repository = tmp_path / 'repository'
    workspace = repository / 'projects' / 'laobai'
    workspace.mkdir(parents=True)
    with pytest.raises(ValueError):
        module().VideoProductionPlanStore(tmp_path, workspace=workspace, repository=repository,
            actor_id='owner', ip='laobai', session_id='host-chat')
    assert not (tmp_path / 'video_production_plans.sqlite3').exists()


def test_reference_and_returned_record_mutation_cannot_change_frozen_authority(tmp_path):
    value = store(tmp_path)
    record = approved(value)
    first = value.approved_clip(record['planId'], 'story-01')
    first['references'][0]['path'] = 'foreign.png'
    record['clips'][0]['seconds'] = 15
    still = value.approved_clip(record['planId'], 'story-01')
    assert still['seconds'] == 12 and still['references'][0]['path'] == 'new-film/assets/mother-son.png'


def test_revocation_while_waiting_for_claim_transaction_never_consumes_intent(tmp_path):
    allowed = [True]
    def authorize():
        if not allowed[0]:
            raise PermissionError('permission revoked')
    value = store(tmp_path, authorize=authorize)
    record = approved(value)
    allowed[0] = False
    with pytest.raises(PermissionError):
        value.claim_clip(record['planId'], 'story-01', 'one')
    allowed[0] = True
    assert value.get(record['planId'])['clipClaims'] == []


def test_same_operation_cannot_be_moved_to_another_clip(tmp_path):
    value = store(tmp_path)
    record = approved(value)
    value.claim_clip(record['planId'], 'story-01', 'original-operation')
    with pytest.raises(module().VideoProductionPlanError):
        value.claim_clip(record['planId'], 'story-02', 'original-operation')
    assert len(value.get(record['planId'])['clipClaims']) == 1


def test_plan_mode_remains_visible_after_decline_or_expiry_so_legacy_cannot_bypass_it(tmp_path):
    clock = [1000]
    value = store(tmp_path, clock=lambda: clock[0])
    assert value.has_plan() is False and value.has_approved_plan() is False
    record = value.prepare('1' * 32, plan())
    assert value.has_plan() is True and value.has_approved_plan() is False
    value.decide(record['planId'], record['scopeHash'], False)
    assert value.has_plan() is True and value.has_approved_plan() is False
    other = value.prepare('2' * 32, plan())
    value.decide(other['planId'], other['scopeHash'], True)
    assert value.has_approved_plan() is True
    clock[0] = other['expiresAt']
    assert value.has_approved_plan() is False and value.has_plan() is True
    assert store(tmp_path, session='another-chat').has_plan() is False


@pytest.mark.parametrize('field,value', [('prompt', 'Changed after human approval.'), ('seconds', 14)])
def test_stored_scope_corruption_cannot_silently_change_human_approved_inputs(tmp_path, field, value):
    current = store(tmp_path)
    record = approved(current)
    with sqlite3.connect(current.path) as db:
        raw = json.loads(db.execute('SELECT data FROM video_production_plans WHERE id=?',
                                   (record['planId'],)).fetchone()[0])
        raw['clips'][0][field] = value
        db.execute('UPDATE video_production_plans SET data=? WHERE id=?',
                   (json.dumps(raw), record['planId']))
    with pytest.raises(module().VideoProductionPlanError):
        current.approved_clip(record['planId'], 'story-01')
    with pytest.raises(module().VideoProductionPlanError):
        current.claim_clip(record['planId'], 'story-01', 'not-admitted')
    with sqlite3.connect(current.path) as db:
        assert db.execute('SELECT count(*) FROM video_production_clip_intents').fetchone()[0] == 0


@pytest.mark.parametrize('method', ['get', 'approved_clip', 'claim_clip'])
def test_explicit_actual_workshop_must_match_approved_scope(tmp_path, method):
    value = store(tmp_path)
    record = approved(value)
    with pytest.raises(module().VideoProductionPlanError):
        if method == 'get':
            value.get(record['planId'], workshop_id='2' * 32)
        elif method == 'approved_clip':
            value.approved_clip(record['planId'], 'story-01', workshop_id='2' * 32)
        else:
            value.claim_clip(record['planId'], 'story-01', 'other', workshop_id='2' * 32)


def replacement_plan(*, label='v3'):
    result = plan()
    result['title'] = 'A revised single-scene production ' + label
    result['clips'] = [{**clip, 'clipId': clip['clipId'] + '-' + label} for clip in result['clips']]
    return result


def collected_proof(*, clip='story-01', operation='original-operation'):
    return {'clipId': clip, 'operationId': operation, 'taskId': 'a' * 32,
            'artifactSha256': 'b' * 64}


def parent_with_collected_claim(value):
    record = approved(value)
    value.claim_clip(record['planId'], 'story-01', 'original-operation')
    return value.get(record['planId'])


def test_replacement_preserves_the_original_direct_provider_task_identity(tmp_path):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    proof = {**collected_proof(), 'taskId': 'video_' + 'a' * 32}
    child = replacement(value, parent, evidence=[proof])
    assert child['supersession']['collectedClaims'][0]['taskId'] == 'video_' + 'a' * 32


def replacement(value, parent, *, label='v3', evidence=None):
    return value.prepare('1' * 32, replacement_plan(label=label),
        supersedes_plan_id=parent['planId'],
        collected_claims=[collected_proof()] if evidence is None else evidence)


def test_replacement_approval_closes_only_old_unsubmitted_scope_and_keeps_original_intent(tmp_path):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    child = replacement(value, parent)
    assert child['state'] == 'pending'
    assert value.get(parent['planId'])['state'] == 'approved'
    assert child['supersession'] == {
        'planId': parent['planId'], 'scopeHash': parent['scopeHash'],
        'collectedClaims': [{'clipId': 'story-01', 'operationId': 'original-operation',
            'claimedAt': 1000, 'taskId': 'a' * 32, 'artifactSha256': 'b' * 64}],
        'unsubmittedClipIds': ['story-02', 'story-03', 'close-01']}
    assert replacement(value, parent)['planId'] == child['planId']
    granted = value.decide(child['planId'], child['scopeHash'], True)
    assert granted['state'] == 'approved'
    retired = value.get(parent['planId'])
    assert retired['state'] == 'superseded' and retired['supersededBy'] == child['planId']
    assert retired['clipClaims'] == parent['clipClaims']
    assert retired['scopeHash'] == parent['scopeHash']
    assert retired['maxGenerations'] == 4 and retired['totalSeconds'] == 45
    for clip in ('story-01', 'story-02'):
        with pytest.raises(module().VideoProductionPlanError):
            value.approved_clip(parent['planId'], clip)
        with pytest.raises(module().VideoProductionPlanError):
            value.claim_clip(parent['planId'], clip, 'new-' + clip)
    for index, clip in enumerate(granted['clips']):
        assert value.claim_clip(child['planId'], clip['clipId'], f'new-{index}')['claimed'] is True
    assert value.decide(child['planId'], child['scopeHash'], True)['state'] == 'approved'
    assert replacement(value, parent)['planId'] == child['planId']
    with sqlite3.connect(value.path) as db:
        assert db.execute('SELECT count(*) FROM video_production_clip_intents').fetchone()[0] == 5
    assert value.has_plan() and value.has_approved_plan()


@pytest.mark.parametrize('settlement', ['decline', 'expiry'])
def test_unapproved_replacement_never_revokes_the_original_plan(tmp_path, settlement):
    clock = [1000]
    value = store(tmp_path, clock=lambda: clock[0])
    parent = parent_with_collected_claim(value)
    child = replacement(value, parent)
    if settlement == 'decline':
        assert value.decide(child['planId'], child['scopeHash'], False)['state'] == 'declined'
    else:
        clock[0] = child['expiresAt']
        assert value.get(child['planId'])['state'] == 'expired'
    assert value.get(parent['planId'])['state'] == 'approved'
    assert value.claim_clip(parent['planId'], 'story-02', 'still-original')['claimed'] is True


@pytest.mark.parametrize('evidence', [
    [],
    [collected_proof(operation='unknown-operation')],
    [collected_proof(clip='story-02')],
    [{**collected_proof(), 'taskId': ''}],
    [{**collected_proof(), 'artifactSha256': 'forged'}],
    [{**collected_proof(), 'state': 'collected'}],
    [collected_proof(), collected_proof()],
])
def test_missing_or_mismatched_host_collected_evidence_cannot_reset_claimed_scope(tmp_path, evidence):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    with pytest.raises(module().VideoProductionPlanError):
        replacement(value, parent, evidence=evidence)
    assert value.get(parent['planId'])['state'] == 'approved'
    assert len(value.get(parent['planId'])['clipClaims']) == 1
    with sqlite3.connect(value.path) as db:
        assert db.execute('SELECT count(*) FROM video_production_plans').fetchone()[0] == 1


def test_collected_evidence_is_mandatory_and_cannot_be_added_to_an_ordinary_plan(tmp_path):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    with pytest.raises(module().VideoProductionPlanError):
        value.prepare('1' * 32, replacement_plan(), supersedes_plan_id=parent['planId'])
    with pytest.raises(module().VideoProductionPlanError):
        value.prepare('1' * 32, replacement_plan(), collected_claims=[collected_proof()])
    with pytest.raises(module().VideoProductionPlanError):
        value.prepare('1' * 32, {**replacement_plan(), 'supersession': {'planId': parent['planId']}})


@pytest.mark.parametrize('scope', [{'actor': 'other'}, {'ip': 'other-ip'}, {'session': 'other-chat'}])
def test_replacement_parent_must_have_the_same_authenticated_owner_scope(tmp_path, scope):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    foreign = store(tmp_path, **scope)
    with pytest.raises(module().VideoProductionPlanError) as caught:
        replacement(foreign, parent)
    assert caught.value.status_code == 404


def test_replacement_parent_must_be_an_approved_plan_in_the_same_workshop(tmp_path):
    value = store(tmp_path)
    pending = value.prepare('1' * 32, plan())
    with pytest.raises(module().VideoProductionPlanError):
        value.prepare('1' * 32, replacement_plan(), supersedes_plan_id=pending['planId'], collected_claims=[])
    value.decide(pending['planId'], pending['scopeHash'], True)
    with pytest.raises(module().VideoProductionPlanError) as caught:
        value.prepare('2' * 32, replacement_plan(), supersedes_plan_id=pending['planId'], collected_claims=[])
    assert caught.value.status_code == 404


@pytest.mark.parametrize('old_clip', ['story-01', 'story-02'])
def test_replacement_clip_ids_cannot_rebind_claimed_or_unsubmitted_parent_clips(tmp_path, old_clip):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    source = replacement_plan(); source['clips'][0]['clipId'] = old_clip
    with pytest.raises(module().VideoProductionPlanError):
        value.prepare('1' * 32, source, supersedes_plan_id=parent['planId'],
                      collected_claims=[collected_proof()])


def test_a_parent_claim_after_preparation_invalidates_the_replacement_approval(tmp_path):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    child = replacement(value, parent)
    value.claim_clip(parent['planId'], 'story-02', 'racing-operation')
    with pytest.raises(module().VideoProductionPlanError) as caught:
        value.decide(child['planId'], child['scopeHash'], True)
    assert caught.value.status_code == 409
    assert value.get(parent['planId'])['state'] == 'approved'
    assert value.get(child['planId'])['state'] == 'pending'
    assert len(value.get(parent['planId'])['clipClaims']) == 2


def test_competing_replacements_can_approve_only_one_child(tmp_path):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    one = replacement(value, parent, label='v3')
    two = replacement(value, parent, label='v4')
    barrier = threading.Barrier(2)
    def settle(child):
        other = store(tmp_path)
        barrier.wait(timeout=5)
        try:
            return other.decide(child['planId'], child['scopeHash'], True)['planId']
        except module().VideoProductionPlanError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(settle, [one, two]))
    assert sum(item is not None for item in results) == 1
    winner = next(item for item in results if item)
    assert value.get(parent['planId'])['supersededBy'] == winner
    loser = two if winner == one['planId'] else one
    assert value.get(loser['planId'])['state'] == 'pending'
    with pytest.raises(module().VideoProductionPlanError):
        value.decide(loser['planId'], loser['scopeHash'], True)


def test_parent_new_claim_and_replacement_approval_are_atomically_exclusive(tmp_path):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    child = replacement(value, parent)
    barrier = threading.Barrier(2)
    def race(action):
        other = store(tmp_path)
        barrier.wait(timeout=5)
        try:
            if action == 'claim':
                other.claim_clip(parent['planId'], 'story-02', 'racing-operation')
            else:
                other.decide(child['planId'], child['scopeHash'], True)
            return action
        except module().VideoProductionPlanError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(race, ['claim', 'approve']))
    assert sum(item is not None for item in results) == 1
    retired = value.get(parent['planId'])
    assert (retired['state'] == 'superseded') == (results[1] == 'approve')
    assert len(retired['clipClaims']) == (1 if results[1] == 'approve' else 2)


@pytest.mark.parametrize('field', ['taskId', 'artifactSha256', 'operationId', 'claimedAt', 'unsubmittedClipIds'])
def test_supersession_scope_tampering_never_reaches_approval(tmp_path, field):
    value = store(tmp_path)
    parent = parent_with_collected_claim(value)
    child = replacement(value, parent)
    with sqlite3.connect(value.path) as db:
        raw = json.loads(db.execute('SELECT data FROM video_production_plans WHERE id=?',
                                   (child['planId'],)).fetchone()[0])
        if field == 'unsubmittedClipIds':
            raw['supersession'][field] = []
        else:
            raw['supersession']['collectedClaims'][0][field] = 'c' * 64 if field != 'claimedAt' else 99
        db.execute('UPDATE video_production_plans SET data=? WHERE id=?', (json.dumps(raw), child['planId']))
    with pytest.raises(module().VideoProductionPlanError):
        value.decide(child['planId'], child['scopeHash'], True)
    assert value.get(parent['planId'])['state'] == 'approved'


def test_an_unstarted_approved_plan_can_be_replaced_without_erasing_its_scope(tmp_path):
    value = store(tmp_path)
    parent = approved(value)
    child = value.prepare('1' * 32, replacement_plan(), supersedes_plan_id=parent['planId'], collected_claims=[])
    assert child['supersession']['collectedClaims'] == []
    assert child['supersession']['unsubmittedClipIds'] == ['story-01', 'story-02', 'story-03', 'close-01']
    value.decide(child['planId'], child['scopeHash'], True)
    assert value.get(parent['planId'])['state'] == 'superseded'


def test_ordinary_plan_approval_cannot_add_a_parallel_budget_in_the_same_workshop(tmp_path):
    value = store(tmp_path)
    original = approved(value)
    new = value.prepare('1' * 32, replacement_plan())
    with pytest.raises(module().VideoProductionPlanError) as caught:
        value.decide(new['planId'], new['scopeHash'], True)
    assert caught.value.status_code == 409
    assert value.get(new['planId'])['state'] == 'pending'
    assert value.get(original['planId'])['state'] == 'approved'
    another = value.prepare('2' * 32, replacement_plan())
    assert value.decide(another['planId'], another['scopeHash'], True)['state'] == 'approved'


def test_two_ordinary_pending_plans_compete_for_one_active_workshop_budget(tmp_path):
    value = store(tmp_path)
    one = value.prepare('1' * 32, plan())
    two = value.prepare('1' * 32, replacement_plan())
    barrier = threading.Barrier(2)
    def approve_one(record):
        other = store(tmp_path)
        barrier.wait(timeout=5)
        try:
            return other.decide(record['planId'], record['scopeHash'], True)['planId']
        except module().VideoProductionPlanError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve_one, [one, two]))
    assert sum(item is not None for item in results) == 1


def test_a_later_revision_cannot_reuse_an_earlier_ancestor_clip_identifier(tmp_path):
    value = store(tmp_path)
    original = parent_with_collected_claim(value)
    child = replacement(value, original)
    value.decide(child['planId'], child['scopeHash'], True)
    source = replacement_plan(label='v4')
    source['clips'][0]['clipId'] = 'story-01'
    with pytest.raises(module().VideoProductionPlanError):
        value.prepare('1' * 32, source, supersedes_plan_id=child['planId'], collected_claims=[])
    assert value.get(child['planId'])['state'] == 'approved'
    assert value.get(original['planId'])['clipClaims'] == original['clipClaims']


def test_a_fresh_later_revision_preserves_the_entire_supersession_audit_chain(tmp_path):
    value = store(tmp_path)
    original = parent_with_collected_claim(value)
    child = replacement(value, original)
    value.decide(child['planId'], child['scopeHash'], True)
    grandchild = value.prepare('1' * 32, replacement_plan(label='v4'),
        supersedes_plan_id=child['planId'], collected_claims=[])
    value.decide(grandchild['planId'], grandchild['scopeHash'], True)
    assert value.get(original['planId'])['supersededBy'] == child['planId']
    assert value.get(child['planId'])['supersededBy'] == grandchild['planId']
    assert value.get(grandchild['planId'])['state'] == 'approved'
    assert value.get(original['planId'])['clipClaims'] == original['clipClaims']
    with pytest.raises(module().VideoProductionPlanError):
        value.decide(child['planId'], child['scopeHash'], True)


@pytest.mark.parametrize('invalid_now', [True, 1000.5, -1])
def test_invalid_settlement_clock_cannot_create_a_partially_unreadable_supersession(tmp_path, invalid_now):
    clock = [1000]
    value = store(tmp_path, clock=lambda: clock[0])
    parent = parent_with_collected_claim(value)
    child = replacement(value, parent)
    clock[0] = invalid_now
    with pytest.raises(module().VideoProductionPlanError):
        value.decide(child['planId'], child['scopeHash'], True)
    clock[0] = 1000
    assert value.get(parent['planId'])['state'] == 'approved'
    assert value.get(child['planId'])['state'] == 'pending'


@pytest.mark.parametrize('started', [False, True])
def test_another_host_chat_cannot_approve_a_parallel_budget_in_the_same_owned_workshop(tmp_path, started):
    value = store(tmp_path)
    parent = approved(value)
    if started:
        value.claim_clip(parent['planId'], 'story-01', 'original-operation')
    other = store(tmp_path, session='different-host-chat')
    child = other.prepare('1' * 32, replacement_plan())
    with pytest.raises(module().VideoProductionPlanError) as caught:
        other.decide(child['planId'], child['scopeHash'], True)
    assert caught.value.status_code == 409
    assert parent['planId'] not in str(caught.value)
    assert 'First original mother and child scene.' not in str(caught.value)
    assert other.get(child['planId'])['state'] == 'pending'
    assert value.get(parent['planId'])['state'] == 'approved'
    with pytest.raises(module().VideoProductionPlanError) as hidden:
        other.get(parent['planId'])
    assert hidden.value.status_code == 404


def test_ordinary_pending_plans_in_different_host_chats_compete_for_one_active_workshop_budget(tmp_path):
    value, other = store(tmp_path), store(tmp_path, session='different-host-chat')
    one = value.prepare('1' * 32, plan())
    two = other.prepare('1' * 32, replacement_plan())
    barrier = threading.Barrier(2)
    def approve_one(item):
        current, record = item
        barrier.wait(timeout=5)
        try:
            return current.decide(record['planId'], record['scopeHash'], True)['planId']
        except module().VideoProductionPlanError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve_one, [(value, one), (other, two)]))
    assert sum(item is not None for item in results) == 1


def test_an_unstarted_expired_foreign_chat_scope_does_not_block_or_get_mutated_by_global_guard(tmp_path):
    clock = [1000]
    value = store(tmp_path, clock=lambda: clock[0])
    parent = approved(value)
    with sqlite3.connect(value.path) as db:
        old_bytes = db.execute('SELECT data FROM video_production_plans WHERE id=?', (parent['planId'],)).fetchone()[0]
    clock[0] = parent['expiresAt']
    other = store(tmp_path, session='different-host-chat', clock=lambda: clock[0])
    child = other.prepare('1' * 32, replacement_plan())
    assert other.decide(child['planId'], child['scopeHash'], True)['state'] == 'approved'
    with sqlite3.connect(value.path) as db:
        assert db.execute('SELECT data FROM video_production_plans WHERE id=?', (parent['planId'],)).fetchone()[0] == old_bytes


def test_started_foreign_chat_scope_is_still_active_after_its_original_approval_deadline(tmp_path):
    clock = [1000]
    value = store(tmp_path, clock=lambda: clock[0])
    parent = parent_with_collected_claim(value)
    clock[0] = parent['expiresAt'] + 3600000
    other = store(tmp_path, session='different-host-chat', clock=lambda: clock[0])
    child = other.prepare('1' * 32, replacement_plan())
    with pytest.raises(module().VideoProductionPlanError):
        other.decide(child['planId'], child['scopeHash'], True)
    assert value.get(parent['planId'])['state'] == 'approved'
    assert len(value.get(parent['planId'])['clipClaims']) == 1


@pytest.mark.parametrize('corruption', ['prompt', 'sessionId', 'expiresAt'])
def test_corrupted_foreign_chat_authority_fails_closed_without_exposing_its_spec(tmp_path, corruption):
    value = store(tmp_path)
    parent = approved(value)
    other = store(tmp_path, session='different-host-chat')
    child = other.prepare('1' * 32, replacement_plan())
    with sqlite3.connect(value.path) as db:
        raw = json.loads(db.execute('SELECT data FROM video_production_plans WHERE id=?', (parent['planId'],)).fetchone()[0])
        if corruption == 'prompt':
            raw['clips'][0]['prompt'] = 'foreign-private-do-not-expose'
        elif corruption == 'sessionId':
            raw['sessionId'] = 'other-forged-binding'
        else:
            raw['expiresAt'] = False
        db.execute('UPDATE video_production_plans SET data=? WHERE id=?', (json.dumps(raw), parent['planId']))
    with pytest.raises(module().VideoProductionPlanError) as caught:
        other.decide(child['planId'], child['scopeHash'], True)
    assert caught.value.status_code == 503
    assert parent['planId'] not in str(caught.value)
    assert 'foreign-private-do-not-expose' not in str(caught.value)
    assert other.get(child['planId'])['state'] == 'pending'


@pytest.mark.parametrize('other_scope', [{'actor': 'other-owner'}, {'ip': 'other-ip'}])
def test_global_active_guard_does_not_cross_actor_or_ip_scope(tmp_path, other_scope):
    value = store(tmp_path)
    parent = approved(value)
    other = store(tmp_path, session='different-host-chat', **other_scope)
    child = other.prepare('1' * 32, replacement_plan())
    assert other.decide(child['planId'], child['scopeHash'], True)['state'] == 'approved'
    assert value.get(parent['planId'])['state'] == 'approved'


def test_other_chat_can_approve_an_independent_workshop(tmp_path):
    value = store(tmp_path)
    parent = approved(value)
    other = store(tmp_path, session='different-host-chat')
    child = other.prepare('2' * 32, replacement_plan())
    assert other.decide(child['planId'], child['scopeHash'], True)['state'] == 'approved'
    assert value.get(parent['planId'])['state'] == 'approved'
