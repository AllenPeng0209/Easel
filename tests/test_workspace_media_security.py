"""Authenticated workshop calls may only use server-created, space-owned canvases."""
from __future__ import annotations

import json
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from easel.video_workshop import WorkshopStore
from web.workshop_routes import create_router


def client_for(outputs, *, authenticated=True, **callbacks):
    app = FastAPI()
    if authenticated:
        @app.middleware("http")
        async def actor(request, call_next):
            request.state.user = {"id": "operator-a", "is_admin": False}
            return await call_next(request)
    app.include_router(create_router(lambda: outputs, **callbacks))
    return TestClient(app)


def test_authenticated_content_edit_cannot_bind_another_ips_canvas(tmp_path):
    client = client_for(tmp_path)
    project = client.post("/api/workshop/projects", json={"title": "IP A"}).json()
    response = client.patch(f"/api/workshop/projects/{project['id']}", json={
        "expected_revision": 1, "title": "Untrusted replacement", "beeftv_canvas_id": "ip-b-private-canvas",
    })
    assert response.status_code == 403
    saved = client.get(f"/api/workshop/projects/{project['id']}").json()
    assert saved["beeftv_canvas_id"] == ""
    assert saved["title"] == "IP A"
    assert saved["revision"] == 1


def test_legacy_unregistered_canvas_does_not_query_shared_runtime(tmp_path):
    store = WorkshopStore(tmp_path)
    project = store.create({"title": "Legacy local project"})
    project = store.update(project["id"], 1, {"beeftv_canvas_id": "ip-b-private-canvas"})
    queried = []
    client = client_for(tmp_path, canvas_state=lambda project: queried.append(project["beeftv_canvas_id"]) or {"revision": 8})
    response = client.get(f"/api/workshop/projects/{project['id']}")
    assert response.status_code == 200
    assert response.json()["title"] == "Legacy local project"
    assert response.json()["engine_current_revision"] is None
    assert response.json()["engine_dirty"] is True
    assert queried == []


def test_authenticated_canvas_connection_registers_binding_and_survives_reload(tmp_path):
    creator = lambda project: {"beeftv_canvas_id": "own-" + project["id"]}
    client = client_for(tmp_path, canvas_creator=creator, canvas_state=lambda project: {"revision": 3})
    project = client.post("/api/workshop/projects", json={"title": "IP A"}).json()
    assert project["beeftv_canvas_id"] == "own-" + project["id"]
    reopened = client_for(tmp_path, canvas_state=lambda project: {"revision": 3})
    response = reopened.get(f"/api/workshop/projects/{project['id']}")
    assert response.status_code == 200
    assert response.json()["engine_current_revision"] == 3


def test_explicit_connection_replaces_unregistered_canvas_before_rendering(tmp_path):
    store = WorkshopStore(tmp_path)
    project = store.create({"title": "Imported local project"})
    project = store.update(project["id"], 1, {"beeftv_canvas_id": "foreign-canvas"})
    created = []
    def creator(project):
        created.append(project["id"])
        return "new-owned-canvas"
    client = client_for(tmp_path, canvas_creator=creator)
    response = client.post(f"/api/workshop/projects/{project['id']}/canvas", json={"expected_revision": 2})
    assert response.status_code == 200
    assert response.json()["beeftv_canvas_id"] == "new-owned-canvas"
    assert response.json()["revision"] == 3
    assert created == [project["id"]]


def test_unregistered_render_receipts_cannot_query_or_collect_other_tasks(tmp_path):
    client = client_for(tmp_path, canvas_creator=lambda project: "own-" + project["id"])
    project = client.post("/api/workshop/projects", json={"title": "IP A"}).json()
    record = tmp_path / "_workshop" / "projects" / f"{project['id']}.json"
    saved = json.loads(record.read_text())
    receipt_id = "a" * 32
    saved["renders"].append({
        "id": receipt_id, "task_id": "another-ip-task", "status": "running", "submission_state": "confirmed",
        "content_revision": 1, "engine_revision": 2, "progress": 0.5,
    })
    record.write_text(json.dumps(saved))
    queried = []
    client = client_for(tmp_path, render_status=lambda *_: queried.append("remote") or {"status": "succeeded"},
                        canvas_state=lambda _: {"revision": 2}, render_collect=lambda *_: queried.append("download"),
                        render_start=lambda *_: queried.append("submit"))
    base = f"/api/workshop/projects/{project['id']}/renders/{receipt_id}"
    assert client.get(base).status_code == 403
    assert client.post(base + "/collect", json={"expected_revision": 1}).status_code == 403
    assert client.post(f"/api/workshop/projects/{project['id']}/renders",
                       json={"expected_revision": 1}).status_code == 403
    assert queried == []


def test_local_cli_keeps_legacy_binding_behavior(tmp_path):
    client = client_for(tmp_path, authenticated=False, canvas_state=lambda _: {"revision": 4})
    project = client.post("/api/workshop/projects", json={"title": "Trusted CLI work"}).json()
    response = client.patch(f"/api/workshop/projects/{project['id']}", json={"expected_revision": 1,
                            "beeftv_canvas_id": "legacy-local-canvas"})
    assert response.status_code == 200
    assert client.get(f"/api/workshop/projects/{project['id']}").json()["engine_current_revision"] == 4


def test_owned_render_can_be_polled_and_collected_but_its_id_does_not_cross_spaces(tmp_path):
    outputs_a, outputs_b = tmp_path / "ip-a", tmp_path / "ip-b"
    source = outputs_a / "source" / "input.mp4"
    source.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=10:d=0.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True, capture_output=True)
    remote_tasks = []
    def status(project, receipt):
        remote_tasks.append(receipt["task_id"])
        return {"status": "succeeded", "progress": 1.0}
    callbacks = {
        "canvas_creator": lambda project: "owned-" + project["id"],
        "canvas_state": lambda project: {"revision": 7},
        "render_start": lambda project, operation: {"task_id": "task-ip-a", "engine_revision": 7, "status": "queued"},
        "render_status": status,
        "render_collect": lambda project, receipt: {"path": "source/input.mp4", "engine_revision": 7},
    }
    client_a, client_b = client_for(outputs_a, **callbacks), client_for(outputs_b, **callbacks)
    a = client_a.post("/api/workshop/projects", json={"title": "IP A"}).json()
    b = client_b.post("/api/workshop/projects", json={"title": "IP B"}).json()
    rendered = client_a.post(f"/api/workshop/projects/{a['id']}/renders", json={"expected_revision": 1})
    assert rendered.status_code == 200, rendered.text
    receipt = rendered.json()
    assert receipt["canvas_id"] == "owned-" + a["id"]
    foreign = f"/api/workshop/projects/{b['id']}/renders/{receipt['id']}"
    assert client_b.get(foreign).status_code == 404
    assert client_b.post(foreign + "/collect", json={"expected_revision": 1}).status_code == 404
    assert remote_tasks == []
    url = f"/api/workshop/projects/{a['id']}/renders/{receipt['id']}"
    assert client_a.get(url).json()["status"] == "succeeded"
    collected = client_a.post(url + "/collect", json={"expected_revision": 1})
    assert collected.status_code == 200, collected.text
    assert collected.json()["deliverables"][0]["engine_revision"] == 7
    assert collected.json()["renders"][0]["artifact_id"]
    assert remote_tasks == ["task-ip-a"]
