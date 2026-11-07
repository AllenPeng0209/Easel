import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easel.access import AccessStore
from web.access_routes import create_router

pytestmark = pytest.mark.access_integration


def test_identity_bootstrap_combines_configuration_and_verified_cookie_identity(tmp_path):
    user, token = AccessStore(tmp_path).setup('owner', 'bootstrap-identity-tests-42')
    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path, lambda: []))
    client = TestClient(app)
    assert client.get('/api/auth/bootstrap').json() == {'configured': True}
    flag = {'X-Easel-Bootstrap-Identity': '1'}
    assert client.get('/api/auth/bootstrap', headers=flag).json() == {'configured': True, 'user': None}
    result = client.get('/api/auth/bootstrap', headers={**flag, 'Cookie': 'easel_session=' + token})
    assert result.json()['user']['id'] == user['id']
    assert result.headers['cache-control'] == 'no-store'
    changed = client.get('/api/auth/bootstrap', headers={**flag, 'Cookie': 'easel_session=' + token, 'X-Easel-User': 'another-user'})
    assert changed.json()['user'] is None
