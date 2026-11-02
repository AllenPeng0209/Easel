"""D01 explicit file declarations preserve the authenticated session boundary."""
from pathlib import Path
import secrets

import pytest


@pytest.fixture
def artifact_environment(tmp_path):
    workspace = tmp_path / 'workspace'
    outputs = workspace / 'outputs'
    outputs.mkdir(parents=True)
    return workspace, outputs, tmp_path / 'private-sessions'


def store_for(environment, *, project='default'):
    from easel.chat_artifacts import ChatArtifactStore
    workspace, outputs, sessions = environment
    return ChatArtifactStore(sessions, outputs, workspace, project)


def file_in(environment, path, content=b'file'):
    full = environment[1] / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_bytes(content)
    return full


def legacy_store_for(environment, files, *, session_id='s', turn_id='t', project='default'):
    """Seed genuine pre-D08 rows/schema, then exercise their real migration."""
    import json
    import mimetypes
    import sqlite3
    import uuid
    from contextlib import closing
    from easel.chat_artifacts import ChatArtifactStore
    workspace, outputs, sessions = environment
    sessions.mkdir(parents=True, exist_ok=True)
    values = []
    with closing(sqlite3.connect(sessions / 'chat_artifacts.sqlite3')) as connection:
        connection.execute('CREATE TABLE artifacts (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, '
            'turn_id TEXT NOT NULL,path TEXT NOT NULL,position INTEGER NOT NULL,payload TEXT NOT NULL,'
            'UNIQUE(session_id,turn_id,path))')
        for position, value in enumerate(files):
            path = Path(value['path'])
            relative = path.relative_to(outputs) if path.is_absolute() else Path(*path.parts[1:])
            identifier = uuid.uuid4().hex
            metadata = {'artifactId': identifier, 'projectId': project, 'sessionId': session_id,
                'turnId': turn_id, 'path': relative.as_posix(), 'workspacePath': 'outputs/' + relative.as_posix(),
                'name': relative.name, 'mime': mimetypes.guess_type(relative.name)[0] or 'application/octet-stream',
                'size': (outputs / relative).stat().st_size, 'status': 'available', 'createdAt': 1,
                'url': f'/api/chat/artifacts/{identifier}/content'}
            if 'description' in value:
                metadata['description'] = value['description']
            connection.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',
                (identifier, session_id, turn_id, relative.as_posix(), position, json.dumps(metadata)))
            values.append(identifier)
        connection.commit()
    store = ChatArtifactStore(sessions, outputs, workspace, project)
    return store, [store.get(identifier) for identifier in values]


def test_explicit_declaration_survives_reload_without_prose_links(artifact_environment):
    from easel.chat_artifacts import ChatArtifactStore
    full = file_in(artifact_environment, 'topic/报告.txt', b'')
    store = store_for(artifact_environment)
    metadata = store.present([{'path': str(full), 'description': '可编辑报告'}],
                             session_id='session', turn_id='turn', call_id='present-call')[0]
    assert metadata['path'] == 'topic/报告.txt'
    assert metadata['name'] == '报告.txt'
    assert metadata['size'] == 0
    assert metadata['mime'] == 'text/plain'
    assert metadata['projectId'] == 'default'
    assert metadata['sessionId'] == 'session'
    assert metadata['turnId'] == 'turn'
    assert metadata['status'] == 'available'
    assert len(metadata['artifactId']) == 32
    assert int(metadata['artifactId'], 16) >= 0
    assert metadata['url'] == f"/api/chat/artifacts/{metadata['artifactId']}/content"
    assert str(artifact_environment[0]) not in str(metadata)
    workspace, outputs, sessions = artifact_environment
    assert ChatArtifactStore(sessions, outputs, workspace, 'default').get(metadata['artifactId']) == metadata


def test_repeat_declaration_uses_stable_identity_last_description_first_order(artifact_environment):
    file_in(artifact_environment, 'topic/a.txt')
    file_in(artifact_environment, 'topic/b.txt')
    store = store_for(artifact_environment)
    first = store.present([{'path': 'outputs/topic/a.txt', 'description': '旧说明'}, {'path': 'outputs/topic/b.txt'}],
                          session_id='s', turn_id='t', call_id='call-1')
    changed = store.present([{'path': 'outputs/topic/a.txt', 'description': '新说明'}],
                            session_id='s', turn_id='t', call_id='call-2')
    assert changed[0]['artifactId'] == first[0]['artifactId']
    assert changed[0]['description'] == '新说明'
    assert changed[0]['createdAt'] == first[0]['createdAt']
    assert [item['name'] for item in store.for_turn('s', 't')] == ['a.txt', 'b.txt']


def test_entire_batch_is_validated_before_any_success_is_committed(artifact_environment):
    from easel.chat_artifacts import ArtifactError
    file_in(artifact_environment, 'topic/a.txt')
    store = store_for(artifact_environment)
    with pytest.raises(ArtifactError):
        store.present([{'path': 'outputs/topic/a.txt'}, {'path': 'outputs/topic/missing.txt'}],
                      session_id='s', turn_id='t', call_id='call')
    assert store.for_turn('s', 't') == []


@pytest.mark.parametrize('path', [
    '../secret.txt', 'outputs/../../secret.txt', 'https://example.test/report.pdf',
    'outputs/_sessions/private.json', 'outputs/_login/qr.png', 'outputs/_analytics/stats.json',
    'outputs/_debug/run.log', 'outputs/_harness/config.json', 'outputs/topic/.env',
    'outputs/topic/auth.json', 'outputs/topic/cookies.json', 'outputs/topic/private.pem',
    'outputs/topic/credentials.json', 'outputs/_publish/private.json', 'outputs/topic\\secret.txt',
])
def test_private_and_unsafe_paths_cannot_become_deliverables(artifact_environment, path):
    from easel.chat_artifacts import ArtifactError
    with pytest.raises(ArtifactError) as caught:
        store_for(artifact_environment).present([{'path': path}], session_id='s', turn_id='t', call_id='call')
    assert caught.value.status_code in (400, 403)


def test_external_absolute_path_directory_and_symlinks_are_rejected(artifact_environment):
    from easel.chat_artifacts import ArtifactError
    workspace, outputs, _ = artifact_environment
    secret = workspace.parent / 'outside.txt'
    secret.write_bytes(b'private')
    file_in(artifact_environment, 'topic/source.txt')
    (outputs / 'topic' / 'link.txt').symlink_to(outputs / 'topic' / 'source.txt')
    (outputs / 'alias').symlink_to(secret.parent, target_is_directory=True)
    for path in [str(secret), 'outputs/topic', 'outputs/topic/link.txt', 'outputs/alias/outside.txt']:
        with pytest.raises(ArtifactError):
            store_for(artifact_environment).present([{'path': path}], session_id='s', turn_id='t', call_id='call')


def test_manual_export_zip_and_current_session_inbox_are_allowed(artifact_environment):
    from easel.chat_artifacts import ArtifactError
    file_in(artifact_environment, '_publish/package.zip', b'PK')
    file_in(artifact_environment, '_inbox/current-scope/batch/photo.png', b'png')
    store = store_for(artifact_environment)
    result = store.present([{'path': 'outputs/_publish/package.zip'}, {'path': 'outputs/_inbox/current-scope/batch/photo.png'}],
                           session_id='s', turn_id='t', call_id='call', inbox_scope='current-scope')
    assert len(result) == 2
    with pytest.raises(ArtifactError) as caught:
        store.present([{'path': 'outputs/_inbox/current-scope/batch/photo.png'}],
                      session_id='other', turn_id='t2', call_id='call', inbox_scope='other-scope')
    assert caught.value.status_code == 403


def test_content_open_rechecks_replaced_symlink_and_never_serves_external_bytes(artifact_environment):
    from easel.chat_artifacts import ArtifactError
    full = file_in(artifact_environment, 'topic/file.txt', b'original')
    store, records = legacy_store_for(artifact_environment, [{'path': str(full)}])
    artifact = records[0]
    external = artifact_environment[0].parent / 'external.txt'
    external.write_bytes(b'private')
    full.unlink()
    full.symlink_to(external)
    with pytest.raises(ArtifactError):
        store.open_content(artifact['artifactId'])


def test_legacy_file_content_is_current_file_not_an_immutable_snapshot(artifact_environment):
    full = file_in(artifact_environment, 'topic/file.txt', b'original')
    store, records = legacy_store_for(artifact_environment, [{'path': str(full)}])
    artifact = records[0]
    full.write_bytes(b'updated')
    with store.open_content(artifact['artifactId']) as stream:
        assert stream.read() == b'updated'


def test_catalog_lookup_is_private_even_when_business_outputs_are_shared(artifact_environment):
    from easel.chat_artifacts import ChatArtifactStore
    full = file_in(artifact_environment, 'topic/file.txt')
    first = store_for(artifact_environment)
    artifact = first.present([{'path': str(full)}], session_id='same', turn_id='same', call_id='call')[0]
    workspace, outputs, sessions = artifact_environment
    second = ChatArtifactStore(sessions / 'other-user', outputs, workspace, 'default')
    assert second.get(artifact['artifactId']) is None


@pytest.fixture
def artifact_http_environment(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from web import app as web
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'outputs' / '_sessions')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    def client():
        return TestClient(web.app, base_url='http://localhost:7860', client=('127.0.0.1', 50000),
                          headers={'Origin': 'http://localhost:7860'})
    owner = client()
    owner_password = secrets.token_urlsafe(24)
    setup = owner.post('/api/auth/setup', json={'username': 'owner', 'password': owner_password})
    assert setup.status_code == 200, setup.text
    members = {}
    for name in ('operator', 'viewer'):
        password = secrets.token_urlsafe(24)
        created = owner.post('/api/users', json={'username': name, 'password': password}).json()
        assert owner.post('/api/projects/default/members', json={'user_id': created['id'], 'role': name}).status_code == 200
        member = client()
        assert member.post('/api/auth/login', json={'username': name, 'password': password}).status_code == 200
        members[name] = member
    from easel.request_identity import actor_context
    from easel.projects import project_context
    from easel.chat_artifacts import ChatArtifactStore
    (tmp_path / 'outputs' / 'topic').mkdir(parents=True)
    (tmp_path / 'outputs' / 'topic' / 'report.txt').write_bytes(b'owned-result')
    (tmp_path / 'outputs' / 'topic' / 'page.html').write_text('<script>document.body.dataset.preview="ready"</script>')
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', tmp_path):
        store, files = legacy_store_for((web._workspace_root(), web._outputs_dir(), web._sessions_dir()),
            [{'path': 'outputs/topic/report.txt'}, {'path': 'outputs/topic/page.html'}],
            session_id='owner-session', turn_id='owner-turn')
    return tmp_path, owner, members, client, files


@pytest.mark.access_integration
def test_artifact_metadata_and_bytes_require_same_user_and_ip(artifact_http_environment):
    _, owner, members, client, files = artifact_http_environment
    artifact = files[0]
    route = '/api/chat/artifacts/' + artifact['artifactId']
    response = owner.get(route)
    assert response.status_code == 200, response.text
    assert response.json() == artifact
    assert owner.get(route + '/content').content == b'owned-result'
    assert client().get(route).status_code == 401
    assert members['operator'].get(route).status_code == 404
    assert members['operator'].get(route + '/content').status_code == 404
    assert members['viewer'].get(route).status_code == 403
    other_ip = owner.post('/api/projects', json={'name': '另一个 IP'}).json()['id']
    assert owner.get(f'/api/projects/{other_ip}' + route.removeprefix('/api')).status_code == 404
    assert members['operator'].get(f'/api/projects/{other_ip}' + route.removeprefix('/api')).status_code == 403


@pytest.mark.access_integration
def test_artifact_content_keeps_sandbox_headers_and_explicit_download(artifact_http_environment):
    _, owner, _, _, files = artifact_http_environment
    route = '/api/chat/artifacts/' + files[1]['artifactId'] + '/content'
    response = owner.get(route)
    assert response.status_code == 200, response.text
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'sandbox' in response.headers['content-security-policy']
    assert 'allow-same-origin' not in response.headers['content-security-policy']
    download = owner.get(route + '?download=1')
    assert download.headers['content-disposition'].startswith('attachment;')
    assert 'page.html' in download.headers['content-disposition']


def test_native_declaration_is_persisted_before_structured_success_event(tmp_path, monkeypatch):
    import asyncio
    from web import app as web
    from easel.chat_artifacts import ChatArtifactStore
    from easel.request_identity import ActorMap
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    (tmp_path / 'outputs' / 'topic').mkdir(parents=True)
    (tmp_path / 'outputs' / 'topic' / 'result.txt').write_bytes(b'final')
    monkeypatch.setattr(web, '_RUNNING_CHAT', ActorMap())
    monkeypatch.setattr(web, '_session_locks', ActorMap())
    monkeypatch.setattr(web, '_chat_backend', lambda session: 'codex')
    events = []
    class Adapter:
        async def run(self, request, emit, ask, bind):
            assert request.present is not None
            declared = await request.present([{'path': 'outputs/topic/result.txt', 'description': '最终结果'}], 'call-present')
            assert declared[0]['path'] == 'topic/result.txt'
            emit('token', 'done')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    def receive(kind, value):
        if kind == 'agent_event' and value.get('kind') == 'artifact':
            stored = ChatArtifactStore(web._sessions_dir(), web._outputs_dir(), web._workspace_root(), 'default')
            assert stored.get(value['artifacts'][0]['artifactId']) == value['artifacts'][0]
        events.append((kind, value))
    asyncio.run(web._run_harness_chat(web.ChatRequest(message='run', sessionId='s', turnId='t'),
        's', 'run', 'codex', {'models': {}, 'sandbox': 'read-only'}, receive))
    artifacts = [value for kind, value in events if kind == 'agent_event' and value.get('kind') == 'artifact']
    assert len(artifacts) == 1
    assert artifacts[0]['status'] == 'available'
    assert artifacts[0]['callId'] == 'call-present'


def test_open_content_keeps_verified_descriptor_if_path_is_replaced_later(artifact_environment):
    full = file_in(artifact_environment, 'topic/file.txt', b'original')
    store, records = legacy_store_for(artifact_environment, [{'path': str(full)}])
    artifact = records[0]
    external = artifact_environment[0].parent / 'external.txt'
    external.write_bytes(b'private')
    with store.open_content(artifact['artifactId']) as stream:
        full.unlink()
        full.symlink_to(external)
        assert stream.read() == b'original'


@pytest.mark.access_integration
def test_native_present_survives_live_stream_replay_snapshot_and_authenticated_content(artifact_http_environment, monkeypatch):
    import json
    from PIL import Image
    from web import app as web
    root, owner, _, _, _ = artifact_http_environment
    Image.new('RGB', (2, 2), color='blue').save(root / 'outputs' / 'topic' / 'preview.png')
    monkeypatch.setattr(web, '_chat_backend', lambda session: 'codex')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'read-only'})
    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('native-artifact-fixture')
            assert request.present is not None
            await request.present([{'path': 'outputs/topic/report.txt', 'description': '报告'},
                                   {'path': 'outputs/topic/preview.png', 'description': '预览图'}], 'present-real-boundary')
            emit('token', '文件已准备好。')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    response = owner.post('/api/chat/stream', json={'message': '交付已有文件',
        'sessionId': 'artifact-session', 'turnId': 'artifact-native-turn'})
    assert response.status_code == 200, response.text
    def structured_events(text):
        result = []
        event = ''
        for line in text.splitlines():
            if line.startswith('event: '):
                event = line[7:]
            elif line.startswith('data: ') and event == 'agent_event':
                result.append(json.loads(line[6:]))
        return result
    events = [event for event in structured_events(response.text) if event['kind'] == 'artifact']
    assert len(events) == 1
    assert [item['name'] for item in events[0]['artifacts']] == ['report.txt', 'preview.png']
    replay = owner.get('/api/chat/jobs/artifact-native-turn/stream')
    assert [event for event in structured_events(replay.text) if event['kind'] == 'artifact'] == events
    snapshot = owner.get('/api/chat/last/artifact-session').json()
    assert [event for event in snapshot['events'] if event['kind'] == 'artifact'] == events
    assert snapshot['text'] == '文件已准备好。'
    image = events[0]['artifacts'][1]
    fetched = owner.get(image['url'])
    assert fetched.status_code == 200
    assert fetched.headers['content-type'].startswith('image/png')
    assert fetched.content == (root / 'outputs' / 'topic' / 'preview.png').read_bytes()


def test_failed_native_declaration_never_emits_or_commits_partial_success(tmp_path, monkeypatch):
    import asyncio
    from web import app as web
    from easel.chat_artifacts import ArtifactError, ChatArtifactStore
    from easel.request_identity import ActorMap
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    (tmp_path / 'outputs' / 'topic').mkdir(parents=True)
    (tmp_path / 'outputs' / 'topic' / 'result.txt').write_bytes(b'final')
    monkeypatch.setattr(web, '_RUNNING_CHAT', ActorMap())
    monkeypatch.setattr(web, '_session_locks', ActorMap())
    monkeypatch.setattr(web, '_chat_backend', lambda session: 'codex')
    events, rejected = [], []
    class Adapter:
        async def run(self, request, emit, ask, bind):
            assert request.present is not None
            try:
                await request.present([{'path': 'outputs/topic/result.txt'}, {'path': 'outputs/topic/missing.txt'}], 'invalid-present')
            except ArtifactError:
                rejected.append(True)
                emit('token', '文件不可交付。')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    asyncio.run(web._run_harness_chat(web.ChatRequest(message='run', sessionId='s', turnId='t'),
        's', 'run', 'codex', {'models': {}, 'sandbox': 'read-only'}, lambda kind, value: events.append((kind, value))))
    assert rejected == [True]
    assert not [value for kind, value in events if kind == 'agent_event' and value.get('kind') == 'artifact']
    assert ChatArtifactStore(web._sessions_dir(), web._outputs_dir(), web._workspace_root(), 'default').for_turn('s', 't') == []


@pytest.mark.parametrize('failure_stage', ['insert', 'initialization'])
def test_real_sqlite_save_failure_has_safe_error_and_no_success_event(tmp_path, monkeypatch, failure_stage):
    import asyncio
    import sqlite3
    from web import app as web
    from easel.chat_artifacts import ChatArtifactStore
    from easel.harnesses.base import HarnessError
    from easel.request_identity import ActorMap
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'sessions')
    monkeypatch.setattr(web, 'ENV_FILE', tmp_path / '.env')
    (tmp_path / '.env').write_text('')
    (tmp_path / 'outputs' / 'topic').mkdir(parents=True)
    (tmp_path / 'outputs' / 'topic' / 'result.txt').write_bytes(b'final')
    monkeypatch.setattr(web, '_RUNNING_CHAT', ActorMap())
    monkeypatch.setattr(web, '_session_locks', ActorMap())
    monkeypatch.setattr(web, '_chat_backend', lambda session: 'codex')
    store = None
    if failure_stage == 'insert':
        store = ChatArtifactStore(web._sessions_dir(), web._outputs_dir(), web._workspace_root(), 'default')
        with sqlite3.connect(store.path) as connection:
            connection.execute("CREATE TRIGGER fail_save BEFORE INSERT ON artifacts BEGIN SELECT RAISE(ABORT,'fixture insert failure'); END")
    else:
        web._sessions_dir().mkdir(parents=True)
        (web._sessions_dir() / 'chat_artifacts.sqlite3').mkdir()
    events, rejected = [], []
    class Adapter:
        async def run(self, request, emit, ask, bind):
            try:
                await request.present([{'path': 'outputs/topic/result.txt'}], 'failed-save')
            except HarnessError as error:
                rejected.append(str(error))
                emit('token', '交付未保存。')
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    asyncio.run(web._run_harness_chat(web.ChatRequest(message='run', sessionId='s', turnId='t'),
        's', 'run', 'codex', {'models': {}, 'sandbox': 'read-only'}, lambda kind, value: events.append((kind, value))))
    assert len(rejected) == 1
    assert '交付记录保存失败' in rejected[0]
    assert 'fixture insert failure' not in rejected[0]
    assert str(tmp_path) not in rejected[0]
    assert not [value for kind, value in events if kind == 'agent_event' and value.get('kind') == 'artifact']
    if store is not None:
        assert store.for_turn('s', 't') == []


@pytest.mark.parametrize('construct_before_swap', [False, True])
def test_outputs_root_symlink_is_rejected_before_or_after_store_creation(artifact_environment, construct_before_swap):
    from easel.chat_artifacts import ArtifactError
    workspace, outputs, _ = artifact_environment
    foreign = workspace.parent / 'foreign'
    (foreign / 'topic').mkdir(parents=True)
    (foreign / 'topic' / 'private.txt').write_bytes(b'foreign-private')
    store = store_for(artifact_environment) if construct_before_swap else None
    outputs.rename(workspace / 'original-outputs')
    outputs.symlink_to(foreign, target_is_directory=True)
    if store is None:
        store = store_for(artifact_environment)
    with pytest.raises(ArtifactError) as caught:
        store.present([{'path': 'outputs/topic/private.txt'}], session_id='s', turn_id='t', call_id='call')
    assert caught.value.status_code == 403
    assert store.for_turn('s', 't') == []


def test_outputs_root_swapped_between_inspection_and_open_is_not_followed(artifact_environment, monkeypatch):
    from easel.chat_artifacts import ArtifactError
    workspace, outputs, _ = artifact_environment
    file_in(artifact_environment, 'topic/file.txt', b'original')
    foreign = workspace.parent / 'foreign'
    (foreign / 'topic').mkdir(parents=True)
    (foreign / 'topic' / 'file.txt').write_bytes(b'foreign-private')
    store = store_for(artifact_environment)
    original_lstat = Path.lstat
    swapped = []
    def lstat(path, *args, **kwargs):
        result = original_lstat(path, *args, **kwargs)
        if path == outputs / 'topic' / 'file.txt' and not swapped:
            swapped.append(True)
            outputs.rename(workspace / 'original-outputs')
            outputs.symlink_to(foreign, target_is_directory=True)
        return result
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(ArtifactError):
        store.present([{'path': 'outputs/topic/file.txt'}], session_id='s', turn_id='t', call_id='call')
    assert swapped == [True]
    assert store.for_turn('s', 't') == []


def test_native_relative_path_is_based_on_session_cwd_not_guessed_outputs(artifact_environment):
    from easel.chat_artifacts import ArtifactError
    workspace, _, _ = artifact_environment
    (workspace / 'report.txt').write_bytes(b'workspace-file')
    file_in(artifact_environment, 'report.txt', b'different-output-file')
    store = store_for(artifact_environment)
    with pytest.raises(ArtifactError) as caught:
        store.present([{'path': 'report.txt'}], session_id='s', turn_id='t', call_id='wrong')
    assert caught.value.status_code == 403
    assert store.for_turn('s', 't') == []
    correct = store.present([{'path': 'outputs/report.txt'}], session_id='s', turn_id='t', call_id='correct')[0]
    assert correct['path'] == 'report.txt'
    assert correct['workspacePath'] == 'outputs/report.txt'
    with store.open_content(correct['artifactId']) as stream:
        assert stream.read() == b'different-output-file'


def test_optional_wal_initialization_contention_does_not_drop_declaration(artifact_environment, monkeypatch):
    import sqlite3
    original_connect = sqlite3.connect
    class BusyWalConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.strip().upper() == 'PRAGMA JOURNAL_MODE=WAL':
                raise sqlite3.OperationalError('database is locked')
            return super().execute(sql, *args, **kwargs)
    def connect(*args, **kwargs):
        return original_connect(*args, factory=BusyWalConnection, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    file_in(artifact_environment, 'topic/file.txt', b'result')
    store = store_for(artifact_environment)
    artifact = store.present([{'path': 'outputs/topic/file.txt'}], session_id='s', turn_id='t', call_id='call')[0]
    assert store.get(artifact['artifactId']) == artifact
    with store.open_content(artifact['artifactId']) as stream:
        assert stream.read() == b'result'


@pytest.mark.access_integration
@pytest.mark.parametrize('header,want,content_range', [
    ('bytes=2-5', b'ned-', 'bytes 2-5/12'),
    ('bytes=-3', b'ult', 'bytes 9-11/12'),
    ('bytes=6-', b'result', 'bytes 6-11/12'),
    ('bytes=-100', b'owned-result', 'bytes 0-11/12'),
])
def test_content_single_ranges_match_verified_file_bytes(artifact_http_environment, header, want, content_range):
    _, owner, _, _, files = artifact_http_environment
    response = owner.get(files[0]['url'], headers={'Range': header})
    assert response.status_code == 206, response.text
    assert response.content == want
    assert response.headers['content-range'] == content_range
    assert int(response.headers['content-length']) == len(want)
    assert response.headers['accept-ranges'] == 'bytes'
    assert response.headers['x-content-type-options'] == 'nosniff'


@pytest.mark.access_integration
def test_content_unsatisfied_range_and_malformed_range_are_explicit(artifact_http_environment):
    _, owner, _, _, files = artifact_http_environment
    outside = owner.get(files[0]['url'], headers={'Range': 'bytes=99-'})
    assert outside.status_code == 416
    assert outside.headers['content-range'] == 'bytes */12'
    assert outside.content == b''
    malformed = owner.get(files[0]['url'], headers={'Range': 'bytes=5-2'})
    assert malformed.status_code == 400


@pytest.mark.access_integration
def test_if_range_uses_current_file_validator_or_falls_back_to_full_response(artifact_http_environment):
    _, owner, _, _, files = artifact_http_environment
    full = owner.get(files[0]['url'])
    etag = full.headers['etag']
    modified = full.headers['last-modified']
    for validator in (etag, modified):
        partial = owner.get(files[0]['url'], headers={'Range': 'bytes=0-1', 'If-Range': validator})
        assert partial.status_code == 206
        assert partial.content == b'ow'
    changed = owner.get(files[0]['url'], headers={'Range': 'bytes=0-1', 'If-Range': '"different"'})
    assert changed.status_code == 200
    assert changed.content == b'owned-result'
    assert 'content-range' not in changed.headers


@pytest.mark.access_integration
def test_multiple_ranges_are_ignored_without_claiming_multipart_support(artifact_http_environment):
    _, owner, _, _, files = artifact_http_environment
    response = owner.get(files[0]['url'], headers={'Range': 'bytes=0-1,4-5'})
    assert response.status_code == 200
    assert response.content == b'owned-result'
    assert 'content-range' not in response.headers
    assert not response.headers['content-type'].startswith('multipart/')


@pytest.mark.access_integration
def test_content_head_returns_headers_without_reading_bytes_and_closes_handle(artifact_http_environment, monkeypatch):
    from easel.chat_artifacts import ChatArtifactStore
    _, owner, _, _, files = artifact_http_environment
    original = ChatArtifactStore.open_content
    streams = []
    class NoRead:
        def __init__(self, stream):
            self.stream = stream
        def fileno(self):
            return self.stream.fileno()
        def close(self):
            self.stream.close()
        def read(self, *_):
            raise AssertionError('HEAD must not read file bytes')
    def open_content(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        streams.append(stream)
        return NoRead(stream)
    monkeypatch.setattr(ChatArtifactStore, 'open_content', open_content)
    response = owner.head(files[0]['url'])
    assert response.status_code == 200
    assert response.content == b''
    assert response.headers['content-length'] == '12'
    partial = owner.head(files[0]['url'], headers={'Range': 'bytes=2-5'})
    assert partial.status_code == 206
    assert partial.content == b''
    assert partial.headers['content-length'] == '4'
    assert partial.headers['content-range'] == 'bytes 2-5/12'
    outside = owner.get(files[0]['url'], headers={'Range': 'bytes=99-'})
    assert outside.status_code == 416
    malformed = owner.get(files[0]['url'], headers={'Range': 'bytes=5-2'})
    assert malformed.status_code == 400
    assert streams and all(stream.closed for stream in streams)


@pytest.mark.access_integration
def test_partial_content_reads_only_selected_bytes_from_verified_handle(artifact_http_environment, monkeypatch):
    from easel.chat_artifacts import ChatArtifactStore
    _, owner, _, _, files = artifact_http_environment
    original = ChatArtifactStore.open_content
    requests = []
    class BoundedRead:
        def __init__(self, stream):
            self.stream = stream
        def fileno(self):
            return self.stream.fileno()
        def close(self):
            self.stream.close()
        def seek(self, position):
            return self.stream.seek(position)
        def read(self, size):
            requests.append(size)
            assert 0 < size <= 4
            return self.stream.read(size)
    def open_content(self, *args, **kwargs):
        return BoundedRead(original(self, *args, **kwargs))
    monkeypatch.setattr(ChatArtifactStore, 'open_content', open_content)
    response = owner.get(files[0]['url'], headers={'Range': 'bytes=2-5'})
    assert response.status_code == 206
    assert response.content == b'ned-'
    assert requests == [4]


@pytest.fixture
def related_artifacts(artifact_environment):
    file_in(artifact_environment, 'topic/page.html', b'<link rel="stylesheet" href="assets/style.css">')
    file_in(artifact_environment, 'topic/assets/style.css', b'body{color:blue}')
    file_in(artifact_environment, 'topic/assets/app.js', b'window.previewReady=true')
    file_in(artifact_environment, 'public/shared.css', b'.shared{color:red}')
    store, records = legacy_store_for(artifact_environment, [{'path': 'outputs/topic/page.html'}])
    metadata = records[0]
    return artifact_environment, store, metadata


def test_related_asset_is_bound_to_declared_html_and_allows_public_sibling(related_artifacts):
    _, store, metadata = related_artifacts
    assert store.read_related(metadata['artifactId'], 'assets/style.css') == (b'body{color:blue}', 'text/css')
    assert store.read_related(metadata['artifactId'], '../public/shared.css') == (b'.shared{color:red}', 'text/css')
    assert store.read_document_related('topic/page.html', 'assets/app.js')[0] == b'window.previewReady=true'


@pytest.mark.parametrize('path,status', [
    ('../../outside.css', 403), ('/outside.css', 403), ('https://example.test/app.js', 400),
    ('../_login/private.js', 403), ('../_sessions/private.js', 403),
    ('../_debug/private.css', 403), ('../_harness/private.js', 403),
    ('assets/.env.js', 403), ('assets/credentials.js', 403),
    ('assets/settings.json', 415), ('assets/module.mjs', 415), ('assets/font.woff2', 415),
    ('assets/nested.html', 415), ('assets\\app.js', 400),
])
def test_related_reader_rejects_unsafe_paths_and_unapproved_types(related_artifacts, path, status):
    from easel.chat_artifacts import ArtifactError
    _, store, metadata = related_artifacts
    with pytest.raises(ArtifactError) as caught:
        store.read_related(metadata['artifactId'], path)
    assert caught.value.status_code == status


def test_related_reader_requires_live_regular_html_source_and_regular_asset(related_artifacts):
    from easel.chat_artifacts import ArtifactError
    environment, store, metadata = related_artifacts
    outputs = environment[1]
    (outputs / 'topic' / 'directory.css').mkdir()
    (outputs / 'topic' / 'alias.css').symlink_to(outputs / 'topic' / 'assets' / 'style.css')
    for path in ('directory.css', 'alias.css'):
        with pytest.raises(ArtifactError):
            store.read_related(metadata['artifactId'], path)
    (outputs / 'topic' / 'page.html').unlink()
    with pytest.raises(ArtifactError) as caught:
        store.read_related(metadata['artifactId'], 'assets/style.css')
    assert caught.value.status_code == 404
    with pytest.raises(ArtifactError):
        store.read_document_related('topic/assets/app.js', '../assets/style.css')


def test_related_assets_enforce_per_file_limit_from_verified_handle(related_artifacts):
    from easel.chat_artifacts import ArtifactError
    environment, store, metadata = related_artifacts
    file_in(environment, 'topic/assets/exact.css', b'x' * (4 * 1024 * 1024))
    file_in(environment, 'topic/assets/too-large.css', b'x' * (4 * 1024 * 1024 + 1))
    assert len(store.read_related(metadata['artifactId'], 'assets/exact.css')[0]) == 4 * 1024 * 1024
    with pytest.raises(ArtifactError) as caught:
        store.read_related(metadata['artifactId'], 'assets/too-large.css')
    assert caught.value.status_code == 413


def test_related_paths_are_not_url_decoded_a_second_time(related_artifacts):
    environment, store, metadata = related_artifacts
    file_in(environment, 'topic/%2e%2e/ok.css', b'literal-percent-directory')
    file_in(environment, 'ok.css', b'wrong-parent-file')
    assert store.read_related(metadata['artifactId'], '%2e%2e/ok.css')[0] == b'literal-percent-directory'


def test_legacy_related_reader_cannot_use_owner_private_or_inbox_source(artifact_environment):
    from easel.chat_artifacts import ArtifactError
    file_in(artifact_environment, '_login/index.html', b'private')
    file_in(artifact_environment, '_inbox/scope/batch/index.html', b'private')
    store = store_for(artifact_environment)
    for path in ('_login/index.html', '_inbox/scope/batch/index.html'):
        with pytest.raises(ArtifactError) as caught:
            store.read_document_related(path, 'style.css')
        assert caught.value.status_code == 403


def test_legacy_inbox_html_related_assets_stay_in_same_session_scope(artifact_environment):
    from easel.chat_artifacts import ArtifactError
    file_in(artifact_environment, '_inbox/mine/batch/index.html', b'html')
    file_in(artifact_environment, '_inbox/mine/batch/style.css', b'my-css')
    file_in(artifact_environment, '_inbox/other/batch/style.css', b'other-css')
    store, records = legacy_store_for(artifact_environment, [{'path': 'outputs/_inbox/mine/batch/index.html'}])
    metadata = records[0]
    assert store.read_related(metadata['artifactId'], 'style.css', inbox_scope='mine')[0] == b'my-css'
    with pytest.raises(ArtifactError) as caught:
        store.read_related(metadata['artifactId'], '../../other/batch/style.css', inbox_scope='mine')
    assert caught.value.status_code == 403


@pytest.mark.access_integration
def test_related_http_routes_bind_actor_ip_html_and_static_assets(artifact_http_environment):
    root, owner, members, client, files = artifact_http_environment
    directory = root / 'outputs' / 'topic' / 'assets'
    directory.mkdir()
    (directory / 'style.css').write_bytes(b'body{color:blue}')
    (root / 'outputs' / '_login').mkdir()
    (root / 'outputs' / '_login' / 'private.js').write_bytes(b'private')
    route = '/api/chat/artifacts/' + files[1]['artifactId'] + '/related'
    response = owner.get(route, params={'path': 'assets/style.css'})
    assert response.status_code == 200, response.text
    assert response.content == b'body{color:blue}'
    assert response.headers['content-type'].startswith('text/css')
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert client().get(route, params={'path': 'assets/style.css'}).status_code == 401
    assert members['operator'].get(route, params={'path': 'assets/style.css'}).status_code == 404
    assert members['viewer'].get(route, params={'path': 'assets/style.css'}).status_code == 403
    assert owner.get(route, params={'path': '../_login/private.js'}).status_code == 403
    legacy = owner.get('/api/chat/artifact-related', params={'document': 'topic/page.html', 'path': 'assets/style.css'})
    assert legacy.status_code == 200, legacy.text
    assert legacy.content == response.content
    private_source = owner.get('/api/chat/artifact-related', params={'document': '_login/index.html', 'path': '../topic/assets/style.css'})
    assert private_source.status_code == 403
    other_ip = owner.post('/api/projects', json={'name': '资源隔离'}).json()['id']
    assert owner.get(f'/api/projects/{other_ip}' + route.removeprefix('/api'), params={'path': 'assets/style.css'}).status_code == 404


def test_related_photo_names_are_not_mistaken_for_text_credential_files(related_artifacts):
    environment, store, metadata = related_artifacts
    file_in(environment, 'topic/assets/cookies.png', b'photo-bytes')
    content, mime = store.read_related(metadata['artifactId'], 'assets/cookies.png')
    assert content == b'photo-bytes'
    assert mime == 'image/png'


def test_related_growth_after_stat_is_bounded_and_closes_verified_handle(related_artifacts, monkeypatch):
    from easel.chat_artifacts import ArtifactError, MAX_RELATED_BYTES
    environment, store, metadata = related_artifacts
    full = file_in(environment, 'topic/assets/growing.css', b'x')
    original = store._checked_file
    requests, streams = [], []
    class GrowingFile:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def read(self, size):
            requests.append(size)
            assert 0 < size <= MAX_RELATED_BYTES + 1
            full.write_bytes(b'g' * (MAX_RELATED_BYTES + 1))
            return self.stream.read(size)
    def checked(relative):
        stream = original(relative)
        if relative == Path('topic/assets/growing.css'):
            streams.append(stream)
            return GrowingFile(stream)
        return stream
    monkeypatch.setattr(store, '_checked_file', checked)
    with pytest.raises(ArtifactError) as caught:
        store.read_related(metadata['artifactId'], 'assets/growing.css')
    assert caught.value.status_code == 413
    assert requests == [MAX_RELATED_BYTES + 1]
    assert streams and all(stream.closed for stream in streams)
