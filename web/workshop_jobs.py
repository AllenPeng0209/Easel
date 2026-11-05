"""Read-only native composition jobs. Listing never starts or collects a task."""
from datetime import datetime


def _timestamp(value):
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def workshop_jobs(store, **_):
    jobs = []
    for project in store.list():
        for receipt in project['renders']:
            if receipt.get('backend') != 'native':
                continue
            status = receipt['status']
            artifact = next((row for row in project['deliverables'] if row.get('render_id') == receipt['id']
                             and row['id'] == receipt.get('artifact_id')), None)
            if status == 'succeeded':
                status = 'awaiting_review' if artifact else 'awaiting_collection'
            created, finished = _timestamp(receipt['created_at']), _timestamp(receipt.get('completed_at'))
            jobs.append({'id': receipt['id'], 'workshopProjectId': project['id'], 'title': project['title'],
                'taskId': receipt['task_id'], 'status': status, 'progress': receipt.get('progress') if receipt.get('progress_known') else None,
                'createdAt': created, 'updatedAt': _timestamp(receipt['updated_at']), 'finishedAt': finished,
                'elapsedMs': finished - created if finished is not None and created is not None else None,
                'artifactId': artifact['id'] if artifact else None, 'artifactSha256': artifact['sha256'] if artifact else None,
                'contentRevision': receipt['content_revision'], 'canCancel': False, 'cancelReason': 'unsupported'})
    return jobs
