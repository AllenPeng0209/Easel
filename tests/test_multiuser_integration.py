"""Real application authentication and per-IP authorization, with isolated data."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from easel.access import AccessStore
from web import app as web


def test_unconfigured_platform_does_not_start_unowned_background_automation(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, '_InteractionStore', lambda *args: pytest.fail('Background automation started before administrator setup'))
    web._interaction_tick()


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(web, 'PROFILES_DIR', tmp_path / 'profiles')
    monkeypatch.setattr(web, 'BROWSER_PROFILES', tmp_path / 'browser')
    monkeypatch.setattr(web, 'SESSIONS_DIR', tmp_path / 'outputs' / '_sessions')
    monkeypatch.setattr(web, 'SCHEDULE_FILE', tmp_path / 'outputs' / '_schedule.json')
    env = tmp_path / '.env'
    env.write_text('EASEL_CHAT_TRANSPORT=api\nEASEL_DIRECT_API_BASE_URL=http://localhost:9/v1\nEASEL_DIRECT_API_MODEL=test\n')
    monkeypatch.setattr(web, 'ENV_FILE', env)
    def client():
        return TestClient(web.app, base_url='http://localhost:7860',
                          client=('127.0.0.1', 50000), headers={'Origin': 'http://localhost:7860'})
    owner = client()
    response = owner.post('/api/auth/setup', json={'username': 'owner', 'display_name': '主理人', 'password': 'owner-pass-for-tests'})
    assert response.status_code == 200, response.text
    users = {}
    for name, role in [('operator', 'operator'), ('viewer', 'viewer')]:
        created = owner.post('/api/users', json={'username': name, 'display_name': name, 'password': name + '-pass-for-tests'})
        assert created.status_code == 200, created.text
        user = created.json()
        assert owner.post('/api/projects/default/members', json={'user_id': user['id'], 'role': role}).status_code == 200
        session = client()
        assert session.post('/api/auth/login', json={'username': name, 'password': name + '-pass-for-tests'}).status_code == 200
        users[name] = (session, user)
    return tmp_path, owner, users, client


def test_real_roles_filter_ip_list_and_gate_all_default_aliases(environment):
    root, owner, users, make_client = environment
    operator, _ = users['operator']; viewer, _ = users['viewer']
    assert make_client().get('/api/schedule').status_code == 401
    assert make_client().get('/api/accounts', headers={'X-User-Id': owner.get('/api/auth/me').json()['id'], 'X-Role': 'owner'}).status_code == 401
    assert viewer.get('/api/projects').json()[0]['role'] == 'viewer'
    assert operator.post('/api/schedule', json={'title': '运营排期', 'date': '2026-11-05'}).status_code == 200
    assert viewer.get('/api/schedule').json()[0]['title'] == '运营排期'
    for endpoint, payload in [('/api/schedule', {'title': '不可写'}), ('/api/chat/stream', {'message':'不能运行'}),
                              ('/api/upload/local', {}), ('/api/skill', {'skill': 'anything', 'input':'run'})]:
        assert viewer.post(endpoint, json=payload).status_code == 403
    for endpoint in ['/api/login/douyin', '/api/logout/douyin', '/api/accounts/wechat-oa/credentials']:
        assert operator.post(endpoint, json={}).status_code == 403
    assert operator.patch('/api/projects/default', json={'name':'越权修改'}).status_code == 403
    assert operator.get('/api/settings/models').status_code == 403
    assert operator.post('/api/upload/local', json={}).status_code == 422
    assert operator.post('/api/skill', json={'skill':'anything','input':'run'}).status_code == 403
    private = owner.post('/api/projects', json={'name':'另一个 IP'}).json()
    assert len(operator.get('/api/projects').json()) == 1
    assert operator.get(f"/api/projects/{private['id']}/schedule").status_code in (403,404)


def test_role_changes_take_effect_on_next_request_and_last_owner_is_protected(environment):
    root, owner, users, _ = environment
    operator, user = users['operator']
    assert owner.patch(f"/api/projects/default/members/{user['id']}", json={'role':'viewer'}).status_code == 200
    assert operator.post('/api/schedule', json={'title':'已撤销写权限'}).status_code == 403
    assert owner.delete(f"/api/projects/default/members/{user['id']}").status_code == 200
    assert operator.get('/api/schedule').status_code in (403,404)
    own_id = owner.get('/api/auth/me').json()['id']
    assert owner.patch(f'/api/projects/default/members/{own_id}', json={'role':'viewer'}).status_code == 409


def test_different_users_cannot_replay_each_others_conversations(environment):
    root, owner, users, _ = environment
    operator, operator_user = users['operator']
    from easel.request_identity import actor_context
    own = owner.get('/api/auth/me').json()
    with actor_context(own):
        web._save_turn('web:same-session', 'done', 'owner-private')
    with actor_context(operator_user):
        web._save_turn('web:same-session', 'done', 'operator-private')
    assert owner.get('/api/chat/last/same-session').json()['text'] == 'owner-private'
    assert operator.get('/api/chat/last/same-session').json()['text'] == 'operator-private'


def test_executable_media_is_sandboxed_and_system_files_are_not_readable(environment):
    root, owner, users, _ = environment
    viewer, _ = users['viewer']
    outputs = root / 'outputs'
    (outputs / 'topic').mkdir(parents=True)
    (outputs / 'topic' / 'page.html').write_text('<script>fetch("/api/schedule")</script>')
    (outputs / '_sessions').mkdir(exist_ok=True)
    (outputs / '_sessions' / 'secret.json').write_text('{"text":"private"}')
    response = viewer.get('/api/media/topic/page.html')
    assert response.status_code == 200
    assert 'sandbox' in response.headers.get('content-security-policy', '')
    assert 'allow-same-origin' not in response.headers['content-security-policy']
    assert response.headers['x-content-type-options'] == 'nosniff'
    for path in ['/api/media/_sessions/secret.json', '/api/output/topic/%2E%2E/_sessions/secret.json']:
        assert viewer.get(path).status_code == 403


def test_alias_files_cannot_expose_owner_only_login_data(environment):
    root, owner, users, _ = environment
    outputs = root / 'outputs'
    (outputs / '_login').mkdir(parents=True)
    (outputs / '_login' / 'qr.png').write_bytes(b'owner-login-only')
    (outputs / 'topic').mkdir()
    (outputs / 'topic' / 'alias.png').symlink_to(outputs / '_login' / 'qr.png')
    for name in ['operator', 'viewer']:
        assert users[name][0].get('/api/media/topic/alias.png').status_code == 403
    assert owner.get('/api/media/topic/alias.png').content == b'owner-login-only'


def test_content_tree_does_not_follow_foreign_ip_directories_or_cycles(environment):
    root, owner, users, _ = environment
    outputs = root / 'outputs'
    (outputs / 'topic').mkdir(parents=True)
    other = root / 'projects' / 'not-my-ip' / 'outputs'
    other.mkdir(parents=True)
    (other / 'secret-content-title.md').write_text('private')
    (outputs / 'topic' / 'foreign').symlink_to(other, target_is_directory=True)
    response = users['viewer'][0].get('/api/outputs')
    assert response.status_code == 200
    assert 'secret-content-title' not in response.text
    (outputs / 'topic' / 'cycle').symlink_to(outputs / 'topic', target_is_directory=True)
    assert users['viewer'][0].get('/api/outputs').status_code == 200


def test_operator_chat_is_always_tool_free_and_uses_private_history(environment, monkeypatch):
    root, owner, users, _ = environment
    operator, _ = users['operator']
    captured = []
    async def safe_stream(settings, content, **options):
        captured.append(options['history_file'])
        yield 'token', '安全对话'
    monkeypatch.setattr(web, 'stream_chat', safe_stream)
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime':'openclaw','models':{},'sandbox':'workspace-write'})
    def unsafe(*args, **kwargs):
        pytest.fail('Team member chat must never spawn a host Agent')
    monkeypatch.setattr(web.subprocess, 'Popen', unsafe)
    result = operator.post('/api/chat', json={'message':'帮我写一个标题', 'sessionId':'same-session'})
    assert result.status_code == 200, result.text
    assert result.json()['response'] == '安全对话'
    assert 'users' in captured[0].parts


def test_former_admin_cannot_approve_an_old_host_tool_question(environment):
    import asyncio
    root, owner, users, make_client = environment
    original = owner.get('/api/auth/me').json()
    backup = owner.post('/api/users', json={'username':'backup-admin','password':'backup-admin-tests','is_admin':True}).json()
    admin = make_client()
    assert admin.post('/api/auth/login', json={'username':'backup-admin','password':'backup-admin-tests'}).status_code == 200
    from easel.request_identity import actor_context
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    with actor_context(original):
        web._HARNESS_QUESTIONS['harness_old-permission'] = {'future':future,'status':'pending',
            'questions':[{'questionId':'confirm'}]}
    assert admin.patch(f"/api/users/{original['id']}", json={'is_admin':False}).status_code == 200
    assert owner.post('/api/auth/login', json={'username':'owner','password':'owner-pass-for-tests'}).status_code == 200
    result = owner.post('/api/chat/question/answer', json={'questionId':'harness_old-permission','answers':{'confirm':['yes']}})
    assert result.status_code == 403
    assert not future.done()
    with actor_context(original):
        web._HARNESS_QUESTIONS.pop('harness_old-permission', None)
    loop.close()
