"""Team roles and IP-owned credential references for the interaction center."""
from __future__ import annotations

import json
import sqlite3

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easel.access import AccessStore
from easel.interaction_channels import InteractionChannels
from easel.interaction_center import InteractionStore
from easel.projects import ProjectScopeMiddleware, project_context, space_path
from easel.request_identity import actor_context
from web.access_control import AccessControlMiddleware
from web.interaction_routes import create_router

pytestmark = pytest.mark.access_integration


def channel(prefix):
    return {'id': 'a' * 32, 'client_id': 'b' * 32, 'platform': 'douyin', 'kind': 'comment',
            'delivery_mode': 'official', 'enabled': True, 'config': {'env_prefix': prefix, 'video_id': 'video-one'}}


def credentials(prefix):
    return {prefix + '_ACCESS_TOKEN': 'private-test-token', prefix + '_OPEN_ID': 'account-one',
            prefix + '_PERMISSION_VERIFIED': 'true'}


def test_actor_cannot_guess_another_ips_prefix_and_send_with_its_token(tmp_path):
    foreign = 'EASEL_INTERACTION_BBBBBBBBBBBBBBBB_DOUYIN'
    contacted = []
    def remote(request):
        contacted.append(request)
        return httpx.Response(200, json={'data': {'error_code': 0, 'comment_id': 'sent-one'}})
    adapter = InteractionChannels(environ=credentials(foreign), workspace=tmp_path,
                                  transport=httpx.MockTransport(remote))
    with project_context('aaaaaaaaaaaaaaaa', tmp_path), actor_context({'id': 'operator', 'is_admin': False}):
        result = adapter.send(channel(foreign), {'last_inbound_external_id': 'comment-one'}, {'text': '回复'}, 'operation-one')
    assert result['status'] == 'failed'
    assert contacted == []


def test_authenticated_actor_can_use_its_own_explicit_ip_namespace(tmp_path):
    own = 'EASEL_INTERACTION_AAAAAAAAAAAAAAAA_DOUYIN'
    adapter = InteractionChannels(environ=credentials(own), workspace=tmp_path)
    with project_context('aaaaaaaaaaaaaaaa', tmp_path), actor_context({'id': 'operator', 'is_admin': False}):
        assert adapter.capabilities(channel(own))['can_send'] is True


@pytest.fixture
def team(tmp_path):
    access = AccessStore(tmp_path)
    admin, admin_token = access.setup('administrator', 'admin-password-123', project_ids=['default'])
    people = {'admin': (admin, admin_token)}
    for role in ('owner', 'operator', 'viewer'):
        user = access.create_user(role + '-user', role + '-password-123')
        access.set_member('default', user['id'], role)
        people[role] = (user, access.login(role + '-user', role + '-password-123')[1])
    app = FastAPI()
    app.include_router(create_router(lambda: space_path(tmp_path, tmp_path / 'outputs', 'outputs')))
    app.add_middleware(ProjectScopeMiddleware, get_root=lambda: tmp_path)
    app.add_middleware(AccessControlMiddleware, get_root=lambda: tmp_path)
    def client(role):
        value = TestClient(app, base_url='http://localhost:7860', headers={'Origin': 'http://localhost:7860'})
        value.cookies.set('easel_session', people[role][1])
        return value
    return tmp_path, client


def test_nonadmin_owner_cannot_create_or_replace_a_global_credential_reference(team):
    root, client = team
    owner = client('owner')
    client_id = owner.get('/api/interactions/bootstrap').json()['client_id']
    payload = {'client_id': client_id, 'name': 'Foreign account', 'platform': 'douyin', 'kind': 'comment',
               'delivery_mode': 'official', 'config': {'env_prefix': 'EASEL_INTERACTION_ANOTHER_IP'}}
    assert owner.post('/api/interactions/channels', json=payload).status_code == 403
    payload['config'] = {'env_prefix': 'EASEL_INTERACTION_DEFAULT_DOUYIN'}
    created = owner.post('/api/interactions/channels', json=payload)
    assert created.status_code == 200, created.text
    channel_id = created.json()['channels'][0]['id']
    assert owner.patch('/api/interactions/channels/' + channel_id,
                       json={'client_id': client_id, 'config': {'env_prefix': 'EASEL_INTERACTION_ANOTHER_IP'}}).status_code == 403
    assert owner.get('/api/interactions/bootstrap').json()['channels'][0]['config']['env_prefix'] == 'EASEL_INTERACTION_DEFAULT_DOUYIN'


def test_admin_delegated_legacy_prefix_remains_bound_to_its_ip_after_restart(team):
    root, client = team
    admin = client('admin')
    client_id = admin.get('/api/interactions/bootstrap').json()['client_id']
    prefix = 'EASEL_INTERACTION_LEGACY_CORPORATE'
    response = admin.post('/api/interactions/channels', json={'client_id': client_id,
        'name': 'Delegated enterprise account', 'platform': 'douyin', 'kind': 'comment', 'delivery_mode': 'official',
        'config': {'env_prefix': prefix, 'video_id': 'video-one'}})
    assert response.status_code == 200
    delegated = response.json()['channels'][0]
    # Current actor=None models the periodic worker. The configured multi-user
    # database still requires the persisted IP ownership proof.
    adapter = InteractionChannels(environ=credentials(prefix), workspace=root)
    with project_context('default', root):
        assert adapter.capabilities(delegated)['can_send'] is True
    with project_context('default', root), actor_context({'id': 'operator', 'is_admin': False}):
        assert adapter.capabilities(delegated)['can_send'] is True
    with project_context('aaaaaaaaaaaaaaaa', root):
        assert adapter.capabilities(delegated)['can_send'] is False
    # A normal owner cannot forge or replace the privileged delegation fields.
    forged = client('owner').patch('/api/interactions/channels/' + delegated['id'], json={
        'client_id': client_id, 'credential_binding': {'project_id': 'default', 'prefix': prefix}})
    assert forged.status_code == 422


def test_unattributed_background_job_rejects_legacy_unbound_global_credentials(team):
    root, _ = team
    prefix = 'EASEL_INTERACTION_OTHER_CUSTOMER'
    adapter = InteractionChannels(environ=credentials(prefix), workspace=root)
    with project_context('default', root):
        assert adapter.capabilities(channel(prefix))['can_send'] is False


def test_viewer_bootstrap_does_not_create_a_default_business_client(team):
    root, client = team
    response = client('viewer').get('/api/interactions/bootstrap')
    assert response.status_code == 200, response.text
    assert response.json()['clients'] == []
    database = root / 'outputs' / '_interactions' / 'center.sqlite3'
    if database.exists():
        with sqlite3.connect(database) as connection:
            assert connection.execute('SELECT COUNT(*) FROM clients').fetchone()[0] == 0


def test_viewer_reads_existing_custom_clients_without_inserting_the_default(team):
    root, client = team
    local = InteractionStore(root / 'outputs', initialize_default_client=False)
    created = local.create_client('Existing custom client')
    response = client('viewer').get('/api/interactions/bootstrap')
    assert response.status_code == 200, response.text
    assert [item['name'] for item in response.json()['clients']] == ['Existing custom client']
    assert response.json()['client_id'] == created['client_id']
    with sqlite3.connect(local.path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM clients').fetchone()[0] == 1


def test_viewer_cannot_write_messages_while_operator_can_process_simulation(team):
    _, client = team
    owner = client('owner')
    client_id = owner.get('/api/interactions/bootstrap').json()['client_id']
    assert client('viewer').post('/api/interactions/simulation', json={'client_id': client_id}).status_code == 403
    assert client('operator').post('/api/interactions/channels', json={'client_id': client_id,
        'name': 'Not operator managed', 'platform': 'xiaohongshu', 'kind': 'comment'}).status_code == 403
    seeded = client('operator').post('/api/interactions/simulation', json={'client_id': client_id})
    assert seeded.status_code == 200, seeded.text
    conversation = seeded.json()['conversations'][0]
    payload = {'client_id': client_id, 'expected_revision': conversation['revision']}
    url = '/api/interactions/conversations/' + conversation['id'] + '/draft'
    assert client('viewer').post(url, json=payload).status_code == 403
    assert client('operator').post(url, json=payload).status_code == 200


def test_unconfigured_local_cli_keeps_legacy_prefix_support(tmp_path):
    prefix = 'EASEL_INTERACTION_LEGACY_LOCAL'
    adapter = InteractionChannels(environ=credentials(prefix), workspace=tmp_path)
    with project_context('default', tmp_path):
        assert adapter.capabilities(channel(prefix))['can_send'] is True
