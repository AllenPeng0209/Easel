"""The real authenticated bridge serves media without opening the production engine."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easel.access import AccessStore
from easel.video_workshop import WorkshopStore
from web.access_control import AccessControlMiddleware
from web import huobao_bridge

pytestmark = pytest.mark.access_integration


@pytest.fixture
def delivery_client(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    store = WorkshopStore(root / 'outputs')
    project = store.create({'title': 'Existing video'})
    directory = tmp_path / 'private-engine'
    source = directory / 'data' / 'static' / 'videos' / 'clip.mp4'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'0123456789abcdefghijklmnopqrstuv')

    def forbidden_open(*args):
        raise AssertionError('Reading media must not open or synchronize the video engine')

    engine = SimpleNamespace(directory=directory, data_root=directory / 'data', store=store,
                             bindings=lambda: {project['id']: {'episode_id': 1}}, open=forbidden_open)
    monkeypatch.setattr(huobao_bridge, 'runtime', lambda host: engine)
    host = SimpleNamespace(PROJECT_ROOT=root)
    app = FastAPI()
    app.include_router(huobao_bridge.create_router(lambda: host))
    app.add_middleware(AccessControlMiddleware, get_root=lambda: root)
    _, token = AccessStore(root).setup('owner', 'private-media-tests-42', project_ids=('default',))
    assert AccessStore(root).authenticate_token(token) is not None
    client = TestClient(app, base_url='http://localhost:7860',
                        headers={'Origin': 'http://localhost:7860', 'Cookie': 'easel_session=' + token})
    return client, f"/api/workshop/projects/{project['id']}/engine/static/videos/clip.mp4"


def test_authenticated_media_keeps_range_metadata_and_conditional_cache(delivery_client):
    client, url = delivery_client
    result = client.get(url, headers={'Range': 'bytes=0-9'})
    assert result.status_code == 206, result.text
    assert result.content == b'0123456789'
    assert result.headers['content-length'] == '10'
    assert result.headers['content-range'] == 'bytes 0-9/32'
    assert result.headers['cache-control'] == 'private, no-cache'
    assert client.get(url, headers={'If-None-Match': result.headers['etag']}).status_code == 304
    client.headers.pop('Cookie')
    assert client.get(url, headers={'If-None-Match': result.headers['etag']}).status_code == 401


def test_media_cache_does_not_serve_an_unbound_project(delivery_client):
    client, url = delivery_client
    parts = url.split('/')
    parts[4] = 'a' * 32
    assert client.get('/'.join(parts)).status_code == 404


def test_hashed_editor_assets_are_immutable_while_its_html_is_revalidated(tmp_path, monkeypatch):
    public = tmp_path / 'frontend' / '.output' / 'public'
    (public / '_nuxt').mkdir(parents=True)
    (public / '_nuxt' / 'Test1234.js').write_text('export const ready=true;')
    (public / 'index.html').write_text('<main>Video workbench</main>')
    monkeypatch.setattr(huobao_bridge, 'SOURCE', tmp_path)
    app = FastAPI()
    app.include_router(huobao_bridge.create_router(lambda: None))
    client = TestClient(app)
    asset = client.get('/huobao-studio/_nuxt/Test1234.js')
    assert asset.status_code == 200
    assert asset.headers.get('cache-control') == 'public, max-age=31536000, immutable'
    assert client.get('/huobao-studio/drama/1').headers.get('cache-control') == 'no-cache'


@pytest.mark.parametrize('method', ['GET', 'HEAD'])
def test_editor_json_reads_use_the_revision_checked_binding_without_full_open(tmp_path, monkeypatch, method):
    import httpx
    observed = []

    def read_binding(identifier):
        observed.append(identifier)
        return {'episode_id': 1}

    def full_sync(*args):
        raise AssertionError('A JSON read must not open or pull the whole project')

    identifier = 'a' * 32
    engine = SimpleNamespace(directory=tmp_path, state={'base': 'http://engine', 'token': 'fixture'},
                             read_binding=read_binding, open=full_sync, pull=full_sync)
    monkeypatch.setattr(huobao_bridge, 'runtime', lambda host: engine)

    async def transport(host, runtime):
        return httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={'code': 200, 'data': []}))), True

    monkeypatch.setattr(huobao_bridge, 'proxy_client', transport)
    app = FastAPI()
    app.include_router(huobao_bridge.create_router(lambda: SimpleNamespace()))
    response = TestClient(app).request(method, f'/api/workshop/projects/{identifier}/engine/api/v1/episodes/1/characters')
    assert response.status_code == 200
    assert observed == [identifier]
