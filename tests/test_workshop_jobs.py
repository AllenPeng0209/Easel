"""The native job list is a read-only projection of recorded work."""
from easel.video_workshop import WorkshopStore
from web.workshop_jobs import workshop_jobs


def test_native_job_roster_never_adopts_a_foreign_or_uncollected_output(tmp_path):
    store = WorkshopStore(tmp_path)
    project = store.create({'title': '家庭教育短剧'})
    receipt = {'id': 'a' * 32, 'task_id': 'a' * 32, 'backend': 'native', 'content_revision': 1,
        'status': 'succeeded', 'created_at': '2026-11-05T00:00:00+00:00', 'updated_at': '2026-11-05T00:00:02+00:00',
        'completed_at': '2026-11-05T00:00:02+00:00', 'progress': 1, 'progress_known': True,
        'inputs': [{'path': 'private/source.mp4'}], 'output_path': 'private/result.mp4'}
    project['renders'] = [receipt]
    store._write(project)
    before = store._path(project['id']).read_bytes()
    rows = workshop_jobs(store)
    assert rows[0]['status'] == 'awaiting_collection'
    assert rows[0]['artifactId'] is None
    assert rows[0]['elapsedMs'] == 2000
    assert 'private' not in str(rows)
    assert store._path(project['id']).read_bytes() == before
    project['deliverables'] = [{'id': 'b' * 32, 'render_id': 'different', 'sha256': 'c' * 64}]
    project['renders'][0]['artifact_id'] = 'b' * 32
    store._write(project)
    assert workshop_jobs(store)[0]['artifactId'] is None
