import asyncio
import json
from types import SimpleNamespace

from easel.request_identity import actor_context
from easel.video_workshop import WorkshopStore


def test_finished_action_retains_its_receipt_time_after_later_project_edits(tmp_path, monkeypatch):
    from web import workshop_actions as module
    root = tmp_path / 'repo'
    store = WorkshopStore(root / 'outputs')
    project = store.create({'title': 'Completed rewrite'})
    store.production(project['id'], project['revision'], {'script': 'Saved AI result', 'assets': [], 'shots': []})
    journal = tmp_path / 'events.jsonl'
    journal.write_text(json.dumps({'id': 1, 'event': 'done', 'data': {'outcome': 'completed'}}) + '\n')
    host = SimpleNamespace(PROJECT_ROOT=root, _outputs_dir=lambda: root / 'outputs', _require_host_tools=lambda: None,
        _ACTIVE_TURN_IDS=set(), _scrub_secrets=lambda value: value,
        _read_job_events=lambda _turn: [json.loads(line) for line in journal.read_text().splitlines()])
    monkeypatch.setattr(module, 'generation_state_directory', lambda *_: tmp_path / 'private' / 'video-generations')
    now = [1.0]
    monkeypatch.setattr(module.time, 'time', lambda: now[0])
    row = {'id': 'job', 'workshop_id': project['id'], 'action': 'rewrite', 'label': 'Rewrite', 'status': 'running',
        'session_id': 'session', 'turn_id': 'turn', 'created_at': 0, 'questions': [], 'message': '', 'before': '', 'shot_ids': [], 'asset_ids': []}
    actions = module.WorkshopActions(host)
    with actor_context({'id': 'owner'}):
        first = asyncio.run(actions.observe(row))
        current = store.get(project['id'])
        store.production(project['id'], current['revision'], {'script': '', 'assets': [], 'shots': []})
        now[0] = 20.0
        following = asyncio.run(actions.observe(row))
    assert first['status'] == following['status'] == 'completed'
    assert first['finished_at'] == following['finished_at'] == 1000
