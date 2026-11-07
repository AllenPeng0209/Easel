import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_huobao_workshop import engine


def test_unchanged_read_binding_does_not_snapshot_or_rewrite_the_engine(engine, monkeypatch):
    project = engine.store.create({'title': 'Read-only existing project'})
    original = engine.open(project['id'])
    before = engine.bindings_path.stat().st_mtime_ns

    def unnecessary_snapshot(*args, **kwargs):
        raise AssertionError('Unchanged reads must not snapshot the entire engine')

    monkeypatch.setattr(engine, '_snapshot', unnecessary_snapshot)
    assert engine.read_binding(project['id'])['episode_id'] == original['episode_id']
    assert engine.bindings_path.stat().st_mtime_ns == before


def test_native_revision_changes_still_reach_the_existing_editor(engine):
    project = engine.store.create({'title': 'Native edit'})
    binding = engine.open(project['id'])
    engine.store.production(project['id'], project['revision'], {'script': 'New authoritative script', 'assets': [], 'shots': []})
    engine.read_binding(project['id'])
    current = engine.request('GET', '/api/v1/dramas/' + str(binding['drama_id']))['data']
    assert current['episodes'][0]['script_content'] == 'New authoritative script'


def test_pull_only_reconciles_when_the_underlying_engine_database_changes(engine, monkeypatch):
    project = engine.store.create({'title': 'Changed engine'})
    binding = engine.open(project['id'])
    engine.pull_if_changed(project['id'])
    engine.request('PUT', '/api/v1/episodes/' + str(binding['episode_id']), {'script_content': 'Editor change'})
    assert engine.pull_if_changed(project['id'])['script'] == 'Editor change'

    def unnecessary_snapshot(*args, **kwargs):
        raise AssertionError('The unchanged database should use its last observed version')

    monkeypatch.setattr(engine, '_snapshot', unnecessary_snapshot)
    assert engine.pull_if_changed(project['id'])['script'] == 'Editor change'


def test_video_list_summary_keeps_workflow_receipts_without_loading_scripts(tmp_path):
    from easel.video_workshop import WorkshopStore
    from web.workshop_routes import create_router
    store = WorkshopStore(tmp_path)
    project = store.create({'title': 'Exact workflow status', 'brief': 'Searchable creative requirements'})
    store.production(project['id'], project['revision'], {'script': 'Long screenplay\n' * 10000, 'assets': [], 'shots': []})
    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path))
    client = TestClient(app)
    response = client.get('/api/workshop/projects?summary=true')
    assert response.status_code == 200
    assert len(response.content) < 2500
    summary = response.json()['projects'][0]
    assert summary['title'] == project['title']
    assert summary['brief'] == project['brief']
    assert summary['revision'] == 2
    assert summary['script'] == ''
    assert summary['assets'] == [] and summary['shots'] == []
    assert store.get(project['id'])['script'].startswith('Long screenplay')
    unchanged = client.get('/api/workshop/projects?summary=true', headers={'If-None-Match': response.headers.get('etag', '')})
    assert unchanged.status_code == 304
    detail = client.get('/api/workshop/projects/' + project['id'])
    assert detail.headers.get('etag')
    assert client.get('/api/workshop/projects/' + project['id'], headers={'If-None-Match': detail.headers['etag']}).status_code == 304


def test_snapshot_reads_storyboard_associations_in_a_bounded_number_of_queries(engine):
    project = engine.store.create({'title': 'Twenty storyboards'})
    engine.store.production(project['id'], project['revision'], {'script': 'Script', 'assets': [],
        'shots': [{'id': f'shot-{index}', 'title': str(index), 'prompt': 'A clear scene', 'seconds': 2, 'asset_ids': []}
                  for index in range(20)]})
    binding = engine.open(project['id'])
    statements = []
    with engine.database() as database:
        database.set_trace_callback(statements.append)
        snapshot = engine._snapshot(binding, database)
    assert len(snapshot['shots']) == 20
    assert all(shot['character_ids'] == [] and shot['prop_ids'] == [] for shot in snapshot['shots'])
    assert len(statements) <= 12, f'Twenty shots needed {len(statements)} SQL reads'
