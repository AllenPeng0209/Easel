"""Workshop production files remain editable downloads within their owning IP."""
import hashlib

import pytest

from tests.test_chat_artifacts import artifact_http_environment
from tests.test_native_workshop import client_for, production


@pytest.mark.access_integration
def test_registered_ass_and_production_documents_are_immutable_and_downloadable(artifact_http_environment):
    root, owner, members, _, _ = artifact_http_environment
    files = {
        'captions.ass': ('subtitle', '[Script Info]\nTitle: 家庭教育\n[Events]\n'),
        'storyboard.json': ('document', '{"shots":[{"id":"shot-1","description":"母亲倾听"}]}'),
        'script.md': ('document', '# 剧本\n先听孩子说完。\n'),
        'cuts.csv': ('document', 'shot,in,out\nshot-1,0,3\n'),
        'notes.txt': ('document', '保留原始声音。\n'),
    }
    source = root / 'outputs' / 'production-files'
    source.mkdir()
    assets = []
    for index, (name, (kind, body)) in enumerate(files.items()):
        (source / name).write_text(body, encoding='utf8')
        assets.append({'id': f'material-{index}', 'kind': kind, 'name': name, 'path': 'production-files/' + name})
    project = owner.post('/api/workshop/projects', json={'title': '完整二修素材'}).json()
    response = owner.put(f"/api/workshop/projects/{project['id']}/production", json={
        'expected_revision': project['revision'], 'script': '完整剧情', 'assets': assets, 'shots': []})
    assert response.status_code == 200, response.text
    registered = response.json()
    for asset in registered['assets']:
        expected = files[asset['name']][1].encode()
        assert asset['sha256'] == hashlib.sha256(expected).hexdigest()
        (source / asset['name']).write_text('工作稿后续已修改', encoding='utf8')
        downloaded = owner.get('/api/media/' + asset['path'])
        assert downloaded.status_code == 200
        assert downloaded.content == expected
        assert downloaded.headers['x-content-type-options'] == 'nosniff'
        assert members['viewer'].get('/api/media/' + asset['path']).status_code == 200
    other = owner.post('/api/projects', json={'name': '其他制作 IP'}).json()['id']
    assert owner.get(f"/api/projects/{other}/media/" + registered['assets'][0]['path']).status_code == 404


@pytest.mark.parametrize('relative,body', [
    ('sources/unsafe.html', '<script>alert(1)</script>'),
    ('sources/run.py', 'print("no")'),
    ('sources/drawing.svg', '<svg onload="alert(1)"/>'),
    ('sources/program.js', 'alert(1)'),
    ('sources/fake.json', 'not json'),
])
def test_document_assets_reject_executables_and_invalid_json(tmp_path, relative, body):
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(body)
    with client_for(tmp_path) as client:
        project = production(client)
        response = client.put(f"/api/workshop/projects/{project['id']}/production", json={
            'expected_revision': project['revision'], 'script': '剧本',
            'assets': [{'id': 'unsafe', 'name': '不安全文件', 'kind': 'document', 'path': relative}], 'shots': []})
        assert response.status_code == 422
        assert client.get(f"/api/workshop/projects/{project['id']}").json()['revision'] == project['revision']


@pytest.mark.parametrize('relative', ['../outside.md', '/tmp/outside.md', '_sessions/private.json'])
def test_document_asset_cannot_escape_content_scope(tmp_path, relative):
    with client_for(tmp_path) as client:
        project = production(client)
        response = client.put(f"/api/workshop/projects/{project['id']}/production", json={
            'expected_revision': project['revision'], 'script': '剧本',
            'assets': [{'id': 'outside', 'name': '越界文件', 'kind': 'document', 'path': relative}], 'shots': []})
        assert response.status_code in {403, 422}


def test_document_upload_validates_file_content_and_keeps_revision(tmp_path):
    with client_for(tmp_path) as client:
        project = production(client)
        route = f"/api/workshop/projects/{project['id']}/assets/upload"
        data = {'expected_revision': project['revision'], 'asset_id': 'script', 'kind': 'document', 'name': '剧本'}
        response = client.post(route, data=data, files={'file': ('script.md', '# 剧本\n真实文本'.encode(), 'text/markdown')})
        assert response.status_code == 200, response.text
        asset = response.json()['assets'][0]
        assert (tmp_path / asset['path']).read_text() == '# 剧本\n真实文本'
        data['expected_revision'] = response.json()['revision']
        bad = client.post(route, data=data, files={'file': ('script.md', b'\x00\xff\x00', 'text/markdown')})
        assert bad.status_code == 422
        loaded = client.get(f"/api/workshop/projects/{project['id']}").json()
        assert loaded['assets'][0]['sha256'] == asset['sha256']
        assert loaded['revision'] == data['expected_revision']
