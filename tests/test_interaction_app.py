from fastapi.testclient import TestClient
import pytest

import web.app as host


@pytest.mark.parametrize('route', ['media', 'output'])
def test_interaction_database_cannot_be_downloaded_as_a_content_artifact(tmp_path, monkeypatch, route):
    private = tmp_path / '_interactions' / 'center.sqlite3'
    private.parent.mkdir()
    private.write_bytes(b'private-customer-conversations')
    monkeypatch.setattr(host, 'OUTPUTS_DIR', tmp_path)
    client = TestClient(host.app, base_url='http://127.0.0.1:7860')
    response = client.get(f'/api/{route}/_interactions/center.sqlite3')
    assert response.status_code == 403
    assert b'private-customer-conversations' not in response.content


def test_interaction_routes_and_automatic_processor_share_the_app_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'OUTPUTS_DIR', tmp_path)
    with TestClient(host.app, base_url='http://127.0.0.1:7860', headers={'Origin': 'http://127.0.0.1:7860'}) as client:
        response = client.get('/api/interactions/bootstrap')
        assert response.status_code == 200
        data = response.json()
        assert data['clients'] and data['conversations'] == []
        worker = host.app.state.interaction_worker
        assert worker.running
    assert worker.running is False


def test_inboxes_follow_the_selected_project_even_with_the_same_customer_id(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(host, 'OUTPUTS_DIR', tmp_path / 'outputs')
    with TestClient(host.app, base_url='http://127.0.0.1:7860', headers={'Origin': 'http://127.0.0.1:7860'}) as client:
        a = client.post('/api/projects', json={'name': '客户空间A'}).json()['id']
        b = client.post('/api/projects', json={'name': '客户空间B'}).json()['id']
        first = client.get(f'/api/projects/{a}/interactions/bootstrap').json()
        other = client.get(f'/api/projects/{b}/interactions/bootstrap').json()
        assert first['client_id'] == other['client_id']
        seeded = client.post(f'/api/projects/{a}/interactions/simulation', json={'client_id': first['client_id']})
        assert seeded.status_code == 200 and seeded.json()['conversations']
        assert client.get(f'/api/projects/{b}/interactions/bootstrap').json()['conversations'] == []
        assert client.get('/api/interactions/bootstrap').json()['conversations'] == []
