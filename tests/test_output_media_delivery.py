import pytest
from fastapi.testclient import TestClient
from easel.access import AccessStore

pytestmark = pytest.mark.access_integration


def test_output_media_revalidates_privately_and_keeps_original_download_bytes(tmp_path, monkeypatch):
    from web import app as web
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    folder = tmp_path / 'outputs' / 'film'
    folder.mkdir(parents=True)
    original = b'0123456789abcdefghijklmnopqrstuv'
    (folder / 'clip.mp4').write_bytes(original)
    _, token = AccessStore(tmp_path).setup('owner', 'output-media-tests-42', project_ids=('default',))
    client = TestClient(web.app, base_url='http://localhost:7860',
        headers={'Origin': 'http://localhost:7860', 'Cookie': 'easel_session=' + token})
    result = client.get('/api/media/film/clip.mp4')
    assert result.content == original
    assert result.headers['cache-control'] == 'private, no-cache'
    etag = result.headers['etag']
    assert client.get('/api/media/film/clip.mp4', headers={'If-None-Match': etag}).status_code == 304
    client.headers.pop('Cookie')
    assert client.get('/api/media/film/clip.mp4', headers={'If-None-Match': etag}).status_code == 401
