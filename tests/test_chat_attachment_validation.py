import pytest
from fastapi import HTTPException

from tests.test_multiuser_integration import environment
from web import app as web


pytestmark = pytest.mark.access_integration


def upload(client, session_id, name):
    response = client.post('/api/upload', data={'sessionId': session_id},
                           files={'files': (name, b'content', 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()['files'][0]


def validate(client, session_id, attachments, *, prefix='/api'):
    return client.post(f'{prefix}/upload/validate',
                       json={'sessionId': session_id, 'attachments': attachments})


def test_validation_preserves_order_and_checks_current_file_and_session(environment):
    root, owner, _, _ = environment
    ready = upload(owner, 'draft', 'ready.txt')
    missing = upload(owner, 'draft', 'missing.txt')
    foreign = upload(owner, 'other', 'other.txt')
    (root / 'outputs' / missing['path']).unlink()

    result = validate(owner, 'draft', [missing, ready, foreign, {**ready, 'id': 'wrong'}, ready])

    assert result.status_code == 200, result.text
    assert result.json() == {'valid': [False, True, False, False, True]}
    project = owner.post('/api/projects', json={'name': 'Other IP'}).json()['id']
    assert validate(owner, 'draft', [ready], prefix=f'/api/projects/{project}').json() == {'valid': [False]}


def test_validation_requires_operation_permission_and_limits_batch(environment):
    _, owner, users, unauthenticated = environment
    operator = users['operator'][0]
    ready = upload(operator, 'draft', 'operator.txt')

    assert validate(operator, 'draft', [ready]).json() == {'valid': [True]}
    assert validate(users['viewer'][0], 'draft', [ready]).status_code == 403
    assert validate(unauthenticated(), 'draft', [ready]).status_code == 401
    assert validate(owner, 'draft', [ready]).json() == {'valid': [False]}
    assert validate(operator, 'draft', [ready] * 101).status_code == 422


@pytest.mark.parametrize('target_kind', ['foreign-inbox', 'system-directory', 'foreign-batch-directory'])
def test_validation_and_normal_submit_reject_inbox_symlink_aliases(environment, target_kind):
    root, owner, _, _ = environment
    ready = upload(owner, 'draft', 'alias.txt')
    path = root / 'outputs' / ready['path']
    if target_kind in {'foreign-inbox', 'foreign-batch-directory'}:
        other = upload(owner, 'other', 'private.txt')
        target = root / 'outputs' / other['path']
    else:
        target = root / 'outputs' / '_sessions' / 'private.txt'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'private content')
    path.unlink()
    if target_kind == 'foreign-batch-directory':
        target.rename(target.with_name(path.name))
        path.parent.rmdir()
        path.parent.symlink_to(target.parent, target_is_directory=True)
    else:
        path.symlink_to(target)

    response = validate(owner, 'draft', [ready])
    assert response.status_code == 200, response.text
    assert response.json() == {'valid': [False]}
    req = web.ChatRequest(sessionId='draft', message='use file', attachments=[ready])
    with pytest.raises(HTTPException) as error:
        web._attachment_context(req)
    assert error.value.status_code == 403
