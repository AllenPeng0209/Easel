"""One exact human workshop review dispatches the approved film only once."""
import subprocess
import time

import pytest

import web.app as web
from easel.video_workshop import WorkshopStore
from tests.test_native_workshop import media
from tests.test_chat_artifacts import artifact_http_environment
from tests.test_publication_workflow_http import publication_environment

_RUN = subprocess.run
pytestmark = pytest.mark.access_integration


@pytest.fixture
def ready(media, publication_environment, monkeypatch):
    root, owner, members, client, sent = publication_environment
    monkeypatch.setattr(subprocess, 'run', _RUN)
    source = root / 'outputs' / 'film' / 'master.mp4'
    source.parent.mkdir()
    source.write_bytes(media.read_bytes())
    store = WorkshopStore(root / 'outputs')
    project = store.create({'title': '审核后自动发布'})
    project = store.deliver(project['id'], 1, 'film/master.mp4')
    return owner, members, store, project, sent


def prepare(owner, project):
    response = owner.post(f"/api/workshop/projects/{project['id']}/publish/prepare", json={
        'expected_revision': project['revision'], 'artifact_id': project['deliverables'][-1]['id'],
        'platform': 'douyin', 'title': '先听孩子说完', 'body': '家庭教育短剧', 'tags': '家庭教育',
        'aiGenerated': True})
    assert response.status_code == 200, response.text
    return response.json()


def review(owner, project, approval, **changes):
    return owner.post(f"/api/workshop/projects/{project['id']}/reviews", json={
        'expected_revision': project['revision'], 'artifact_id': project['deliverables'][-1]['id'],
        'kind': 'human', 'decision': 'approved', 'publication_approval_id': approval['id'],
        'publication_payload_hash': approval['payloadHash'], **changes})


def test_one_review_dispatches_the_frozen_video_and_duplicate_click_never_republishes(ready):
    owner, _, store, project, sent = ready
    with owner:
        prepared = prepare(owner, project)
        assert not sent
        approval = prepared['approval']
        assert approval['subject']['account']['id'] == 'actual-account-1'
        assert approval['subject']['media'][0]['sha256'] == project['deliverables'][-1]['sha256']
        response = review(owner, project, approval)
        assert response.status_code == 200, response.text
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = owner.get('/api/workshop/projects/' + project['id']).json()
            if current['publication_runs'] and (current['publication_runs'][-1].get('result') or {}).get('status') == 'submitted':
                break
            time.sleep(.01)
        assert current['publication_runs'][-1]['result']['status'] == 'submitted'
        assert len(sent) == 1
        assert sent[0]['media'] == [(store.outputs / project['deliverables'][-1]['path']).read_bytes()]
        assert current['publications'] == [], 'A submission acknowledgement is not a public post'
        assert review(owner, project, approval).status_code == 200
        assert len(sent) == 1


@pytest.mark.parametrize('kind,decision', [('simulated', 'approved'), ('human', 'changes_requested')])
def test_simulation_or_requested_changes_never_publish(ready, kind, decision):
    owner, _, _, project, sent = ready
    with owner:
        prepared = prepare(owner, project)
        response = review(owner, project, prepared['approval'], kind=kind, decision=decision, notes='请修改开头')
        assert response.status_code == 200, response.text
        assert not sent


def test_modified_film_or_account_requires_a_new_review(ready, monkeypatch):
    owner, _, _, project, sent = ready
    with owner:
        prepared = prepare(owner, project)
        async def changed(platform):
            return {'id': 'other-account', 'name': '另一个账号'}
        monkeypatch.setattr(web, '_publication_account', changed)
        response = review(owner, project, prepared['approval'])
        assert response.status_code == 409, response.text
        assert not sent


def test_viewer_cannot_approve_and_stale_publish_subject_is_rejected(ready):
    owner, members, store, project, sent = ready
    prepared = prepare(owner, project)
    assert review(members['viewer'], project, prepared['approval']).status_code == 403
    store.update(project['id'], project['revision'], {'brief': '修改后的内容要求'})
    assert review(owner, project, prepared['approval']).status_code == 409
    assert not sent
