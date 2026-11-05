"""Native production contracts: stable shots, immutable candidates and exact reviews."""
import json
import subprocess
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easel.video_workshop import WorkshopStore
from web.workshop_routes import create_router


@pytest.fixture
def media(tmp_path):
    source = tmp_path / 'sources' / 'one.mp4'
    source.parent.mkdir()
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=red:s=64x64:r=10:d=0.4', '-f', 'lavfi', '-i',
        'sine=frequency=440:duration=0.4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', str(source)], check=True, capture_output=True)
    return source


def client_for(outputs):
    app = FastAPI()
    app.include_router(create_router(lambda: outputs))
    return TestClient(app)


def production(client):
    project = client.post('/api/workshop/projects', json={'title': '对话制作', 'aspect_ratio': '1:1'}).json()
    response = client.put(f"/api/workshop/projects/{project['id']}/production", json={
        'expected_revision': project['revision'], 'script': '母亲放下手机，先听孩子说完。',
        'assets': [], 'shots': [
            {'id': 'shot-1', 'title': '倾听', 'prompt': '母亲放下手机', 'seconds': 1, 'asset_ids': []},
            {'id': 'shot-2', 'title': '回应', 'prompt': '孩子抬头回应', 'seconds': 1, 'asset_ids': []}]})
    assert response.status_code == 200, response.text
    return response.json()


def candidate(client, project, shot, path):
    response = client.post(f"/api/workshop/projects/{project['id']}/shots/{shot}/candidates", json={
        'expected_revision': project['revision'], 'path': path})
    assert response.status_code == 200, response.text
    return response.json()


def test_new_project_is_usable_without_an_external_editor(tmp_path):
    with client_for(tmp_path) as client:
        project = production(client)
        assert project['script'].startswith('母亲')
        assert [shot['id'] for shot in project['shots']] == ['shot-1', 'shot-2']
        assert 'beeftv_canvas_id' not in project
        assert client.post(f"/api/workshop/projects/{project['id']}/canvas", json={'expected_revision': 2}).status_code == 404


def test_candidates_are_immutable_and_rework_preserves_other_shots(tmp_path, media):
    with client_for(tmp_path) as client:
        project = candidate(client, production(client), 'shot-1', 'sources/one.mp4')
        selected = project['shots'][0]['selected_candidate_id']
        first = project['shots'][0]['candidates'][0]
        project = candidate(client, project, 'shot-2', 'sources/one.mp4')
        other = project['shots'][1]
        second = tmp_path / 'sources/two.mp4'
        second.write_bytes(media.read_bytes())
        project = candidate(client, project, 'shot-1', 'sources/two.mp4')
        assert len(project['shots'][0]['candidates']) == 2
        assert project['shots'][0]['selected_candidate_id'] == selected
        assert project['shots'][1] == other
        media.write_bytes(b'changed mutable source')
        assert (tmp_path / first['path']).read_bytes() != b'changed mutable source'
        revision = project['revision']
        target = project['shots'][0]['candidates'][-1]['id']
        response = client.post(f"/api/workshop/projects/{project['id']}/shots/shot-1/select", json={
            'expected_revision': revision, 'candidate_id': target})
        assert response.status_code == 200
        assert response.json()['revision'] == revision + 1
        assert response.json()['shots'][1] == other
        assert client.post(f"/api/workshop/projects/{project['id']}/shots/shot-1/select", json={
            'expected_revision': revision, 'candidate_id': selected}).status_code == 409


def test_native_render_collects_selected_shots_once_and_invalidates_old_review(tmp_path, media):
    with client_for(tmp_path) as client:
        project = production(client)
        for shot in ['shot-1', 'shot-2']:
            project = candidate(client, project, shot, 'sources/one.mp4')
        route = f"/api/workshop/projects/{project['id']}"
        started = client.post(route + '/renders', json={'expected_revision': project['revision']})
        assert started.status_code == 200, started.text
        render = started.json()
        assert client.post(route + '/renders', json={'expected_revision': project['revision']}).json()['id'] == render['id']
        deadline = time.monotonic() + 10
        while render['status'] in {'queued', 'running'} and time.monotonic() < deadline:
            time.sleep(.03)
            render = client.get(route + '/renders/' + render['id']).json()
        assert render['status'] == 'succeeded', render
        result = client.post(route + '/renders/' + render['id'] + '/collect', json={'expected_revision': project['revision']})
        assert result.status_code == 200, result.text
        delivered = result.json()
        assert len(delivered['deliverables']) == 1
        assert client.post(route + '/renders/' + render['id'] + '/collect', json={
            'expected_revision': project['revision']}).json() == delivered
        artifact = delivered['deliverables'][0]
        response = client.post(route + '/reviews', json={'expected_revision': delivered['revision'],
            'artifact_id': artifact['id'], 'kind': 'human', 'decision': 'approved'})
        assert response.status_code == 200, response.text
        updated = client.patch(route, json={'expected_revision': delivered['revision'], 'brief': '改变了内容要求'}).json()
        assert client.post(route + '/reviews', json={'expected_revision': updated['revision'],
            'artifact_id': artifact['id'], 'kind': 'human', 'decision': 'approved'}).status_code == 409


def test_legacy_delivery_survives_loading_without_editor_access(tmp_path, media):
    store = WorkshopStore(tmp_path)
    project = store.create({'title': '已有成片'})
    project = store.deliver(project['id'], 1, 'sources/one.mp4')
    path = store.directory / (project['id'] + '.json')
    legacy = json.loads(path.read_text())
    legacy.update(beeftv_canvas_id='retired-canvas', engine_dirty=True)
    path.write_text(json.dumps(legacy))
    with client_for(tmp_path) as client:
        loaded = client.get('/api/workshop/projects/' + project['id']).json()
        assert loaded['deliverables'][0]['sha256'] == project['deliverables'][0]['sha256']
        assert 'beeftv_canvas_id' not in loaded
        assert client.post(f"/api/workshop/projects/{project['id']}/reviews", json={
            'expected_revision': loaded['revision'], 'artifact_id': loaded['deliverables'][0]['id'],
            'kind': 'human', 'decision': 'approved'}).status_code == 200


def test_assets_keep_versions_and_validate_shot_bindings(tmp_path):
    from PIL import Image
    image = tmp_path / 'sources' / 'mother.png'
    image.parent.mkdir()
    Image.new('RGB', (32, 32), 'red').save(image)
    with client_for(tmp_path) as client:
        project = production(client)
        route = f"/api/workshop/projects/{project['id']}/production"
        payload = {'expected_revision': project['revision'], 'script': project['script'],
            'assets': [{'id': 'mother', 'kind': 'character', 'name': '母亲', 'path': 'sources/mother.png'}],
            'shots': [{**{key: project['shots'][0][key] for key in ['id', 'title', 'prompt', 'seconds']},
                       'asset_ids': ['mother']}]}
        response = client.put(route, json=payload)
        assert response.status_code == 200, response.text
        first = response.json()
        old = first['assets'][0]['path']
        Image.new('RGB', (32, 32), 'blue').save(image)
        payload['expected_revision'] = first['revision']
        second = client.put(route, json=payload).json()
        assert len(second['assets'][0]['versions']) == 2
        assert (tmp_path / old).read_bytes() != image.read_bytes()
        payload['expected_revision'] = second['revision']
        payload['shots'][0]['asset_ids'] = ['foreign']
        assert client.put(route, json=payload).status_code == 422


def test_asset_upload_creates_real_versions_and_rejects_disguised_images(tmp_path):
    from io import BytesIO
    from PIL import Image
    def image_bytes(color):
        stream = BytesIO()
        Image.new('RGB', (32, 32), color).save(stream, format='PNG')
        return stream.getvalue()
    with client_for(tmp_path) as client:
        project = production(client)
        route = f"/api/workshop/projects/{project['id']}/assets/upload"
        data = {'expected_revision': project['revision'], 'asset_id': 'mother', 'kind': 'character', 'name': '母亲'}
        response = client.post(route, data=data, files={'file': ('mother.png', image_bytes('red'), 'image/png')})
        assert response.status_code == 200, response.text
        first = response.json()
        data['expected_revision'] = first['revision']
        response = client.post(route, data=data, files={'file': ('mother.png', image_bytes('blue'), 'image/png')})
        assert response.status_code == 200, response.text
        assert len(response.json()['assets'][0]['versions']) == 2
        data['expected_revision'] = response.json()['revision']
        assert client.post(route, data=data, files={'file': ('fake.png', b'not a picture', 'image/png')}).status_code == 422


def test_skill_manifest_automatically_delivers_a_master_and_resumes_without_duplicate(tmp_path, media):
    from web.workshop_delivery import run_workshop_delivery
    manifest = tmp_path / 'sources' / 'film.json'
    manifest.write_text(json.dumps({'series': '家庭教育', 'episodes': [{'id': '01', 'title': '先听孩子说完',
        'brief': '已完成剪辑字幕的短剧', 'path': 'sources/one.mp4'}]}))
    payload = {'action': 'deliver', 'manifestPath': 'sources/film.json'}
    first = run_workshop_delivery(payload, outputs=tmp_path, ip='default', authorize=lambda: None)
    second = run_workshop_delivery(payload, outputs=tmp_path, ip='default', authorize=lambda: None)
    assert first[0]['artifactId'] == second[0]['artifactId']
    assert len(WorkshopStore(tmp_path).list()) == 1
    assert len(WorkshopStore(tmp_path).get(first[0]['workshopId'])['deliverables']) == 1


@pytest.mark.parametrize('path', ['../outside.mp4', '/tmp/video.mp4', '_workshop/file.mp4'])
def test_candidate_cannot_escape_the_current_content_library(tmp_path, path):
    with client_for(tmp_path) as client:
        project = production(client)
        response = client.post(f"/api/workshop/projects/{project['id']}/shots/shot-1/candidates", json={
            'expected_revision': project['revision'], 'path': path})
        assert response.status_code in {400, 403, 422}
