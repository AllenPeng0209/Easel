"""Exercise the read-only CreatorHub path through Xiangya's real Web API."""
import httpx
import pytest
from fastapi.testclient import TestClient

import web.app as host
from easel.interaction_channels import InteractionChannels


PREFIX = 'EASEL_INTERACTION_DEFAULT_CREATORHUB_PILOT'
ITEM_ID = '7412345678901234567'


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(host, 'OUTPUTS_DIR', tmp_path / 'outputs')
    local = TestClient(host.app, base_url='http://127.0.0.1:7860',
                       headers={'Origin': 'http://127.0.0.1:7860'})
    client_id = local.get('/api/interactions/bootstrap').json()['client_id']
    settings = {
        PREFIX + '_CREATORHUB_CLIENT_ID': client_id,
        PREFIX + '_CREATORHUB_ACCOUNT_ID': '7',
        PREFIX + '_CREATORHUB_WORK_ID': '11',
        PREFIX + '_CREATORHUB_ITEM_ID': ITEM_ID,
    }
    state = {'status': 'active', 'account': 7, 'item': ITEM_ID, 'work_platform': 'douyin',
             'comment_platform': 'douyin', 'unavailable': False, 'media_type': 'video', 'empty': False}
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.method == 'GET'
        assert (request.url.scheme, request.url.host, request.url.port) == ('http', '127.0.0.1', 8000)
        if state['unavailable']:
            return httpx.Response(302, headers={'Location': 'https://example.invalid'})
        if request.url.path == '/api/accounts' and request.url.params['platform'] == 'douyin':
            return httpx.Response(200, json=[{'id': 7, 'platform': 'douyin', 'status': state['status'],
                                              'has_read_login': True}])
        if request.url.path == '/api/account-works' and request.url.params['account_id'] == '7':
            return httpx.Response(200, json=[{'id': 11, 'account_id': state['account'],
                                              'platform': state['work_platform'], 'item_id': state['item'],
                                              'desc': '自有作品', 'media_type': state['media_type']}])
        if request.url.path == '/api/account-works/11/comments':
            if state['empty']:
                return httpx.Response(200, json=[])
            assert request.url.params['limit'] == '200'
            return httpx.Response(200, json=[{'id': 25, 'watch_id': 0, 'platform': state['comment_platform'],
                                              'aweme_id': ITEM_ID, 'comment_id': '9876',
                                              'text': '想了解课程', 'user_nickname': '访客'}])
        return httpx.Response(404)

    # Only the external HTTP transport is simulated. The actual host adapter,
    # routes, project scope and inbox database stay in the request path.
    def adapter():
        return InteractionChannels(environ=settings, workspace=tmp_path,
                                   transport=httpx.MockTransport(upstream))
    monkeypatch.setattr('easel.interaction_channels.InteractionChannels', adapter)
    return local, client_id, state, requests


def create_channel(local, client_id, api='/api/interactions'):
    response = local.post(api + '/channels', json={
        'client_id': client_id, 'name': 'CreatorHub 自有作品评论', 'platform': 'douyin',
        'kind': 'comment', 'delivery_mode': 'creatorhub', 'config': {'env_prefix': PREFIX},
    })
    assert response.status_code == 200, response.text
    return response.json()['channels'][0]


def test_creatorhub_sync_imports_one_comment_with_provenance_and_no_send(pilot):
    local, client_id, _, requests = pilot
    channel = create_channel(local, client_id)
    assert channel['capabilities']['can_sync'] is True
    assert channel['capabilities']['can_send'] is False
    assert requests == []
    url = '/api/interactions/channels/' + channel['id'] + '/sync'
    for _ in range(2):
        response = local.post(url, json={'client_id': client_id})
        assert response.status_code == 200, response.text
    data = response.json()
    assert len(data['conversations']) == 1
    assert data['channels'][0]['last_sync_at']
    conversation = data['conversations'][0]
    detail_url = '/api/interactions/conversations/' + conversation['id']
    detail = local.get(detail_url, params={'client_id': client_id}).json()
    assert detail['revision'] == 1
    assert detail['source_url'] == 'https://www.douyin.com/video/' + ITEM_ID
    assert detail['messages'][0]['text'] == '想了解课程'
    assert detail['messages'][0]['simulated'] is False
    assert detail['messages'][0]['external_id'] == 'creatorhub:douyin:' + ITEM_ID + ':9876'

    draft = local.post(detail_url + '/draft', json={'client_id': client_id, 'expected_revision': 1}).json()
    draft_url = '/api/interactions/drafts/' + draft['drafts'][0]['id']
    edited = local.patch(draft_url, json={'client_id': client_id, 'expected_revision': 2, 'text': '请查看课程介绍'}).json()
    approved = local.post(draft_url + '/approve', json={'client_id': client_id,
                          'expected_revision': edited['revision'], 'review_kind': 'human'}).json()
    response = local.post(draft_url + '/send', json={'client_id': client_id,
                          'expected_revision': approved['revision'], 'operation_id': 'readonly-test'})
    assert response.status_code == 503, response.text
    assert len(requests) == 6


@pytest.mark.parametrize('changes', [{'status': 'invalid'}, {'account': 8}, {'item': '123'},
                                    {'work_platform': 'xhs'}, {'comment_platform': 'xhs'},
                                    {'unavailable': True}])
def test_creatorhub_sync_rejects_stale_or_mismatched_source(pilot, changes):
    local, client_id, state, requests = pilot
    channel = create_channel(local, client_id)
    url = '/api/interactions/channels/' + channel['id'] + '/sync'
    first = local.post(url, json={'client_id': client_id}).json()
    state.update(changes)
    response = local.post(url, json={'client_id': client_id})
    assert response.status_code == 503, response.text
    after = local.get('/api/interactions/bootstrap').json()
    assert after['channels'][0]['last_sync_at'] == first['channels'][0]['last_sync_at']
    assert after['conversations'] == first['conversations']
    if 'unavailable' in changes:
        assert requests[-1].url.path == '/api/accounts'


def test_creatorhub_binding_cannot_be_reused_by_a_different_customer_or_project(pilot):
    local, client_id, _, requests = pilot
    other_id = local.post('/api/interactions/clients', json={'name': '另一客户'}).json()['client_id']
    channel = create_channel(local, other_id)
    assert channel['capabilities']['can_sync'] is False
    project_id = local.post('/api/projects', json={'name': '另一项目'}).json()['id']
    api = f'/api/projects/{project_id}/interactions'
    channel = create_channel(local, client_id, api)
    assert channel['capabilities']['can_sync'] is False
    assert not requests


def test_creatorhub_sync_rejects_a_browser_supplied_source(pilot):
    local, client_id, _, requests = pilot
    channel = create_channel(local, client_id)
    url = '/api/interactions/channels/' + channel['id'] + '/sync'
    response = local.post(url, json={'client_id': client_id, 'source_url': 'https://example.invalid'})
    assert response.status_code == 400, response.text
    assert not requests


def test_creatorhub_image_note_keeps_the_note_source_url(pilot):
    local, client_id, state, _ = pilot
    state['media_type'] = 'images'
    channel = create_channel(local, client_id)
    result = local.post('/api/interactions/channels/' + channel['id'] + '/sync',
                        json={'client_id': client_id})
    assert result.status_code == 200
    assert result.json()['conversations'][0]['source_url'] == 'https://www.douyin.com/note/' + ITEM_ID


def test_creatorhub_empty_comments_are_a_successful_empty_inbox(pilot):
    local, client_id, state, _ = pilot
    state['empty'] = True
    channel = create_channel(local, client_id)
    result = local.post('/api/interactions/channels/' + channel['id'] + '/sync',
                        json={'client_id': client_id})
    assert result.status_code == 200
    data = result.json()
    assert data['conversations'] == []
    assert data['channels'][0]['last_sync_at']
