"""Adopting a collected candidate is scoped, versioned, and not publication approval."""
import json

import pytest

from easel.video_workshop import WorkshopError, WorkshopStore
from tests.test_native_workshop import media
from web.workshop_delivery import run_workshop_delivery


@pytest.fixture
def selection(tmp_path, media):
    store = WorkshopStore(tmp_path)
    project = store.create({'title': '采用已验收镜头'})
    project = store.production(project['id'], project['revision'], {
        'script': '母亲先听孩子说完。', 'assets': [], 'shots': [
            {'id': 'main', 'title': '倾听', 'prompt': '母亲先听完', 'seconds': 1, 'asset_ids': []},
            {'id': 'other', 'title': '回应', 'prompt': '孩子回应', 'seconds': 1, 'asset_ids': []}]})
    for shot, identifier in [('main', '1' * 32), ('main', '2' * 32), ('other', '3' * 32)]:
        project = store.add_candidate(project['id'], project['revision'], shot, 'sources/one.mp4',
                                      candidate_id=identifier)
    project = store.deliver(project['id'], project['revision'], 'sources/one.mp4')
    artifact = project['deliverables'][-1]
    project = store.review(project['id'], project['revision'], artifact['id'], {
        'kind': 'human', 'decision': 'approved', 'notes': '旧版审核记录', 'actor_id': 'test-owner'})
    manifest = tmp_path / 'sources' / 'selection.json'
    choice = {'workshopId': project['id'], 'expectedRevision': project['revision'],
              'shotId': 'main', 'candidateId': '2' * 32}
    manifest.write_text(json.dumps(choice))
    payload = {'action': 'select_candidate', 'manifestPath': 'sources/selection.json'}
    return store, project, manifest, choice, payload


def test_host_adopts_existing_candidate_preserving_history_and_invalidating_old_review(tmp_path, selection):
    store, before, _, _, payload = selection
    result = run_workshop_delivery(payload, outputs=tmp_path, ip='approved-ip', authorize=lambda: None)
    after = store.get(before['id'])
    assert result == [{'workshopId': before['id'], 'revision': before['revision'] + 1,
                      'shotId': 'main', 'selectedCandidateId': '2' * 32, 'stage': 'editing'}]
    assert after['shots'][0]['selected_candidate_id'] == '2' * 32
    assert after['shots'][0]['candidates'] == before['shots'][0]['candidates']
    assert after['shots'][1] == before['shots'][1]
    assert after['reviews'] == before['reviews']
    assert after['deliverables'] == before['deliverables']
    assert after['publication_preparation'] is None
    assert after['publication_runs'] == []
    with pytest.raises(WorkshopError) as error:
        store.review(before['id'], after['revision'], before['deliverables'][-1]['id'], {
            'kind': 'human', 'decision': 'approved', 'notes': '不能批准旧片'})
    assert error.value.status == 409


def test_host_selection_respects_current_revision(tmp_path, selection):
    store, before, _, _, payload = selection
    changed = store.update(before['id'], before['revision'], {'brief': '人工刚修改了要求'})
    with pytest.raises(WorkshopError) as error:
        run_workshop_delivery(payload, outputs=tmp_path, ip='approved-ip', authorize=lambda: None)
    assert error.value.status == 409
    assert store.get(before['id']) == changed


def test_host_selection_cannot_adopt_another_shots_candidate(tmp_path, selection):
    store, before, manifest, choice, payload = selection
    manifest.write_text(json.dumps({**choice, 'candidateId': '3' * 32}))
    with pytest.raises(WorkshopError) as error:
        run_workshop_delivery(payload, outputs=tmp_path, ip='approved-ip', authorize=lambda: None)
    assert error.value.status == 404
    assert store.get(before['id']) == before


def test_host_selection_cannot_read_a_foreign_ip_project(tmp_path, selection):
    _, _, _, choice, payload = selection
    foreign_outputs = tmp_path / 'other-ip' / 'outputs'
    folder = foreign_outputs / 'sources'
    folder.mkdir(parents=True)
    (folder / 'selection.json').write_text(json.dumps(choice))
    with pytest.raises(WorkshopError) as error:
        run_workshop_delivery(payload, outputs=foreign_outputs, ip='other-ip', authorize=lambda: None)
    assert error.value.status == 404
    assert WorkshopStore(foreign_outputs).list() == []


@pytest.mark.parametrize('change', [
    {'actorId': 'someone-else'}, {'path': 'sources/one.mp4'},
    {'candidateId': '../private'}, {'shotId': '../main'},
    {'workshopId': '../other'}, {'expectedRevision': True}, {'expectedRevision': '4'},
])
def test_selection_manifest_rejects_identity_extra_fields_and_coerced_ids(tmp_path, selection, change):
    store, before, manifest, choice, payload = selection
    manifest.write_text(json.dumps({**choice, **change}))
    with pytest.raises(ValueError):
        run_workshop_delivery(payload, outputs=tmp_path, ip='approved-ip', authorize=lambda: None)
    assert store.get(before['id']) == before


def test_host_selection_rechecks_authority_before_mutation(tmp_path, selection):
    store, before, _, _, payload = selection
    checks = 0

    def authorize():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise PermissionError('operation authority revoked')

    with pytest.raises(PermissionError):
        run_workshop_delivery(payload, outputs=tmp_path, ip='approved-ip', authorize=authorize)
    assert store.get(before['id']) == before


def test_host_selection_rejects_changed_candidate_bytes(tmp_path, selection):
    store, before, _, _, payload = selection
    target = before['shots'][0]['candidates'][1]
    (tmp_path / target['path']).write_bytes(b'changed after collection')
    with pytest.raises(WorkshopError) as error:
        run_workshop_delivery(payload, outputs=tmp_path, ip='approved-ip', authorize=lambda: None)
    assert error.value.status == 409
    assert store.get(before['id']) == before
