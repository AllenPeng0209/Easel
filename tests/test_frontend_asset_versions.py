"""An open tab must still load its chunk graph after a frontend replacement."""
from fastapi.testclient import TestClient
import pytest

from web import app as web


@pytest.fixture
def frontend(tmp_path, monkeypatch):
    current = tmp_path / 'dist'
    archive = tmp_path / 'archive'
    (current / 'assets').mkdir(parents=True)
    archive.mkdir()
    (current / 'index.html').write_text('<html>new release</html>')
    monkeypatch.setattr(web, 'REACT_DIR', current)
    monkeypatch.setattr(web, 'REACT_ASSET_ARCHIVE', archive, raising=False)
    return TestClient(web.app, base_url='http://localhost:7860'), current / 'assets', archive


def test_an_open_tab_can_load_its_previous_chunk_and_dependency_after_replacement(frontend):
    client, current, archive = frontend
    (archive / 'InteractionCenterPage-previous.js').write_text("import './index-previous.js'; export default 'old';")
    (archive / 'index-previous.js').write_text("export const version = 'previous';")
    (current / 'InteractionCenterPage-current.js').write_text("export default 'current';")
    for name in ['InteractionCenterPage-previous.js', 'index-previous.js', 'InteractionCenterPage-current.js']:
        response = client.get('/assets/' + name)
        assert response.status_code == 200, response.text
        assert 'javascript' in response.headers['content-type']
        assert 'immutable' in response.headers['cache-control']
    assert 'no-store' in client.get('/').headers['cache-control']


def test_active_assets_take_precedence_over_history(frontend):
    client, current, archive = frontend
    (current / 'current.js').write_text('active')
    (archive / 'current.js').write_text('historical')
    assert client.get('/assets/current.js').text == 'active'


def test_missing_javascript_is_not_replaced_by_the_html_entrypoint(frontend):
    client, _, _ = frontend
    response = client.get('/assets/not-built.js')
    assert response.status_code == 404
    assert 'text/html' not in response.headers.get('content-type', '')


def test_asset_history_cannot_serve_files_outside_its_directory(frontend, tmp_path):
    client, _, archive = frontend
    private = tmp_path / 'private.js'
    private.write_text('private fixture')
    (archive / 'outside.js').symlink_to(private)
    response = client.get('/assets/outside.js')
    assert response.status_code in (403, 404)
    assert 'private fixture' not in response.text
    assert client.get('/assets/%2e%2e/private.js').status_code in (403, 404)
