"""Only locally-owned workshop canvases may enter an isolated video runtime."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3

import pytest


def migration():
    try:
        return importlib.import_module("easel.beeftv_migration")
    except ModuleNotFoundError:
        pytest.fail(
            "Project-owned BeefTV migration has not been implemented", pytrace=False
        )


SCHEMA = """
CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE canvas_projects (id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, library_folder_id TEXT, title TEXT, payload_json TEXT, revision INTEGER);
CREATE TABLE canvas_snapshots (id TEXT PRIMARY KEY, canvas_id TEXT, user_id TEXT, revision INTEGER, payload_json TEXT);
CREATE TABLE canvas_snapshot_resources (snapshot_id TEXT, resource_id TEXT, PRIMARY KEY(snapshot_id,resource_id));
CREATE TABLE canvas_drawings (user_id TEXT, canvas_id TEXT, drawing_id TEXT, snapshot_json TEXT, preview_resource_id TEXT, render_resource_id TEXT, PRIMARY KEY(user_id,canvas_id,drawing_id));
CREATE TABLE canvas_library_folders (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, cover_resource_id TEXT);
CREATE TABLE assets (id TEXT PRIMARY KEY, user_id TEXT, folder_id TEXT, primary_version_id TEXT, title TEXT, payload_json TEXT);
CREATE TABLE asset_folders (id TEXT PRIMARY KEY, user_id TEXT, name TEXT);
CREATE TABLE asset_versions (id TEXT PRIMARY KEY, asset_id TEXT, version INTEGER, definition_json TEXT);
CREATE TABLE asset_representations (id TEXT PRIMARY KEY, task_id TEXT, asset_version_id TEXT, resource_id TEXT, metadata_json TEXT);
CREATE TABLE resources (id TEXT PRIMARY KEY, user_id TEXT, kind TEXT, status TEXT, provider TEXT, object_key TEXT, public_url TEXT, size INTEGER, mime_type TEXT, playback_status TEXT, playback_object_key TEXT);
CREATE TABLE tasks (id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, client_operation_id TEXT, type TEXT, status TEXT, input_json TEXT, result_json TEXT);
CREATE TABLE task_logs (id TEXT PRIMARY KEY, task_id TEXT, user_id TEXT, message TEXT);
CREATE TABLE agent_op_records (id TEXT PRIMARY KEY, result_json TEXT);
CREATE TABLE system_settings (key TEXT PRIMARY KEY, value_json TEXT, updated_by TEXT);
CREATE TABLE model_channels (id TEXT PRIMARY KEY, user_id TEXT, api_key TEXT);
"""


def write_project(workspace, identity, canvas, renders=None):
    directory = workspace / "outputs/_workshop/projects"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{identity}.json").write_text(
        json.dumps(
            {
                "id": identity,
                "revision": 4,
                "beeftv_canvas_id": canvas,
                "renders": renders or [],
                "deliverables": [{"path": "existing.mp4"}],
            }
        )
    )


@pytest.fixture
def fixture(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    project = "a" * 16
    other = "b" * 16
    (repository / "projects").mkdir()
    (repository / "projects/_registry.json").write_text(
        json.dumps(
            [
                {"id": "default", "name": "Default"},
                {"id": project, "name": "A"},
                {"id": other, "name": "B"},
            ]
        )
    )
    workspace = repository / "projects" / project
    other_workspace = repository / "projects" / other
    workspace.mkdir()
    other_workspace.mkdir()
    source = repository / ".scratch/beeftv/data"
    target = workspace / ".easel/beeftv/data"
    source.mkdir(parents=True)
    target.mkdir(parents=True)
    source_db = source / "open_ai_canvas.db"
    target_db = target / "open_ai_canvas.db"
    for path, owner in [(source_db, "source-owner"), (target_db, "target-owner")]:
        with sqlite3.connect(path) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO workspaces VALUES (?,?)", (owner, owner))
            db.execute(
                "INSERT INTO agent_op_records VALUES (?,?)",
                (owner, "private-auth-state"),
            )
    render = {
        "id": "render-one",
        "task_id": "c" * 32,
        "operation_id": "render-op-a",
        "engine_revision": 7,
        "canvas_id": "canvas-a",
        "status": "succeeded",
    }
    write_project(workspace, "1" * 32, "canvas-a", [render])
    write_project(other_workspace, "2" * 32, "canvas-b")
    doc = lambda name, resource, asset: {
        "id": name,
        "revision": 7,
        "nodes": [
            {
                "id": "node",
                "metadata": {
                    "storageKey": f"resource:{resource}",
                    "assetId": asset,
                    "url": f"http://127.0.0.1:8842/api/resources/{resource}/file",
                },
            }
        ],
        "connections": [],
    }
    with sqlite3.connect(source_db) as db:
        for canvas, resource, asset in [
            ("canvas-a", "ra", "aa"),
            ("canvas-b", "rb", "ab"),
        ]:
            db.execute(
                "INSERT INTO canvas_projects VALUES (?,?,?,?,?,?,?)",
                (
                    canvas,
                    "source-owner",
                    "",
                    "",
                    canvas,
                    json.dumps(doc(canvas, resource, asset)),
                    7,
                ),
            )
            db.execute(
                "INSERT INTO assets VALUES (?,?,?,?,?,?)",
                (
                    asset,
                    "source-owner",
                    "",
                    "",
                    asset,
                    json.dumps(
                        {"id": asset, "data": {"storageKey": "resource:" + resource}}
                    ),
                ),
            )
        db.execute(
            "INSERT INTO canvas_snapshots VALUES (?,?,?,?,?)",
            (
                "snap-a",
                "canvas-a",
                "source-owner",
                6,
                json.dumps(
                    {
                        "id": "canvas-a",
                        "nodes": [{"metadata": {"storageKey": "resource:rh"}}],
                    }
                ),
            ),
        )
        db.execute(
            "INSERT INTO canvas_snapshot_resources VALUES (?,?)", ("snap-a", "rh")
        )
        db.execute(
            "INSERT INTO asset_versions VALUES (?,?,?,?)", ("version-a", "aa", 1, "{}")
        )
        db.execute(
            "INSERT INTO asset_representations VALUES (?,?,?,?,?)",
            ("representation-a", None, "version-a", "rv", "{}"),
        )
        db.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
            (
                "c" * 32,
                "source-owner",
                "canvas-a",
                "assistant-render:render-op-a",
                "timeline_render",
                "succeeded",
                "{}",
                json.dumps({"resourceId": "rv"}),
            ),
        )
        db.execute(
            "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
            (
                "d" * 32,
                "source-owner",
                "canvas-b",
                "assistant-render:foreign",
                "timeline_render",
                "succeeded",
                "{}",
                "{}",
            ),
        )
        for resource in ["ra", "rb", "rh", "rv", "unassigned"]:
            content = ("owned-media-" + resource).encode()
            path = source / "resources" / f"{resource}.mp4"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(content)
            db.execute(
                "INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    resource,
                    "source-owner",
                    "video",
                    "ready",
                    "local",
                    f"{resource}.mp4",
                    f"http://127.0.0.1:8842/api/resources/{resource}/file",
                    len(content),
                    "video/mp4",
                    "none",
                    "",
                ),
            )
    run = workspace / ".easel/beeftv/run"
    run.mkdir()
    (run / "client.json").write_text('{"target-client":"kept-private"}')
    (target / "agent_owner_token").write_text("target-owner-secret")
    return repository, workspace, other_workspace, source, target


def rows(path, table):
    with sqlite3.connect(path / "open_ai_canvas.db") as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(f"SELECT * FROM {table}")]


def test_only_proven_canvas_graph_is_copied_with_unchanged_revision_and_owner_remap(
    fixture,
):
    repository, workspace, _, source, target = fixture
    result = migration().migrate_workspace(
        repository, workspace, copy_model_configuration=False
    )
    assert result["status"] == "complete"
    assert [row["id"] for row in rows(target, "canvas_projects")] == ["canvas-a"]
    assert rows(target, "canvas_projects")[0]["revision"] == 7
    assert rows(target, "canvas_projects")[0]["user_id"] == "target-owner"
    assert (
        json.loads(rows(target, "canvas_projects")[0]["payload_json"])["nodes"][0][
            "metadata"
        ]["url"]
        == "/api/resources/ra/file"
    )
    assert {row["id"] for row in rows(target, "resources")} == {"ra", "rh", "rv"}
    assert {row["id"] for row in rows(target, "assets")} == {"aa"}
    assert {row["id"] for row in rows(target, "tasks")} == {"c" * 32}
    assert rows(target, "agent_op_records") == [
        {"id": "target-owner", "result_json": "private-auth-state"}
    ]
    assert (target / "agent_owner_token").read_text() == "target-owner-secret"
    assert (
        target.parent / "run/client.json"
    ).read_text() == '{"target-client":"kept-private"}'
    for resource in ["ra", "rh", "rv"]:
        assert (target / "resources" / f"{resource}.mp4").read_bytes() == (
            source / "resources" / f"{resource}.mp4"
        ).read_bytes()
    assert not (target / "resources/rb.mp4").exists()
    assert not (target / "resources/unassigned.mp4").exists()
    assert len(rows(source, "canvas_projects")) == 2
    manifest = json.loads((target.parent / "run/migration.json").read_text())
    assert manifest["legacy_renders"] == [
        {
            "local_project_id": "1" * 32,
            "canvas_id": "canvas-a",
            "task_id": "c" * 32,
            "operation_id": "render-op-a",
            "engine_revision": 7,
        }
    ]
    assert "target-owner-secret" not in json.dumps(result)


def test_same_migration_is_idempotent_and_conflicting_human_edits_are_not_overwritten(
    fixture,
):
    repository, workspace, _, _, target = fixture
    module = migration()
    module.migrate_workspace(repository, workspace, copy_model_configuration=False)
    module.migrate_workspace(repository, workspace, copy_model_configuration=False)
    assert len(rows(target, "canvas_projects")) == 1
    with sqlite3.connect(target / "open_ai_canvas.db") as db:
        db.execute("UPDATE canvas_projects SET revision=8,title=?", ("Human edit",))
    with pytest.raises(module.MigrationError):
        module.migrate_workspace(repository, workspace, copy_model_configuration=False)
    assert rows(target, "canvas_projects")[0]["revision"] == 8


def test_canvas_claimed_by_two_ips_is_rejected_before_any_target_write(fixture):
    repository, workspace, other, _, target = fixture
    write_project(other, "3" * 32, "canvas-a")
    with pytest.raises(migration().MigrationError):
        migration().migrate_workspace(
            repository, workspace, copy_model_configuration=False
        )
    assert rows(target, "canvas_projects") == []


@pytest.mark.parametrize("failure", ["missing", "symlink", "traversal"])
def test_invalid_source_media_never_partially_installs_a_canvas(fixture, failure):
    repository, workspace, _, source, target = fixture
    media = source / "resources/ra.mp4"
    if failure == "missing":
        media.unlink()
    elif failure == "symlink":
        media.unlink()
        media.symlink_to(source / "resources/rb.mp4")
    else:
        with sqlite3.connect(source / "open_ai_canvas.db") as db:
            db.execute("UPDATE resources SET object_key='../outside.mp4' WHERE id='ra'")
    with pytest.raises(migration().MigrationError):
        migration().migrate_workspace(
            repository, workspace, copy_model_configuration=False
        )
    assert rows(target, "canvas_projects") == []
    assert not (target / "resources/ra.mp4").exists()


def test_a_running_destination_is_not_modified(fixture):
    repository, workspace, _, _, target = fixture
    (target.parent / "run/processes.json").write_text(
        json.dumps({"backend": {"pid": os.getpid()}})
    )
    with pytest.raises(migration().MigrationError):
        migration().migrate_workspace(
            repository, workspace, copy_model_configuration=False
        )
    assert rows(target, "canvas_projects") == []


def test_unfinished_tasks_are_kept_only_as_exact_legacy_receipts(fixture):
    repository, workspace, _, source, target = fixture
    with sqlite3.connect(source / "open_ai_canvas.db") as db:
        db.execute("UPDATE tasks SET status='running' WHERE id=?", ("c" * 32,))
    result = migration().migrate_workspace(
        repository, workspace, copy_model_configuration=False
    )
    assert result["status"] == "complete"
    assert rows(target, "tasks") == []
    assert result["legacy_renders"][0]["task_id"] == "c" * 32


def test_dry_run_reports_only_owned_graph_and_does_not_change_destination(fixture):
    repository, workspace, _, _, target = fixture
    before = (target / "open_ai_canvas.db").read_bytes()
    result = migration().migrate_workspace(
        repository, workspace, copy_model_configuration=False, dry_run=True
    )
    assert result["status"] == "planned"
    assert result["canvas_ids"] == ["canvas-a"]
    assert result["counts"]["resources"] == 3
    assert (target / "open_ai_canvas.db").read_bytes() == before
    assert not (target.parent / "run/migration.json").exists()


def test_a_foreign_task_reference_never_imports_another_canvas_history(fixture):
    repository, workspace, _, source, target = fixture
    with sqlite3.connect(source / "open_ai_canvas.db") as db:
        raw = json.loads(
            db.execute(
                "SELECT payload_json FROM canvas_projects WHERE id='canvas-a'"
            ).fetchone()[0]
        )
        raw["nodes"][0]["metadata"]["taskId"] = "d" * 32
        db.execute(
            "UPDATE canvas_projects SET payload_json=? WHERE id='canvas-a'",
            (json.dumps(raw),),
        )
        db.execute(
            "INSERT INTO task_logs VALUES (?,?,?,?)",
            ("foreign-log", "d" * 32, "source-owner", "Foreign prompt"),
        )
    migration().migrate_workspace(repository, workspace, copy_model_configuration=False)
    assert {row["id"] for row in rows(target, "tasks")} == {"c" * 32}
    assert rows(target, "task_logs") == []


def test_drawing_and_folder_cover_resource_columns_are_in_the_owned_graph(fixture):
    repository, workspace, _, source, target = fixture
    with sqlite3.connect(source / "open_ai_canvas.db") as db:
        db.execute(
            "INSERT INTO canvas_drawings VALUES (?,?,?,?,?,?)",
            ("source-owner", "canvas-a", "drawing-one", "{}", "unassigned", ""),
        )
        db.execute(
            "INSERT INTO canvas_library_folders VALUES (?,?,?,?)",
            ("folder-a", "source-owner", "Owned folder", "rb"),
        )
        raw = json.loads(
            db.execute(
                "SELECT payload_json FROM canvas_projects WHERE id='canvas-a'"
            ).fetchone()[0]
        )
        raw["folderId"] = "folder-a"
        db.execute(
            "UPDATE canvas_projects SET library_folder_id='folder-a',payload_json=? WHERE id='canvas-a'",
            (json.dumps(raw),),
        )
    migration().migrate_workspace(repository, workspace, copy_model_configuration=False)
    assert {row["id"] for row in rows(target, "resources")} == {
        "ra",
        "rh",
        "rv",
        "rb",
        "unassigned",
    }
    assert (target / "resources/unassigned.mp4").is_file()


def test_failure_to_publish_manifest_rolls_back_database_and_new_files(
    fixture, monkeypatch
):
    repository, workspace, _, _, target = fixture
    module = migration()
    monkeypatch.setattr(
        module,
        "_write_private",
        lambda *_args: (_ for _ in ()).throw(OSError("fixture failure")),
    )
    with pytest.raises(OSError):
        module.migrate_workspace(repository, workspace, copy_model_configuration=False)
    assert rows(target, "canvas_projects") == []
    assert rows(target, "resources") == []
    assert not (target / "resources/ra.mp4").exists()


def test_model_files_are_copied_without_copying_owner_credentials_or_brand_memory(
    fixture,
):
    repository, workspace, _, source, target = fixture
    (source / ".settings-key").write_bytes(b"source-model-key")
    (source / "local-model-config.json").write_text(
        '{"channels":[{"apiKey":"fixture-provider-secret"}]}'
    )
    (source / "agent_owner_token").write_text("source-owner-secret")
    (source / "agent-memory.json").write_text('{"private":"other brand"}')
    with sqlite3.connect(source / "open_ai_canvas.db") as db:
        db.execute(
            "INSERT INTO model_channels VALUES (?,?,?)",
            ("own-channel", "source-owner", "fixture-provider-secret"),
        )
        db.execute(
            "INSERT INTO model_channels VALUES (?,?,?)",
            ("foreign-channel", "unrelated-owner", "foreign-provider-secret"),
        )
        db.execute(
            "INSERT INTO system_settings VALUES (?,?,?)",
            ("plugin_registry", "{}", "source-owner"),
        )
        db.execute(
            "INSERT INTO system_settings VALUES (?,?,?)",
            ("brand-private", '{"private":"other brand"}', "source-owner"),
        )
    result = migration().migrate_workspace(repository, workspace)
    assert {row["id"] for row in rows(target, "model_channels")} == {"own-channel"}
    assert {row["key"] for row in rows(target, "system_settings")} == {
        "plugin_registry"
    }
    assert (target / "local-model-config.json").read_text() == (
        source / "local-model-config.json"
    ).read_text()
    assert (target / ".settings-key").read_bytes() == b"source-model-key"
    assert (target / "agent_owner_token").read_text() == "target-owner-secret"
    assert not (target / "agent-memory.json").exists()
    assert "provider-secret" not in json.dumps(result)


def test_an_explicit_fresh_runtime_can_replace_only_its_seed_plugin_registry(fixture):
    repository, workspace, _, source, target = fixture
    for data, registry in [
        (source, '{"builtin":true,"customH3":true}'),
        (target, '{"builtin":true}'),
    ]:
        with sqlite3.connect(data / "open_ai_canvas.db") as db:
            db.execute(
                "INSERT INTO system_settings VALUES (?,?,?)",
                (
                    "plugin_registry",
                    registry,
                    "source-owner" if data == source else "target-owner",
                ),
            )
    module = migration()
    with pytest.raises(module.MigrationError):
        module.migrate_workspace(repository, workspace)
    preview = module.migrate_workspace(
        repository, workspace, dry_run=True, replace_initialized_configuration=True
    )
    assert preview["configuration_replacements"] == ["plugin_registry"]
    assert json.loads(rows(target, "system_settings")[0]["value_json"]) == {
        "builtin": True
    }
    module.migrate_workspace(
        repository, workspace, replace_initialized_configuration=True
    )
    assert json.loads(rows(target, "system_settings")[0]["value_json"]) == {
        "builtin": True,
        "customH3": True,
    }


def test_seed_replacement_never_overwrites_configuration_of_a_used_runtime(fixture):
    repository, workspace, _, source, target = fixture
    for data in [source, target]:
        with sqlite3.connect(data / "open_ai_canvas.db") as db:
            db.execute(
                "INSERT INTO system_settings VALUES (?,?,?)",
                (
                    "plugin_registry",
                    "{}" if data == source else '{"human":true}',
                    "source-owner",
                ),
            )
    with sqlite3.connect(target / "open_ai_canvas.db") as db:
        db.execute(
            "INSERT INTO assets VALUES (?,?,?,?,?,?)",
            ("human-asset", "target-owner", "", "", "Human", "{}"),
        )
    with pytest.raises(migration().MigrationError):
        migration().migrate_workspace(
            repository, workspace, replace_initialized_configuration=True
        )
    assert json.loads(rows(target, "system_settings")[0]["value_json"]) == {
        "human": True
    }


def test_a_canvas_changed_during_copy_is_not_published_as_the_new_workspace(
    fixture, monkeypatch
):
    repository, workspace, _, source, target = fixture
    module = migration()
    original_copy = module.shutil.copyfile
    changed = False

    def copy_then_edit(path, destination, *args, **kwargs):
        nonlocal changed
        result = original_copy(path, destination, *args, **kwargs)
        if not changed and Path(destination).suffix == ".mp4":
            with sqlite3.connect(source / "open_ai_canvas.db") as db:
                db.execute("UPDATE canvas_projects SET revision=8 WHERE id='canvas-a'")
            changed = True
        return result

    monkeypatch.setattr(module.shutil, "copyfile", copy_then_edit)
    with pytest.raises(module.MigrationError):
        module.migrate_workspace(repository, workspace, copy_model_configuration=False)
    assert rows(target, "canvas_projects") == []
    assert not (target / "resources/ra.mp4").exists()


@pytest.mark.parametrize(
    "filename,root_attribute",
    [("media_import.py", "PROJECT_ROOT"), ("configure_h3.py", "PROJECT")],
)
def test_video_tools_use_the_selected_private_runtime_and_never_legacy_fallback(
    fixture, monkeypatch, filename, root_attribute
):
    repository, workspace, _, _, _ = fixture
    from easel.beeftv_runtime import ensure_instance, runtime_paths, save_private_json

    paths = runtime_paths(repository, workspace)
    instance = ensure_instance(paths)
    selected = {
        **instance,
        "base_url": instance["api_url"] + "/api",
        "client_id": "selected-client",
        "client_token": "selected-private",
    }
    save_private_json(paths.connection, selected)
    legacy = repository / ".scratch/beeftv/run/client.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        '{"base_url":"http://127.0.0.1:8842/api","client_id":"legacy","client_token":"legacy-private"}'
    )
    script = Path(__file__).parents[1] / "integrations/beeftv" / filename
    spec = importlib.util.spec_from_file_location(
        "migration_tool_" + filename.replace(".", "_"), script
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, root_attribute, repository)
    monkeypatch.setenv("EASEL_ROOT", str(workspace))
    assert hasattr(module, "runtime_client"), (
        "Video tools still select the shared legacy connection"
    )
    client = module.runtime_client()
    assert client.base_url == instance["api_url"] + "/api"
    assert client.headers["X-Beeftv-Client"] == "selected-client"
    paths.connection.unlink()
    with pytest.raises(RuntimeError):
        module.runtime_client()
