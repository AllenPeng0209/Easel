"""Read-only workshop roster projection for the pinned Harness jobs view.

The renderer's provider state is observed through the existing workshop service.
This projection exposes only durable records and never queries that provider.
"""
from __future__ import annotations

import re
from datetime import datetime

from easel.video_workshop import WorkshopError, WorkshopStore

_IDENTIFIER = re.compile(r"[a-f0-9]{32}")
_TASK_IDENTIFIER = re.compile(r"[A-Za-z0-9:_-]{1,160}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_RENDER_STATUS = {"queued", "running", "failed", "cancelled", "succeeded"}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _timestamp(value) -> int | None:
    if not isinstance(value, str) or len(value) > 48:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return int(parsed.timestamp() * 1000) if parsed.tzinfo is not None else None
    except (OverflowError, OSError, ValueError):
        return None


def _integer(value) -> int | None:
    return value if type(value) is int and 0 <= value < 2**53 else None


def _identifier(value, pattern=_IDENTIFIER) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _adopted_artifact(project, receipt, render_id):
    artifact_id = _identifier(receipt.get("artifact_id"))
    collected = _integer(receipt.get("collected_revision"))
    engine = _integer(receipt.get("engine_revision"))
    if artifact_id is None or collected is None or engine is None:
        return None
    return next((row for row in _rows(project.get("deliverables"))
                 if row.get("id") == artifact_id and row.get("render_id") == render_id
                 and _integer(row.get("revision")) == collected
                 and _integer(row.get("engine_revision")) == engine
                 and _identifier(row.get("sha256"), _DIGEST)), None)


def _job_status(project, receipt, artifact, confirmed):
    status = receipt.get("status")
    if not confirmed or not isinstance(status, str) or status not in _RENDER_STATUS:
        return "unconfirmed"
    if status != "succeeded":
        return status
    if artifact is None:
        return "awaiting_collection"
    for review in reversed(_rows(project.get("reviews"))):
        if (review.get("kind") == "human" and review.get("artifact_id") == artifact["id"]
                and review.get("artifact_sha256") == artifact["sha256"]
                and _integer(review.get("revision")) == artifact["revision"]):
            if review.get("decision") == "approved":
                return "completed"
            if review.get("decision") == "changes_requested":
                return "changes_requested"
    return "awaiting_review"


def workshop_jobs(store: WorkshopStore) -> list[dict]:
    jobs = []
    for project in store.list():
        workshop_project_id = project["id"]
        title = project.get("title")
        title = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", title)[:120].strip() if isinstance(title, str) else ""
        for receipt in _rows(project.get("renders")):
            render_id = _identifier(receipt.get("id"))
            if render_id is None:
                continue
            task_id = _identifier(receipt.get("task_id"), _TASK_IDENTIFIER)
            status = receipt.get("status")
            confirmed = bool(task_id and isinstance(status, str) and status in _RENDER_STATUS
                             and WorkshopStore._submission_confirmed(receipt))
            try:
                store._assert_canvas_binding(project, receipt)
            except WorkshopError:
                confirmed = False
            artifact = _adopted_artifact(project, receipt, render_id) if confirmed and receipt.get("status") == "succeeded" else None
            created_at = _timestamp(receipt.get("created_at"))
            updated_at = _timestamp(receipt.get("updated_at"))
            finished_at = (_timestamp(receipt.get("completed_at")) if confirmed
                           and receipt.get("status") in ("succeeded", "failed", "cancelled") else None)
            progress = receipt.get("progress")
            progress_known = (confirmed and receipt.get("progress_known") is True and type(progress) in (int, float)
                              and 0 <= progress <= 1)
            jobs.append({
                "id": render_id, "workshopProjectId": workshop_project_id, "title": title or "未命名工坊",
                "taskId": task_id if confirmed else None, "status": _job_status(project, receipt, artifact, confirmed),
                "progress": progress if progress_known else None,
                "createdAt": created_at, "updatedAt": updated_at, "finishedAt": finished_at,
                "elapsedMs": (finished_at - created_at if finished_at is not None and created_at is not None
                              and finished_at >= created_at else None),
                "artifactId": artifact["id"] if artifact else None, "artifactSha256": artifact["sha256"] if artifact else None,
                "contentRevision": _integer(receipt.get("content_revision")), "engineRevision": _integer(receipt.get("engine_revision")),
            })
    return sorted(jobs, key=lambda job: (-(job["updatedAt"] or 0), job["workshopProjectId"], job["id"]))
