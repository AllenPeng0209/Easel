"""A proven native writer conflict may change only the current Host binding."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from easel.harnesses import store
from easel.harnesses.base import HarnessError, RunRequest


OLD = '01a1a6a0-9283-7f91-ae82-b249a6849005'
NEW = '01a1a777-1111-7222-8333-444444444444'


def prepared(tmp_path):
    sessions = tmp_path / 'outputs' / '_sessions'
    store.bind(sessions, 'same-chat', 'codex', OLD)
    store.remember_model_baseline(sessions, 'same-chat', 'gpt-6.1-sol')
    return sessions


def test_active_writer_cas_changes_only_native_binding_and_preserves_provenance(tmp_path):
    sessions = prepared(tmp_path)
    assert callable(getattr(store, 'rebind_active_writer', None)), 'Missing the Host-only native writer CAS'
    other = sessions / 'unchanged-budget.json'
    other.write_text('{"sessionId":"same-chat","submitAttempted":true}')
    before = other.read_bytes()
    store.rebind_active_writer(sessions, 'same-chat', OLD, NEW)
    record = store.binding(sessions, 'same-chat')
    assert record['native_id'] == NEW and record['runtime'] == 'codex'
    assert record['model_baseline'] == 'gpt-6.1-sol'
    assert record['native_fork'] == {'reason': 'active_writer', 'from': OLD, 'to': NEW}
    store.bind(sessions, 'same-chat', 'codex', NEW)
    assert store.binding(sessions, 'same-chat') == record
    assert other.read_bytes() == before


@pytest.mark.parametrize('old,new', [(OLD, ''), (OLD, OLD), ('different-old', NEW), (OLD, 'new\nwrong')])
def test_active_writer_cas_rejects_drift_or_invalid_transition_without_writing(tmp_path, old, new):
    sessions = prepared(tmp_path)
    assert callable(getattr(store, 'rebind_active_writer', None))
    path = store.binding_path(sessions, 'same-chat')
    before = path.read_bytes()
    with pytest.raises(HarnessError):
        store.rebind_active_writer(sessions, 'same-chat', old, new)
    assert path.read_bytes() == before


def test_ordinary_bind_still_cannot_replace_native_history(tmp_path):
    sessions = prepared(tmp_path)
    with pytest.raises(HarnessError):
        store.bind(sessions, 'same-chat', 'codex', NEW)
    assert store.binding(sessions, 'same-chat')['native_id'] == OLD


def test_non_codex_binding_cannot_acquire_active_writer_cas(tmp_path):
    sessions = tmp_path / 'sessions'
    store.bind(sessions, 'same-chat', 'acp', OLD)
    assert callable(getattr(store, 'rebind_active_writer', None))
    with pytest.raises(HarnessError):
        store.rebind_active_writer(sessions, 'same-chat', OLD, NEW)
    assert store.binding(sessions, 'same-chat') == {'runtime': 'acp', 'native_id': OLD}


def host_callback(tmp_path, *, changed=None):
    """Run the actual nested Host callback with its pre-established lock scope."""
    import web.app as web
    tree = ast.parse(Path(web.__file__).read_text())
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
             and node.name == 'rebind_active_writer']
    assert nodes, 'Missing the scoped Host writer rebind callback'
    sessions = prepared(tmp_path)
    actor = {'id': 'host-actor'}
    state = {'actor': actor, 'ip': 'default', 'workspace': tmp_path, 'sessions': sessions,
             'locked': True, 'acquired': True, 'permission': True}
    if changed:
        state.update(changed)
    def require():
        if not state['permission']:
            raise HarnessError('revoked')
    namespace = dict(vars(web), runtime='codex', interactive=True, sk='same-chat', claimed=True, native_id=OLD,
        locked=state['locked'], acquired=state['acquired'], lock=SimpleNamespace(locked=lambda: state['locked']),
        xlock=SimpleNamespace(acquired=state['acquired']),
        binding_actor_id='host-actor', binding_ip='default', binding_workspace=tmp_path.resolve(),
        binding_sessions=sessions.resolve(),
        _require_host_tools=require, current_user=lambda: state['actor'], project_id=lambda: state['ip'],
        _workspace_root=lambda: state['workspace'], _sessions_dir=lambda: state['sessions'])
    exec(compile(ast.Module(body=[nodes[0]], type_ignores=[]), web.__file__, 'exec'), namespace)
    return namespace['rebind_active_writer'], sessions


def test_host_callback_rebinds_only_inside_the_held_original_scope(tmp_path):
    assert 'rebind_active_writer' in RunRequest.__dataclass_fields__
    callback, sessions = host_callback(tmp_path)
    callback(OLD, NEW)
    assert store.binding(sessions, 'same-chat')['native_id'] == NEW


def test_host_callback_cannot_follow_a_drifted_binding_as_its_new_original(tmp_path):
    callback, sessions = host_callback(tmp_path)
    drifted = '01a1a999-1111-7222-8333-444444444444'
    store.rebind_active_writer(sessions, 'same-chat', OLD, drifted)
    before = store.binding_path(sessions, 'same-chat').read_bytes()
    with pytest.raises(HarnessError):
        callback(drifted, NEW)
    assert store.binding_path(sessions, 'same-chat').read_bytes() == before


@pytest.mark.parametrize('change', [
    {'actor': {'id': 'foreign-actor'}}, {'ip': 'foreign-ip'}, {'permission': False},
    {'locked': False}, {'acquired': False}, {'workspace': Path('/foreign')}, {'sessions': Path('/foreign')},
])
def test_host_callback_rejects_scope_permission_or_lock_changes_before_cas(tmp_path, change):
    callback, sessions = host_callback(tmp_path, changed=change)
    path = store.binding_path(sessions, 'same-chat')
    before = path.read_bytes()
    with pytest.raises(HarnessError):
        callback(OLD, NEW)
    assert path.read_bytes() == before
