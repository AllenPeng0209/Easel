"""Exercise the actual vendored Huobao server and its native-workshop bridge."""
import importlib.util

import pytest

from easel.video_workshop import WorkshopStore
from tests.test_native_workshop import media


@pytest.fixture
def engine(tmp_path):
    assert importlib.util.find_spec('easel.huobao_runtime'), 'The upstream runtime has not been connected'
    from easel.huobao_runtime import HuobaoRuntime
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    runtime = HuobaoRuntime(workspace, workspace, 'default', directory=tmp_path / 'private-engine')
    runtime.ensure()
    yield runtime
    runtime.stop()


def test_original_huobao_workbench_receives_the_real_script_and_shots(engine):
    store = WorkshopStore(engine.workspace / 'outputs')
    project = store.create({'title': '原版火宝工作台', 'brief': '原始小说内容', 'aspect_ratio': '9:16'})
    project = store.production(project['id'], 1, {'script': '已完成的拍摄剧本', 'assets': [], 'shots': [
        {'id': 'shot-01', 'title': '开场', 'prompt': '母亲放下手机', 'seconds': 5, 'asset_ids': []}]})
    binding = engine.open(project['id'])
    drama = engine.request('GET', '/api/v1/dramas/' + str(binding['drama_id']))['data']
    assert drama['title'] == '原版火宝工作台'
    assert drama['episodes'][0]['script_content'] == '已完成的拍摄剧本'
    shots = engine.request('GET', f"/api/v1/episodes/{binding['episode_id']}/storyboards")['data']
    assert shots[0]['video_prompt'] == '母亲放下手机'
    assert len(store.list()) == 1


def test_changes_in_original_huobao_ui_sync_back_without_creating_another_project(engine):
    store = WorkshopStore(engine.workspace / 'outputs')
    project = store.create({'title': '双向同步', 'aspect_ratio': '9:16'})
    project = store.production(project['id'], 1, {'script': '原剧本', 'assets': [], 'shots': [
        {'id': 'stable-shot', 'title': '原镜头', 'prompt': '原提示词', 'seconds': 5, 'asset_ids': []}]})
    binding = engine.open(project['id'])
    shot_id = next(iter(binding['shot_map'].values()))
    engine.request('PUT', '/api/v1/storyboards/' + str(shot_id), {
        'description': '人工编辑的画面描述', 'atmosphere': '自然窗光', 'video_prompt': '人工编辑的提示词'})
    engine.pull(project['id'])
    current = store.get(project['id'])
    assert current['shots'][0]['id'] == 'stable-shot'
    assert current['shots'][0]['prompt'] == '人工编辑的提示词'
    assert current['shots'][0]['description'] == '人工编辑的画面描述'
    assert current['revision'] > project['revision']


def test_skill_edits_are_seen_by_the_original_huobao_backend(engine):
    store = WorkshopStore(engine.workspace / 'outputs')
    project = store.create({'title': '对话继续制作'})
    binding = engine.open(project['id'])
    store.production(project['id'], 1, {'script': 'Skill 更新的剧本', 'assets': [], 'shots': []})
    engine.open(project['id'])
    drama = engine.request('GET', '/api/v1/dramas/' + str(binding['drama_id']))['data']
    assert drama['episodes'][0]['script_content'] == 'Skill 更新的剧本'


def test_existing_native_candidates_and_final_are_imported_into_real_huobao_history(engine, media):
    import shutil
    folder = engine.workspace / 'outputs' / 'sources'
    folder.mkdir(parents=True)
    shutil.copy2(media, folder / 'one.mp4')
    store = WorkshopStore(engine.workspace / 'outputs')
    project = store.create({'title': '保留已有成片', 'aspect_ratio': '1:1'})
    project = store.production(project['id'], 1, {'script': '剧本', 'assets': [], 'shots': [
        {'id': 'one', 'title': '第一个镜头', 'prompt': '母亲放下手机', 'seconds': 1, 'asset_ids': []}]})
    project = store.add_candidate(project['id'], project['revision'], 'one', 'sources/one.mp4')
    project = store.deliver(project['id'], project['revision'], 'sources/one.mp4')
    binding = engine.open(project['id'])
    tasks = engine.request('GET', f"/api/v1/tasks?type=video&drama_id={binding['drama_id']}")['data']
    merges = engine.request('GET', f"/api/v1/merge/episodes/{binding['episode_id']}/merges")['data']
    assert len(tasks) == 1 and tasks[0]['status'] == 'completed'
    assert len(merges) == 1 and merges[0]['status'] == 'completed'


def test_upstream_generation_uses_the_native_gateway_and_authenticated_download(engine, media):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    import threading
    import time
    seen = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps({'id': 'video_' + 'a' * 32, 'status': 'queued'}).encode())
        def do_GET(self):
            if self.headers.get('Authorization') != 'Bearer fixture-key':
                self.send_response(401); self.end_headers(); return
            self.send_response(200)
            if self.path.endswith('/content'):
                self.send_header('Content-Type', 'video/mp4'); self.end_headers(); self.wfile.write(media.read_bytes())
            else:
                self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({'id': 'video_' + 'a' * 32, 'status': 'completed', 'video_url': self.path + '/content'}).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        store = WorkshopStore(engine.workspace / 'outputs')
        project = store.create({'title': '原版任务生成', 'aspect_ratio': '9:16'})
        project = store.production(project['id'], 1, {'script': '剧本', 'assets': [], 'shots': [
            {'id': 'one', 'title': '开场', 'prompt': '母亲放下手机', 'seconds': 5, 'asset_ids': []}]})
        binding = engine.open(project['id'])
        with engine.database() as db:
            config = db.execute("INSERT INTO ai_service_configs(service_type,provider,name,base_url,api_key,model,is_active,created_at,updated_at) VALUES('video','minimax','测试通道',?,'fixture-key','[\"pika-h3-flex-6step\"]',1,'now','now')",
                (f'http://127.0.0.1:{server.server_port}/v1',)).lastrowid
        result = engine.request('POST', '/api/v1/tasks', {'type': 'video', 'storyboard_id': binding['shot_map']['one'],
            'config_id': config, 'model': 'pika-h3-flex-6step', 'prompt': '母亲放下手机', 'duration': 5, 'aspect_ratio': '9:16', 'resolution': '480p'})
        task_id = result['data']['id']
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            task = engine.request('GET', '/api/v1/tasks/' + str(task_id))['data']
            if task['status'] in {'completed', 'failed'}: break
            time.sleep(.1)
        assert task['status'] == 'completed', task
        assert len(seen) == 1 and seen[0]['model'] == 'pika-h3-flex-6step'
        assert seen[0]['request_id'].endswith(':task-' + str(task_id))
        engine.pull(project['id'])
        assert len(store.get(project['id'])['shots'][0]['candidates']) == 1
        current = store.get(project['id'])
        store.production(project['id'], current['revision'], {'script': '对话继续修改剧本', 'assets': [],
            'shots': [{key: current['shots'][0][key] for key in ['id','title','prompt','seconds','asset_ids']}]})
        engine.open(project['id'])
        rows = engine.request('GET', f"/api/v1/episodes/{binding['episode_id']}/storyboards")['data']
        assert rows[0]['video_url'] == task['localPath'], 'Sync must preserve upstream history selection identity'
        merged = engine.request('POST', f"/api/v1/merge/episodes/{binding['episode_id']}/merge", {'storyboard_ids':[binding['shot_map']['one']]})
        assert merged['data']['merge_id']
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            final = engine.request('GET', f"/api/v1/merge/episodes/{binding['episode_id']}/merge")['data']
            if final['status'] in {'completed','failed'}:break
            time.sleep(.1)
        assert final['status'] == 'completed', final
    finally:
        server.shutdown(); server.server_close()


def test_proxy_hides_credentials_without_hiding_configured_model_choices():
    from web.huobao_bridge import _redact
    result = _redact({'api_key': 'real-secret', 'model': ['pika-h3-flex-6step'], 'max_tokens': 1000})
    assert result['api_key'] and result['api_key'] != 'real-secret'
    assert result['model'] == ['pika-h3-flex-6step']
    assert result['max_tokens'] == 1000


@pytest.mark.access_integration
def test_original_agent_buttons_use_existing_skill_host_and_keep_publish_separate(artifact_http_environment, monkeypatch):
    import json
    import time
    import web.app as web
    from web.huobao_bridge import runtime
    from easel.projects import project_context
    from easel.request_identity import actor_context
    root, owner, members, _, _ = artifact_http_environment
    project = WorkshopStore(root / 'outputs').create({'title': '原版 AI 改写', 'brief': '待改写的小说'})
    monkeypatch.setattr(web, '_chat_backend', lambda _: 'codex')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'workspace-write'})
    calls = []
    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('huobao-native-agent')
            assert request.publish is None
            calls.append(request)
            folder = request.cwd / 'outputs' / 'edit'
            folder.mkdir(exist_ok=True)
            current = WorkshopStore(request.cwd / 'outputs').get(project['id'])
            (folder / 'production.json').write_text(json.dumps({'workshopId': project['id'],
                'expectedRevision': current['revision'], 'production': {'script': '由原有 Skill 完成的改写剧本', 'assets': [], 'shots': []}}))
            await request.workshop_delivery({'action': 'update', 'manifestPath': 'edit/production.json'})
            emit('token', '改写已保存')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    route = '/api/workshop/projects/' + project['id']
    engine = None
    try:
        with owner:
            opened = owner.post(route + '/engine/open', json={'expected_revision': 1, 'theme': 'dark'})
            assert opened.status_code == 200, opened.text
            assert '/huobao-studio/drama/' in opened.json()['url']
            with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
                engine = runtime(web)
                binding = engine.bindings()[project['id']]
            response = owner.post(route + '/engine/api/v1/agent/script_rewriter/chat', json={
                'drama_id': binding['drama_id'], 'episode_id': binding['episode_id'], 'message': '改写剧本'})
            assert response.status_code == 200 and response.json()['xiangyaJob']
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                jobs = owner.get(route + '/actions').json()['jobs']
                if jobs[0]['status'] == 'completed': break
                time.sleep(.01)
            assert jobs[0]['status'] == 'completed', jobs
            data = owner.get(route + '/engine/api/v1/dramas/' + str(binding['drama_id'])).json()['data']
            assert data['episodes'][0]['script_content'] == '由原有 Skill 完成的改写剧本'
            assert len(calls) == 1
            assert members['viewer'].post(route + '/engine/api/v1/storyboards', json={}).status_code == 403
            configs = owner.get(route + '/engine/api/v1/ai-configs').json()['data']
            assert engine.state['token'] not in json.dumps(configs)
    finally:
        if engine is not None: engine.stop()


from tests.test_chat_artifacts import artifact_http_environment


@pytest.mark.access_integration
def test_upstream_extract_all_can_queue_all_three_asset_types(artifact_http_environment, monkeypatch):
    import asyncio
    import json
    import time
    import web.app as web
    from web.huobao_bridge import runtime
    from easel.projects import project_context
    from easel.request_identity import actor_context
    root, owner, _, _, _ = artifact_http_environment
    store = WorkshopStore(root / 'outputs')
    project = store.create({'title': '一键提取三类资产'})
    project = store.production(project['id'], 1, {'script': '母亲在客厅把手机放下。', 'assets': [], 'shots': []})
    monkeypatch.setattr(web, '_chat_backend', lambda _: 'codex')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'workspace-write'})
    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('extract-native')
            await asyncio.sleep(.1)
            target = next(target for target in ['characters', 'scenes', 'props'] if '本次仅提取 ' + target in request.prompt)
            current = store.get(project['id'])
            folder = request.cwd / 'outputs' / target
            folder.mkdir(exist_ok=True)
            asset = {'id': target, 'kind': {'characters':'character','scenes':'scene','props':'prop'}[target], 'name': target, 'path': ''}
            (folder / 'edit.json').write_text(json.dumps({'workshopId': project['id'], 'expectedRevision': current['revision'],
                'production': {'script': current['script'], 'assets': [{key: row[key] for key in ['id','kind','name','path']} for row in current['assets']] + [asset], 'shots': []}}))
            await request.workshop_delivery({'action':'update','manifestPath':target+'/edit.json'})
            emit('token', '已提取')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    engine = None
    try:
        with owner:
            route = '/api/workshop/projects/' + project['id']
            assert owner.post(route + '/engine/open', json={'expected_revision':2}).status_code == 200
            with actor_context(owner.get('/api/auth/me').json()), project_context('default',root):
                engine = runtime(web)
                binding = engine.bindings()[project['id']]
            for target in ['characters','scenes','props']:
                response = owner.post(route + f"/engine/api/v1/episodes/{binding['episode_id']}/extract", json={'target':target})
                assert response.status_code == 200, response.text
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                jobs = owner.get(route+'/actions').json()['jobs']
                if len(jobs)==3 and all(row['status']=='completed' for row in jobs):break
                time.sleep(.02)
            assert len(jobs)==3 and all(row['status']=='completed' for row in jobs), jobs
            assert {row['kind'] for row in store.get(project['id'])['assets']} == {'character','scene','prop'}
    finally:
        if engine is not None:engine.stop()
