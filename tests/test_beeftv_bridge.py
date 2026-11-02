import importlib
import json

import httpx
import pytest


def bridge():
    try:
        return importlib.import_module('easel.beeftv_bridge')
    except ModuleNotFoundError:
        pytest.fail('BeefTV bridge is not implemented', pytrace=False)


@pytest.mark.parametrize('url', ['https://example.com/api', 'http://127.0.0.1:8842@evil.test/api', 'file:///tmp/runtime', 'http://localhost:8842/api?token=secret'])
def test_runtime_connection_cannot_send_credentials_outside_loopback(url):
    with pytest.raises(ValueError):
        bridge().BeefTVClient(url, 'client', 'private')


def test_operation_preserves_revision_and_idempotency_key():
    observed = []
    def remote(request):
        observed.append(json.loads(request.content))
        return httpx.Response(200, json={'code': 0, 'data': {'revision': 8, 'created': [{'id': 'shot-1'}]}})
    client = bridge().BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    result = client.operation('canvas.nodes.create', {'canvasId': 'canvas-a', 'expectedRevision': 7, 'nodes': [{'type': 'text', 'title': '脚本'}]}, operation_id='create-shot-v1')
    assert result == {'revision': 8, 'created': [{'id': 'shot-1'}]}
    assert observed == [{'opId': 'create-shot-v1', 'params': {'canvasId': 'canvas-a', 'expectedRevision': 7, 'nodes': [{'type': 'text', 'title': '脚本'}]}}]


def test_mutation_requires_a_stable_operation_id_before_network():
    def remote(request):
        pytest.fail('Unsafe mutation reached the runtime')
    client = bridge().BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    with pytest.raises(ValueError):
        client.operation('canvas.timeline.render', {'canvasId': 'a', 'expectedRevision': 3})


def test_conflict_is_reported_without_retry_or_upstream_body_disclosure():
    calls = []
    def remote(request):
        calls.append(request.url.path)
        return httpx.Response(409, json={'code': 409, 'reason': 'revision_conflict', 'msg': 'private secret fixture'})
    module = bridge()
    client = module.BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    with pytest.raises(module.BeefTVError) as error:
        client.operation('canvas.timeline.update', {'canvasId': 'a', 'expectedRevision': 3}, operation_id='edit-v1')
    assert error.value.status == 409
    assert 'secret fixture' not in str(error.value)
    assert calls == ['/api/ops/canvas.timeline.update']


def test_existing_canvas_is_reused_without_overwriting_human_edits():
    def remote(request):
        assert request.method == 'GET', 'Existing human-edited canvas was overwritten'
        return httpx.Response(200, json={'code': 0, 'data': {'id': 'easel-project1', 'title': '人工改名', 'revision': 9}})
    client = bridge().BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    assert client.create_canvas({'id': 'project1', 'title': '原标题', 'brief': '初稿'}) == 'easel-project1'


def test_new_canvas_uses_the_workshop_project_identity():
    stored = []
    def remote(request):
        if request.method == 'GET':
            return httpx.Response(404, json={'code': 404})
        stored.append(json.loads(request.content)['project'])
        return httpx.Response(200, json={'code': 0, 'data': {'revision': 1}})
    client = bridge().BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    assert client.create_canvas({'id': 'project1', 'title': '视频工坊', 'brief': '原创广告'}) == 'easel-project1'
    assert stored[0]['id'] == 'easel-project1'
    assert stored[0]['title'] == '视频工坊'
    assert stored[0]['revision'] == 0
    assert stored[0]['nodes'] == []
    assert stored[0]['viewport'] == {'x': 0, 'y': 0, 'k': 1}


def test_unready_runtime_is_not_claimed_as_connected():
    def remote(request):
        return httpx.Response(503, json={'code': 503})
    client = bridge().BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    assert client.status()['state'] == 'unavailable'


def test_read_operation_does_not_send_an_op_id_rejected_by_beeftv():
    def remote(request):
        body = json.loads(request.content)
        if 'opId' in body:
            return httpx.Response(400, json={'code': 400, 'reason': 'unexpected_op_id'})
        return httpx.Response(200, json={'code': 0, 'data': {'op': 'canvas.get', 'result': {'canvas': {'id': 'a', 'revision': 4}}}})
    client = bridge().BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    assert client.operation('canvas.get', {'canvasId': 'a'}) == {'canvas': {'id': 'a', 'revision': 4}}


def test_status_checks_a_route_available_to_the_external_client():
    def remote(request):
        if request.url.path == '/api/health/ready':
            return httpx.Response(403, json={'reason': 'external_business_forbidden'})
        return httpx.Response(200, json={'code': 0, 'data': {'ops': [{'id': 'canvas.get'}]}})
    client = bridge().BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    assert client.status()['state'] == 'running'


def test_render_start_records_the_accepted_source_version(monkeypatch):
    def remote(request):
        payload = json.loads(request.content)
        assert payload == {'opId': 'render-project-v7', 'params': {'canvasId': 'canvas-a', 'expectedRevision': 7}}
        return httpx.Response(200, json={'code': 0, 'data': {'op': 'canvas.timeline.render',
            'result': {'taskId': 'a' * 32, 'sourceRevision': 7, 'status': 'queued'}}})
    module = bridge()
    client = module.BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    monkeypatch.setattr(module, 'configured_client', lambda: client)
    assert module.render_start({'beeftv_canvas_id': 'canvas-a', 'engine_current_revision': 7}, 'render-project-v7') == {
        'task_id': 'a' * 32, 'engine_revision': 7, 'status': 'queued', 'progress': 0}


def test_render_status_is_queried_without_starting_a_second_render(monkeypatch):
    def remote(request):
        assert request.method == 'GET'
        return httpx.Response(200, json={'code': 0, 'data': {'status': 'running', 'progress': 43}})
    module = bridge()
    client = module.BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    monkeypatch.setattr(module, 'configured_client', lambda: client)
    assert module.render_status({}, {'task_id': 'a' * 32}) == {'status': 'running', 'progress': .43}


def test_unknown_render_status_does_not_authorize_a_fresh_attempt(monkeypatch):
    def remote(request):
        assert request.method == 'GET'
        return httpx.Response(200, json={'code': 0, 'data': {'status': 'reconciling'}})
    module = bridge()
    client = module.BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    monkeypatch.setattr(module, 'configured_client', lambda: client)
    with pytest.raises(module.BeefTVError) as caught:
        module.render_status({}, {'task_id': 'a' * 32})
    assert caught.value.status == 503


def test_failed_render_cannot_be_collected(monkeypatch, tmp_path):
    def remote(request):
        return httpx.Response(200, json={'code': 0, 'data': {'status': 'failed', 'progress': 0}})
    module = bridge()
    client = module.BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    monkeypatch.setattr(module, 'configured_client', lambda: client)
    with pytest.raises(module.BeefTVError):
        module.render_collect({'id': 'b' * 32}, {'task_id': 'a' * 32, 'engine_revision': 7}, tmp_path)
    assert not list(tmp_path.rglob('*.mp4'))


def test_resource_download_rejects_path_escape_before_any_request(tmp_path):
    def remote(request):
        pytest.fail('Invalid resource id reached the network')
    client = bridge().BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    with pytest.raises(ValueError):
        client.download_resource('../private', tmp_path / 'final.mp4')
