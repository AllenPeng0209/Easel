"""Verify C17 through the production authorization and IP rewrite boundary."""
from io import BytesIO
from zipfile import ZipFile
import secrets

import pytest

from easel.projects import project_context
from easel.request_identity import actor_context
from tests.test_chat_artifacts import artifact_http_environment
from web.chat_history import ChatHistoryStore


@pytest.mark.access_integration
def test_second_platform_admin_cannot_export_first_admin_catalog(artifact_http_environment):
    from web import app as web

    root, owner, _, anonymous, _ = artifact_http_environment
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        ChatHistoryStore(web._sessions_dir()).merge([
            {'id': 'export-target', 'title': 'Source', 'messages': [], 'updated': 10},
        ])

    route = '/api/chat/sessions/export-target/export/logs'
    password = secrets.token_urlsafe(24)
    second_admin = owner.post('/api/users', json={'username': 'secondadmin', 'password': password, 'is_admin': True}).json()
    assert owner.post('/api/projects/default/members', json={'user_id': second_admin['id'], 'role': 'operator'}).status_code == 200
    second = anonymous()
    assert second.post('/api/auth/login', json={'username': 'secondadmin', 'password': password}).status_code == 200
    assert second.get(route).status_code == 404
    assert second.head(route).status_code == 404

    response = owner.get(route)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['content-disposition'].startswith('attachment;')
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ['manifest.json']
    assert owner.delete('/api/chat/sessions/export-target').status_code == 200
    assert owner.get(route).status_code == 404
