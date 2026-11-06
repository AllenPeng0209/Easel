"""Host imports actual edited clips without inventing generation or approval receipts."""
import hashlib
import json
import subprocess

import pytest

from easel.video_workshop import WorkshopError, WorkshopStore
from tests.test_native_workshop import media
from web.workshop_delivery import run_workshop_delivery


@pytest.fixture
def candidate_import(tmp_path, media):
    edited = media.with_name('edited.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(media), '-t', '0.2',
                    '-c:v', 'libx264', '-c:a', 'aac', str(edited)], check=True, capture_output=True)
    store = WorkshopStore(tmp_path)
    project = store.create({'title': '保留剪辑分镜'})
    project = store.production(project['id'], project['revision'], {
        'script': '先倾听，再回应。', 'assets': [], 'shots': [
            {'id': 's01', 'title': '倾听', 'prompt': '母亲倾听', 'seconds': 1, 'asset_ids': []},
            {'id': 's02', 'title': '回应', 'prompt': '孩子回应', 'seconds': 1, 'asset_ids': []}]})
    project = store.add_candidate(project['id'], project['revision'], 's01', 'sources/one.mp4',
                                  candidate_id='1' * 32, task_id='provider:g01')
    original = project['shots'][0]['candidates'][0]
    project = store.deliver(project['id'], project['revision'], 'sources/one.mp4')
    project = store.review(project['id'], project['revision'], project['deliverables'][-1]['id'], {
        'kind': 'human', 'decision': 'approved', 'notes': '此前版本', 'actor_id': 'test-owner'})
    request = {'workshopId': project['id'], 'expectedRevision': project['revision'],
               'shotId': 's02', 'path': 'sources/edited.mp4',
               'sha256': hashlib.sha256(edited.read_bytes()).hexdigest(), 'requestId': '2' * 32,
               'sourceShotId': 's01', 'sourceCandidateId': original['id'],
               'sourceSha256': original['sha256']}
    manifest = tmp_path / 'sources' / 'import.json'
    manifest.write_text(json.dumps(request))
    payload = {'action': 'add_candidate', 'manifestPath': 'sources/import.json'}
    return store, project, manifest, request, payload


def invoke(tmp_path, fixture, changes=None, authorize=lambda: None):
    _, _, manifest, request, payload = fixture
    manifest.write_text(json.dumps({**request, **(changes or {})}))
    return run_workshop_delivery(payload, outputs=tmp_path, ip='current-ip', authorize=authorize)


def test_import_puts_real_edited_candidate_on_secondary_shot_without_forging_task_or_approval(tmp_path, candidate_import):
    store, before, _, request, _ = candidate_import
    receipt, = invoke(tmp_path, candidate_import)
    after = store.get(before['id'])
    candidate = after['shots'][1]['candidates'][0]
    assert candidate['task_id'] is None
    assert candidate['id'] == receipt['candidateId'] != request['requestId']
    assert receipt['requestId'] == request['requestId']
    assert candidate['sha256'] == request['sha256'] == receipt['sha256']
    assert candidate['source_declaration'] == {'kind': 'declared_edit', 'shot_id': 's01',
        'candidate_id': '1' * 32, 'sha256': request['sourceSha256']}
    assert candidate['import_path'] == 'sources/edited.mp4'
    assert candidate['path'] != request['path']
    assert (tmp_path / candidate['path']).read_bytes() == (tmp_path / request['path']).read_bytes()
    assert receipt['selectedCandidateId'] == candidate['id']
    assert receipt['revision'] == before['revision'] + 1 == after['revision']
    assert after['shots'][0] == before['shots'][0]
    assert after['reviews'] == before['reviews']
    assert after['deliverables'] == before['deliverables']
    assert after['publication_preparation'] is None
    assert after['stage'] == 'editing'
    with pytest.raises(WorkshopError) as exc:
        store.review(after['id'], after['revision'], before['deliverables'][-1]['id'], {
            'kind': 'human', 'decision': 'approved', 'notes': '', 'actor_id': 'test-owner'})
    assert exc.value.status == 409


def test_import_keeps_original_selected_until_explicit_adoption_and_allows_reselection(tmp_path, candidate_import):
    store, before, _, request, _ = candidate_import
    receipt, = invoke(tmp_path, candidate_import, {'shotId': 's01'})
    after = store.get(before['id'])
    assert after['shots'][0]['selected_candidate_id'] == '1' * 32
    assert len(after['shots'][0]['candidates']) == 2
    assert after['shots'][0]['candidates'][0] == before['shots'][0]['candidates'][0]
    selected = store.select_candidate(after['id'], after['revision'], 's01', receipt['candidateId'])
    assert selected['stage'] == 'editing'
    reverted = store.select_candidate(after['id'], selected['revision'], 's01', '1' * 32)
    assert reverted['shots'][0]['selected_candidate_id'] == '1' * 32
    assert len(reverted['shots'][0]['candidates']) == 2
    assert reverted['reviews'] == before['reviews']


def test_same_request_replays_after_lost_response_without_duplicate_snapshot(tmp_path, candidate_import):
    store, before, _, _, _ = candidate_import
    first = invoke(tmp_path, candidate_import)
    after = store.get(before['id'])
    snapshots = set(tmp_path.rglob('candidates/*.mp4'))
    assert invoke(tmp_path, candidate_import) == first  # Original, now stale expectedRevision.
    assert store.get(before['id']) == after
    assert set(tmp_path.rglob('candidates/*.mp4')) == snapshots


@pytest.mark.parametrize('changes', [
    {'shotId': 's01'}, {'path': 'sources/one.mp4'}, {'sha256': '0' * 64},
    {'sourceShotId': 's02'}, {'sourceCandidateId': 'f' * 32}, {'sourceSha256': '0' * 64}])
def test_request_id_cannot_be_reused_for_different_import(tmp_path, candidate_import, changes):
    store, before, _, _, _ = candidate_import
    invoke(tmp_path, candidate_import)
    after = store.get(before['id'])
    with pytest.raises(WorkshopError) as exc:
        invoke(tmp_path, candidate_import, changes)
    assert exc.value.status == 409
    assert store.get(before['id']) == after


@pytest.mark.parametrize(('changes', 'status'), [
    ({'expectedRevision': 1}, 409), ({'sourceShotId': 's02'}, 404),
    ({'sourceCandidateId': 'f' * 32}, 404), ({'sourceSha256': '0' * 64}, 409),
    ({'sha256': '0' * 64}, 409), ({'path': '../outside.mp4'}, 422),
    ({'workshopId': 'f' * 32}, 404)])
def test_invalid_import_is_rejected_without_changing_project_or_leaving_snapshot(tmp_path, candidate_import, changes, status):
    store, before, _, _, _ = candidate_import
    snapshots = set(tmp_path.rglob('candidates/*.mp4'))
    with pytest.raises(WorkshopError) as exc:
        invoke(tmp_path, candidate_import, changes)
    assert exc.value.status == status
    assert store.get(before['id']) == before
    assert set(tmp_path.rglob('candidates/*.mp4')) == snapshots


@pytest.mark.parametrize('changes', [
    {'actor': 'owner'}, {'ip': 'other-ip'}, {'task_id': 'fake-provider-task'},
    {'candidateId': '3' * 32}, {'approved': True}, {'expectedRevision': True},
    {'requestId': '../escape'}, {'sha256': 'bad-hash'}])
def test_manifest_cannot_inject_identity_task_approval_or_malformed_values(tmp_path, candidate_import, changes):
    store, before, _, _, _ = candidate_import
    with pytest.raises(ValueError):
        invoke(tmp_path, candidate_import, changes)
    assert store.get(before['id']) == before


def test_candidate_import_cannot_target_another_ip_store(tmp_path, candidate_import):
    store, before, _, request, _ = candidate_import
    other = tmp_path / 'other-ip'
    other.mkdir()
    foreign = WorkshopStore(other).create({'title': 'foreign'})
    with pytest.raises(WorkshopError) as exc:
        invoke(tmp_path, candidate_import, {'workshopId': foreign['id']})
    assert exc.value.status == 404
    assert store.get(before['id']) == before


def test_import_rechecks_turn_authorization_before_mutation(tmp_path, candidate_import):
    store, before, _, _, _ = candidate_import
    calls = 0
    def authorize():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError('turn revoked')
    with pytest.raises(PermissionError):
        invoke(tmp_path, candidate_import, authorize=authorize)
    assert calls == 2
    assert store.get(before['id']) == before


def test_import_rejects_changed_source_candidate_bytes(tmp_path, candidate_import):
    store, before, _, _, _ = candidate_import
    (tmp_path / before['shots'][0]['candidates'][0]['path']).write_bytes(b'changed source')
    with pytest.raises(WorkshopError) as exc:
        invoke(tmp_path, candidate_import)
    assert exc.value.status == 409
    assert store.get(before['id']) == before


def test_import_rejects_nonvideo_even_with_matching_sha(tmp_path, candidate_import):
    store, before, _, _, _ = candidate_import
    (tmp_path / 'sources/edited.mp4').write_bytes(b'not a video')
    snapshots = set(tmp_path.rglob('candidates/*.mp4'))
    with pytest.raises(WorkshopError) as exc:
        invoke(tmp_path, candidate_import, {'sha256': hashlib.sha256(b'not a video').hexdigest()})
    assert exc.value.status == 422
    assert store.get(before['id']) == before
    assert set(tmp_path.rglob('candidates/*.mp4')) == snapshots


def test_import_rejects_symlink_media(tmp_path, candidate_import):
    store, before, _, _, _ = candidate_import
    (tmp_path / 'sources/link.mp4').symlink_to(tmp_path / 'sources/edited.mp4')
    with pytest.raises(WorkshopError) as exc:
        invoke(tmp_path, candidate_import, {'path': 'sources/link.mp4'})
    assert exc.value.status == 403
    assert store.get(before['id']) == before


def test_replay_detects_tampered_import_snapshot(tmp_path, candidate_import):
    store, before, _, _, _ = candidate_import
    invoke(tmp_path, candidate_import)
    after = store.get(before['id'])
    (tmp_path / after['shots'][1]['candidates'][0]['path']).write_bytes(b'tampered')
    with pytest.raises(WorkshopError) as exc:
        invoke(tmp_path, candidate_import)
    assert exc.value.status == 409
    assert store.get(before['id']) == after


def test_import_rejects_episode_filter(tmp_path, candidate_import):
    store, before, _, _, payload = candidate_import
    with pytest.raises(ValueError):
        run_workshop_delivery({**payload, 'episodes': []}, outputs=tmp_path, ip='current-ip', authorize=lambda: None)
    assert store.get(before['id']) == before


def test_import_request_id_cannot_occupy_an_uncollected_generation_candidate_id(tmp_path, candidate_import):
    store, before, _, _, _ = candidate_import
    invoke(tmp_path, candidate_import, {'requestId': 'f' * 32})
    after_import = store.get(before['id'])
    collected = store.add_candidate(before['id'], after_import['revision'], 's02', 'sources/one.mp4',
                                    candidate_id='f' * 32, task_id='provider:g02')
    generated = next(row for row in collected['shots'][1]['candidates'] if row['id'] == 'f' * 32)
    assert generated['task_id'] == 'provider:g02'
    assert generated['sha256'] == before['shots'][0]['candidates'][0]['sha256']
    assert len(collected['shots'][1]['candidates']) == 2
