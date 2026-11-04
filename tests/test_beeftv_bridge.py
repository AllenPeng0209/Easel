import importlib
import json

import httpx
import pytest
from pathlib import Path


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
        'task_id': 'a' * 32, 'engine_revision': 7, 'status': 'queued'}


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


@pytest.mark.parametrize("reported, expected", [(None, {}), (False, {}), ("nan", {}), (-1, {}), (101, {}), (0, {"progress": 0.0})])
def test_render_status_does_not_invent_missing_progress(monkeypatch, reported, expected):
    def remote(request):
        assert request.method == 'GET'
        return httpx.Response(200, json={'code': 0, 'data': {'status': 'running', 'progress': reported}})
    module = bridge()
    client = module.BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private', transport=httpx.MockTransport(remote))
    monkeypatch.setattr(module, 'configured_client', lambda: client)
    assert module.render_status({}, {'task_id': 'a' * 32}) == {'status': 'running', **expected}


def _cancel_fixture(monkeypatch, respond):
    module = bridge()
    task_id = 'b' * 32
    canvas = {'beeftv_canvas_id': 'canvas-test'}
    receipt = {'task_id': task_id, 'canvas_id': 'canvas-test', 'operation_id': 'easel-operation'}
    task = {'id': task_id, 'type': 'timeline_render', 'projectId': 'canvas-test',
            'clientOperationId': 'assistant-render:easel-operation'}
    calls = []

    def remote(request):
        calls.append((request.method, request.url.path))
        data = respond(request, task)
        return httpx.Response(200, json={'code': 0, 'data': data})

    client = module.BeefTVClient('http://127.0.0.1:8842/api', 'client', 'private',
                                 transport=httpx.MockTransport(remote))
    monkeypatch.setattr(module, 'configured_client', lambda: client)
    return module, task_id, canvas, receipt, calls


def test_render_cancel_checks_bound_task_then_sends_only_one_targeted_post(monkeypatch):
    def respond(request, task):
        return {**task, 'status': 'running' if request.method == 'GET' else 'cancelled',
                'progress': 41, 'error': 'private provider error'}

    module, task_id, canvas, receipt, calls = _cancel_fixture(monkeypatch, respond)
    assert module.render_cancel(canvas, receipt) == {'task_id': task_id, 'status': 'cancelled', 'progress': .41}
    assert calls == [('GET', f'/api/tasks/{task_id}'), ('POST', f'/api/tasks/{task_id}/cancel')]


@pytest.mark.parametrize('state', ['succeeded', 'cancelled', 'failed'])
def test_render_cancel_never_posts_for_a_known_terminal_task(monkeypatch, state):
    module, task_id, canvas, receipt, calls = _cancel_fixture(
        monkeypatch, lambda _request, task: {**task, 'status': state})
    assert module.render_cancel(canvas, receipt) == {'task_id': task_id, 'status': state}
    assert calls == [('GET', f'/api/tasks/{task_id}')]


@pytest.mark.parametrize('field,foreign', [('id', 'c' * 32), ('type', 'video'), ('projectId', 'other-canvas'),
                                            ('clientOperationId', 'assistant-render:other-operation')])
def test_render_cancel_refuses_foreign_preflight_identity(monkeypatch, field, foreign):
    module, task_id, canvas, receipt, calls = _cancel_fixture(
        monkeypatch, lambda _request, task: {**task, field: foreign, 'status': 'running'})
    with pytest.raises(module.BeefTVError):
        module.render_cancel(canvas, receipt)
    assert calls == [('GET', f'/api/tasks/{task_id}')]


def test_render_cancel_returns_real_completed_race_instead_of_cancelled(monkeypatch):
    module, task_id, canvas, receipt, calls = _cancel_fixture(monkeypatch,
        lambda request, task: {**task, 'status': 'running' if request.method == 'GET' else 'succeeded'})
    assert module.render_cancel(canvas, receipt) == {'task_id': task_id, 'status': 'succeeded'}
    assert calls == [('GET', f'/api/tasks/{task_id}'), ('POST', f'/api/tasks/{task_id}/cancel')]


def test_ambiguous_cancel_does_not_repeat_post_and_can_query_the_original_task(monkeypatch):
    def respond(request, task):
        if request.method == 'POST':
            raise httpx.ReadTimeout('upstream response unavailable')
        return {**task, 'status': 'running'}

    module, task_id, canvas, receipt, calls = _cancel_fixture(monkeypatch, respond)
    with pytest.raises(module.BeefTVError):
        module.render_cancel(canvas, receipt)
    assert module.render_cancel_status(canvas, receipt) == {'task_id': task_id, 'status': 'running'}
    assert calls == [('GET', f'/api/tasks/{task_id}'), ('POST', f'/api/tasks/{task_id}/cancel'),
                     ('GET', f'/api/tasks/{task_id}')]


def test_render_cancel_does_not_accept_a_different_task_in_post_receipt(monkeypatch):
    def respond(request, task):
        if request.method == 'GET':
            return {**task, 'status': 'queued'}
        return {**task, 'id': 'd' * 32, 'status': 'cancelled'}

    module, task_id, canvas, receipt, calls = _cancel_fixture(monkeypatch, respond)
    with pytest.raises(module.BeefTVError, match='无法确认原渲染任务身份'):
        module.render_cancel(canvas, receipt)
    assert calls == [('GET', f'/api/tasks/{task_id}'), ('POST', f'/api/tasks/{task_id}/cancel')]


def test_render_cancel_reauthorizes_after_preflight_before_provider_write(monkeypatch):
    module, task_id, canvas, receipt, calls = _cancel_fixture(
        monkeypatch, lambda _request, task: {**task, 'status': 'running'})

    def revoked():
        assert calls == [('GET', f'/api/tasks/{task_id}')]
        raise PermissionError('revoked')

    with pytest.raises(PermissionError, match='revoked'):
        module.render_cancel(canvas, receipt, before_post=revoked)
    assert calls == [('GET', f'/api/tasks/{task_id}')]


def test_unconfigured_default_runtime_never_falls_back_to_the_legacy_workspace(tmp_path, monkeypatch):
    module = bridge()
    previous = module.CONNECTION
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path == previous:
            return json.dumps({'base_url': 'http://127.0.0.1:8842/api', 'client_id': 'legacy', 'client_token': 'fixture'})
        return original(path, *args, **kwargs)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(Path, 'read_text', read)
    original_client = httpx.Client
    monkeypatch.setattr(module.httpx, 'Client', lambda **kwargs: original_client(
        **{**kwargs, 'transport': httpx.MockTransport(lambda request: httpx.Response(200, json={'data': {'ops': []}}))}))
    status = module.runtime_status()
    assert status['state'] == 'unavailable'
    assert status['ui_url'] == ''
    assert not (tmp_path / '.easel').exists()


def _migration_binding(tmp_path, monkeypatch):
    module = bridge()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    project = {'id': 'b' * 32, 'beeftv_canvas_id': 'owned-canvas'}
    receipt = {'task_id': 'a' * 32, 'canvas_id': 'owned-canvas', 'operation_id': 'owned-render', 'engine_revision': 7}
    record = {'local_project_id': project['id'], 'canvas_id': receipt['canvas_id'],
              'task_id': receipt['task_id'], 'operation_id': receipt['operation_id'], 'engine_revision': 7}
    directory = tmp_path / '.easel/beeftv/run'
    directory.mkdir(parents=True)
    manifest = directory / 'migration.json'
    manifest.write_text(json.dumps({'version': 1, 'project_id': 'default', 'workspace': str(tmp_path),
                                   'legacy_renders': [record]}))
    return module, project, receipt, record, manifest


def test_only_an_exact_migrated_receipt_can_read_its_legacy_render_status(tmp_path, monkeypatch):
    module, project, receipt, _, _ = _migration_binding(tmp_path, monkeypatch)
    calls = []
    def new(request):
        calls.append(('new', request.method, request.url.path))
        return httpx.Response(404)
    def old(request):
        calls.append(('old', request.method, request.url.path))
        return httpx.Response(200, json={'data': {'id': receipt['task_id'], 'type': 'timeline_render',
            'projectId': 'owned-canvas', 'clientOperationId': 'assistant-render:owned-render', 'status': 'running', 'progress': 30}})
    new_client = module.BeefTVClient('http://127.0.0.1:33001/api', 'new', 'fixture', transport=httpx.MockTransport(new))
    old_client = module.BeefTVClient('http://127.0.0.1:8842/api', 'old', 'fixture', transport=httpx.MockTransport(old))
    monkeypatch.setattr(module, 'configured_client', lambda: new_client)
    monkeypatch.setattr(module, 'legacy_client', lambda repository=None: old_client, raising=False)
    assert module.render_status(project, receipt) == {'status': 'running', 'progress': .3}
    assert calls == [('new', 'GET', '/api/tasks/' + receipt['task_id']), ('old', 'GET', '/api/tasks/' + receipt['task_id'])]


@pytest.mark.parametrize('field,value', [('task_id', 'c' * 32), ('canvas_id', 'another-canvas'),
    ('local_project_id', 'd' * 32), ('operation_id', 'another-operation'), ('engine_revision', 8)])
def test_migration_manifest_does_not_authorize_another_legacy_task(tmp_path, monkeypatch, field, value):
    module, project, receipt, record, manifest = _migration_binding(tmp_path, monkeypatch)
    body = json.loads(manifest.read_text())
    record[field] = value
    body['legacy_renders'] = [record]
    manifest.write_text(json.dumps(body))
    client = module.BeefTVClient('http://127.0.0.1:33001/api', 'new', 'fixture',
        transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    monkeypatch.setattr(module, 'configured_client', lambda: client)
    monkeypatch.setattr(module, 'legacy_client', lambda repository=None: pytest.fail('Foreign legacy task was queried'), raising=False)
    with pytest.raises(module.BeefTVError):
        module.render_status(project, receipt)


def test_legacy_collection_downloads_from_the_same_verified_source_runtime(tmp_path, monkeypatch):
    module, project, receipt, _, _ = _migration_binding(tmp_path, monkeypatch)
    def new(request):
        assert request.url.path.startswith('/api/tasks/')
        return httpx.Response(404)
    def old(request):
        if '/resources/' in request.url.path:
            return httpx.Response(200, content=b'owned-video-fixture')
        return httpx.Response(200, json={'data': {'id': receipt['task_id'], 'type': 'timeline_render',
            'projectId': 'owned-canvas', 'clientOperationId': 'assistant-render:owned-render',
            'status': 'succeeded', 'resultJson': {'resourceId': 'owned-resource'}}})
    monkeypatch.setattr(module, 'configured_client', lambda: module.BeefTVClient(
        'http://127.0.0.1:33001/api', 'new', 'fixture', transport=httpx.MockTransport(new)))
    monkeypatch.setattr(module, 'legacy_client', lambda repository=None: module.BeefTVClient(
        'http://127.0.0.1:8842/api', 'old', 'fixture', transport=httpx.MockTransport(old)), raising=False)
    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    result = module.render_collect(project, receipt, outputs)
    assert (outputs / result['path']).read_bytes() == b'owned-video-fixture'


def test_collect_without_an_explicit_directory_stays_inside_the_current_ip(tmp_path, monkeypatch):
    from easel.projects import project_context

    module = bridge()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    selected = '9cf7c562af154245'
    workspace = tmp_path / 'projects' / selected
    (workspace / 'outputs').mkdir(parents=True)
    (tmp_path / 'outputs').mkdir()
    def remote(request):
        if '/resources/' in request.url.path:
            return httpx.Response(200, content=b'current-ip-video')
        return httpx.Response(200, json={'data': {'status': 'succeeded', 'resultJson': {'resourceId': 'owned-resource'}}})
    client = module.BeefTVClient('http://127.0.0.1:33001/api', 'selected', 'fixture', transport=httpx.MockTransport(remote))
    monkeypatch.setattr(module, 'configured_client', lambda: client)
    with project_context(selected, tmp_path):
        result = module.render_collect({'id': 'b' * 32}, {'task_id': 'a' * 32, 'engine_revision': 7})
    assert (workspace / 'outputs' / result['path']).read_bytes() == b'current-ip-video'
    assert not list((tmp_path / 'outputs').rglob('*.mp4'))


def _private_runtime(repository, workspace, label):
    from easel.beeftv_runtime import runtime_paths, ensure_instance, save_private_json

    paths = runtime_paths(repository, workspace)
    instance = ensure_instance(paths)
    record = {**instance, 'base_url': instance['api_url'] + '/api',
              'client_id': label, 'client_token': 'offline-client-fixture'}
    save_private_json(paths.connection, record)
    return paths, instance


def test_configured_clients_and_canvas_urls_follow_the_entire_ip_context(tmp_path, monkeypatch):
    from easel.projects import project_context

    identifier = '9cf7c562af154245'
    selected = tmp_path / 'projects' / identifier
    selected.mkdir(parents=True)
    (tmp_path / 'projects/_registry.json').write_text(json.dumps([{'id': 'default'}, {'id': identifier}]))
    _, first = _private_runtime(tmp_path, tmp_path, 'default-client')
    _, second = _private_runtime(tmp_path, selected, 'selected-client')
    module = bridge()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    with project_context('default', tmp_path):
        default_client = module.configured_client()
    with project_context(identifier, tmp_path):
        selected_client = module.configured_client()
    assert default_client.base_url == first['api_url'] + '/api'
    assert selected_client.base_url == second['api_url'] + '/api'
    assert default_client.ui_url == first['ui_url'] != second['ui_url']
    assert selected_client.ui_url == second['ui_url']


def test_a_stale_instance_connection_cannot_send_credentials_to_an_unowned_port(tmp_path, monkeypatch):
    _private_runtime(tmp_path, tmp_path, 'owned-client')
    module = bridge()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    client = module.configured_client()
    monkeypatch.setattr(module.httpx, 'Client', lambda **kwargs: pytest.fail('Credentials reached an unowned listener'))
    with pytest.raises(module.BeefTVError, match='尚未启动'):
        client.request('GET', '/ops')
    with pytest.raises(module.BeefTVError, match='尚未启动'):
        client.download_resource('owned-resource', tmp_path / 'video.mp4')
    assert not (tmp_path / 'video.mp4').exists()


def test_copying_another_ips_client_file_cannot_rebind_the_current_runtime(tmp_path, monkeypatch):
    identifier = '9cf7c562af154245'
    selected = tmp_path / 'projects' / identifier
    selected.mkdir(parents=True)
    (tmp_path / 'projects/_registry.json').write_text(json.dumps([{'id': 'default'}, {'id': identifier}]))
    first, _ = _private_runtime(tmp_path, tmp_path, 'default-client')
    second, _ = _private_runtime(tmp_path, selected, 'selected-client')
    first.connection.write_bytes(second.connection.read_bytes())
    module = bridge()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    with pytest.raises(module.BeefTVError, match='当前 IP'):
        module.configured_client()


@pytest.mark.parametrize('field,value', [('id', 'c' * 32), ('type', 'video'),
    ('projectId', 'another-canvas'), ('clientOperationId', 'assistant-render:another-operation')])
def test_an_old_task_response_still_has_to_match_its_owned_receipt(tmp_path, monkeypatch, field, value):
    module, project, receipt, _, _ = _migration_binding(tmp_path, monkeypatch)
    task = {'id': receipt['task_id'], 'type': 'timeline_render', 'projectId': 'owned-canvas',
            'clientOperationId': 'assistant-render:owned-render', 'status': 'running'}
    task[field] = value
    monkeypatch.setattr(module, 'configured_client', lambda: module.BeefTVClient(
        'http://127.0.0.1:33001/api', 'new', 'fixture', transport=httpx.MockTransport(lambda request: httpx.Response(404))))
    monkeypatch.setattr(module, 'legacy_client', lambda repository=None: module.BeefTVClient(
        'http://127.0.0.1:8842/api', 'old', 'fixture', transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'data': task}))))
    with pytest.raises(module.BeefTVError, match='无法确认原渲染任务身份'):
        module.render_status(project, receipt)


def test_a_legacy_source_client_refuses_all_mutating_requests_before_network():
    client = bridge().BeefTVClient('http://127.0.0.1:8842/api', 'historical', 'fixture', read_only=True,
        transport=httpx.MockTransport(lambda request: pytest.fail('A legacy mutation was dispatched')))
    with pytest.raises(bridge().BeefTVError, match='仅用于读取'):
        client.request('POST', '/tasks/owned/cancel')
    with pytest.raises(bridge().BeefTVError, match='仅用于读取'):
        client.create_canvas({'id': 'owned', 'title': 'must not create'})
