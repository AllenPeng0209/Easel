"""Selectively move proven workshop data into a stopped, private BeefTV runtime.

The old database is read-only. The destination must have been initialized by its
own runtime, so its owner/client credentials remain independent. Only rows
reachable from locally owned canvas IDs are staged; no source database clone is
ever published to an IP. Existing different destination rows are never replaced.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import sqlite3
import tempfile
from urllib.parse import urlsplit


class MigrationError(RuntimeError):
    """The selective migration could not preserve a verified boundary."""


_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
_IP = re.compile(r"(?:default|[a-f0-9]{16})")
_RESOURCE_PATH = re.compile(
    r"/api/resources/([A-Za-z0-9_-]{1,80})(/file(?:/[^?#]*)?)?(?:[?#].*)?$"
)
_TERMINAL = {"succeeded", "failed", "cancelled", "canceled"}
_CONFIG_TABLES = (
    "model_channels",
    "channel_models",
    "channel_model_variants",
    "logical_models",
    "logical_model_revisions",
    "logical_model_routes",
    "user_plugin_states",
)
_BUSINESS_ORDER = (
    "resources",
    "asset_folders",
    "canvas_library_folders",
    "assets",
    "asset_versions",
    "asset_representations",
    "tasks",
    "results",
    "task_logs",
    "canvas_projects",
    "canvas_drawings",
    "canvas_snapshots",
    "canvas_snapshot_resources",
)


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _regular(path):
    if path.is_symlink() or not path.is_file():
        raise MigrationError("迁移来源缺失或含符号链接，请保留原数据后核对")


def _confined(root, relative, *, exists=True):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise MigrationError("迁移文件路径无效")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(part in {"..", ".", ""} for part in parts.parts):
        raise MigrationError("迁移文件不能越过当前工作目录")
    path = root
    for part in parts.parts:
        path /= part
        if path.is_symlink():
            raise MigrationError("迁移文件不能经过符号链接")
    if not path.resolve().is_relative_to(root.resolve()):
        raise MigrationError("迁移文件不能越过当前工作目录")
    if exists:
        _regular(path)
    return path


def _locations(repository, workspace, source_data):
    repository = Path(repository).expanduser().absolute()
    workspace = Path(workspace).expanduser().absolute()
    if repository.resolve() != repository or workspace.resolve() != workspace:
        raise MigrationError("迁移工作目录不能使用符号链接或相对越界路径")
    if workspace == repository:
        identifier = "default"
    elif workspace.parent == repository / "projects" and _IP.fullmatch(workspace.name):
        identifier = workspace.name
    else:
        raise MigrationError("迁移目标不属于当前 Easel IP")
    registry = repository / "projects/_registry.json"
    if registry.exists():
        try:
            projects = json.loads(registry.read_text(encoding="utf-8"))
            if identifier not in {item["id"] for item in projects}:
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise MigrationError("无法确认目标 IP 的归属") from None
    elif identifier != "default":
        raise MigrationError("无法确认目标 IP 的归属")
    source = Path(source_data or repository / ".scratch/beeftv/data").absolute()
    if source.resolve() != source or not source.is_relative_to(repository):
        raise MigrationError("旧创作数据必须在当前 Easel 安装目录内")
    target = workspace / ".easel/beeftv/data"
    for path in (source, target):
        if path.resolve() != path or not path.is_dir():
            raise MigrationError("请先初始化独立创作服务并停止它，再迁移旧数据")
    if source == target:
        raise MigrationError("迁移来源与目标不能相同")
    _regular(source / "open_ai_canvas.db")
    _regular(target / "open_ai_canvas.db")
    return repository, workspace, identifier, source, target


def _metadata(repository, workspace):
    registry = repository / "projects/_registry.json"
    entries = (
        json.loads(registry.read_text()) if registry.exists() else [{"id": "default"}]
    )
    claims, local = {}, []
    for entry in entries:
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not _IP.fullmatch(identifier):
            raise MigrationError("IP 清单包含无效身份")
        owner = (
            repository
            if identifier == "default"
            else repository / "projects" / identifier
        )
        directory = owner / "outputs/_workshop/projects"
        if directory.is_symlink():
            raise MigrationError("工坊归属目录不能使用符号链接")
        for path in sorted(directory.glob("*.json")):
            _regular(path)
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                canvas = record.get("beeftv_canvas_id")
                if not canvas:
                    continue
                if not isinstance(canvas, str) or not _ID.fullmatch(canvas):
                    raise ValueError
                project = record["id"]
                if not isinstance(project, str) or not re.fullmatch(
                    r"[a-f0-9]{32}", project
                ):
                    raise ValueError
                if canvas in claims and claims[canvas] != (identifier, project):
                    raise MigrationError("同一画布被多个 IP 或主题声明，未迁移任何数据")
                claims[canvas] = (identifier, project)
                if owner == workspace:
                    local.append(record)
            except (OSError, ValueError, TypeError, KeyError):
                raise MigrationError(
                    "无法读取工坊画布归属，请保留原数据后核对"
                ) from None
    return local


@contextmanager
def _read_only(path):
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        yield db
    finally:
        db.close()


def _schema(db):
    result = {}
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        if re.fullmatch(r"[a-z_]+", table):
            columns = db.execute(f'PRAGMA table_info("{table}")').fetchall()
            result[table] = {
                "columns": [row[1] for row in columns],
                "pk": [
                    row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]
                ],
            }
    return result


def _query(db, schema, table, field=None, values=()):
    if table not in schema:
        return []
    columns = schema[table]["columns"]
    if field is not None:
        if field not in columns or not values:
            return []
        result = []
        values = sorted(set(values))
        for offset in range(0, len(values), 200):
            batch = values[offset : offset + 200]
            sql = f'SELECT * FROM "{table}" WHERE "{field}" IN ({",".join("?" for _ in batch)})'
            result.extend(dict(row) for row in db.execute(sql, batch))
        return result
    return [dict(row) for row in db.execute(f'SELECT * FROM "{table}"')]


def _references(value, resources, assets, tasks, key=""):
    if isinstance(value, dict):
        for child_key, child in value.items():
            _references(child, resources, assets, tasks, child_key)
    elif isinstance(value, list):
        for child in value:
            _references(child, resources, assets, tasks, key)
    elif isinstance(value, str):
        if value.startswith("resource:"):
            identifier = value.removeprefix("resource:")
            if not _ID.fullmatch(identifier):
                raise MigrationError("画布包含无效素材引用")
            resources.add(identifier)
        match = _RESOURCE_PATH.search(value)
        if match:
            resources.add(match[1])
        if value and key in {
            "assetId",
            "asset_id",
            "sourceAssetId",
            "primaryAssetId",
            "assetIds",
        }:
            if not _ID.fullmatch(value):
                raise MigrationError("画布包含无效素材身份")
            assets.add(value)
        if value and key in {"taskId", "task_id", "taskIds"}:
            if not _ID.fullmatch(value):
                raise MigrationError("画布包含无效任务身份")
            tasks.add(value)
        if value and (
            key in {"resourceId", "resource_id", "resourceIds"}
            or key.endswith(("ResourceId", "_resource_id"))
        ):
            if not _ID.fullmatch(value):
                raise MigrationError("画布包含无效素材身份")
            resources.add(value)


def _scan(row, resources, assets, tasks):
    _references(row, resources, assets, tasks)
    for key, value in row.items():
        if key.endswith("_json") and value:
            try:
                _references(json.loads(value), resources, assets, tasks)
            except (ValueError, TypeError):
                raise MigrationError("画布引用数据无法读取") from None


def _rewrite(value):
    if isinstance(value, dict):
        return {key: _rewrite(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_rewrite(child) for child in value]
    if isinstance(value, str):
        match = (
            _RESOURCE_PATH.fullmatch(value) or _RESOURCE_PATH.search(value)
            if value.startswith(("http://", "https://", "/api/resources/"))
            else None
        )
        if match:
            return "/api/resources/" + match[1] + (match[2] or "")
    return value


def _without_unowned_tasks(value, owned_tasks):
    if isinstance(value, dict):
        return {
            key: (
                [item for item in child if item in owned_tasks]
                if key == "taskIds" and isinstance(child, list)
                else _without_unowned_tasks(child, owned_tasks)
            )
            for key, child in value.items()
            if not (
                key in {"taskId", "task_id"}
                and isinstance(child, str)
                and child
                and child not in owned_tasks
            )
        }
    if isinstance(value, list):
        return [_without_unowned_tasks(child, owned_tasks) for child in value]
    return value


def _configuration_rows(db, schema, owner):
    channels = [
        row
        for row in _query(db, schema, "model_channels")
        if row.get("user_id") == owner
        or (
            not row.get("user_id")
            and row.get("scope") in {"platform", "global", "system"}
        )
    ]
    models = _query(
        db, schema, "channel_models", "channel_id", {row["id"] for row in channels}
    )
    routes = _query(
        db,
        schema,
        "logical_model_routes",
        "channel_model_id",
        {row["id"] for row in models},
    )
    revisions = _query(
        db,
        schema,
        "logical_model_revisions",
        "id",
        {row["logical_model_revision_id"] for row in routes},
    )
    logical = _query(
        db,
        schema,
        "logical_models",
        "id",
        {row["logical_model_id"] for row in revisions},
    )
    linked = _query(
        db,
        schema,
        "logical_models",
        "source_channel_model_id",
        {row["id"] for row in models},
    )
    logical = {row["id"]: row for row in [*logical, *linked]}
    revisions = _query(
        db, schema, "logical_model_revisions", "logical_model_id", set(logical)
    )
    return {
        "model_channels": channels,
        "channel_models": models,
        "channel_model_variants": _query(
            db,
            schema,
            "channel_model_variants",
            "channel_model_id",
            {row["id"] for row in models},
        ),
        "logical_models": list(logical.values()),
        "logical_model_revisions": revisions,
        "logical_model_routes": routes,
        "user_plugin_states": _query(
            db, schema, "user_plugin_states", "user_id", {owner}
        ),
    }


def _owner(db, schema):
    values = _query(db, schema, "workspaces")
    if len(values) != 1 or not isinstance(values[0].get("id"), str):
        raise MigrationError("无法确认创作服务的独立 owner 身份")
    return values[0]["id"]


def _source_canvas_versions(canvases):
    return [
        {
            "id": row["id"],
            "revision": row["revision"],
            "sha256": hashlib.sha256(
                _json(
                    {key: row[key] for key in ("id", "revision", "payload_json")}
                ).encode()
            ).hexdigest(),
        }
        for row in sorted(canvases, key=lambda row: row["id"])
    ]


def _assert_source_unchanged(source, versions):
    with _read_only(source / "open_ai_canvas.db") as db:
        current = _query(
            db, _schema(db), "canvas_projects", "id", {row["id"] for row in versions}
        )
        if _source_canvas_versions(current) != versions:
            raise MigrationError("旧画布在迁移期间发生修改，未接管过时版本")


def _offline(target):
    run = target.parent / "run"
    path = run / "processes.json"
    if path.exists():
        _regular(path)
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            raise MigrationError("无法核实目标创作服务是否已停止") from None

        def running(value):
            if isinstance(value, dict):
                pid = value.get("pid")
                if type(pid) is int and pid > 0:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        pass
                    except (PermissionError, OSError):
                        raise MigrationError("无法核实目标进程，未迁移数据") from None
                    else:
                        raise MigrationError("请先停止目标 IP 创作服务，再迁移数据")
                for child in value.values():
                    running(child)
            elif isinstance(value, list):
                for child in value:
                    running(child)

        running(state)
    instance = run / "instance.json"
    if instance.exists():
        _regular(instance)
        try:
            descriptor = json.loads(instance.read_text())
            ports = {
                value
                for key, value in descriptor.items()
                if key in {"api_port", "ui_port", "apiPort", "uiPort"}
                and type(value) is int
            }
            for key in ("api_url", "ui_url", "base_url"):
                if descriptor.get(key):
                    parsed = urlsplit(descriptor[key])
                    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                        raise ValueError
                    if parsed.port:
                        ports.add(parsed.port)
        except (OSError, ValueError, TypeError):
            raise MigrationError("独立运行实例记录无效") from None
        for port in ports:
            if not 0 < port < 65536:
                raise MigrationError("独立运行实例端口无效")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    raise MigrationError("目标 IP 的创作端口仍在使用，未迁移数据")
            except OSError:
                pass


def _legacy_renders(records):
    result = []
    for project in records:
        for receipt in project.get("renders") or []:
            entry = {
                "local_project_id": project["id"],
                "canvas_id": project["beeftv_canvas_id"],
                "task_id": receipt.get("task_id"),
                "operation_id": receipt.get("operation_id"),
                "engine_revision": receipt.get("engine_revision"),
            }
            if (
                not isinstance(entry["task_id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", entry["task_id"])
                or not isinstance(entry["operation_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", entry["operation_id"])
                or type(entry["engine_revision"]) is not int
                or entry["engine_revision"] < 0
                or receipt.get("canvas_id", entry["canvas_id"]) != entry["canvas_id"]
            ):
                continue
            if entry not in result:
                result.append(entry)
    return sorted(result, key=lambda entry: (entry["canvas_id"], entry["task_id"]))


def _plan(
    repository,
    workspace,
    identifier,
    source,
    target,
    copy_model_configuration,
    replace_initialized_configuration=False,
):
    records = _metadata(repository, workspace)
    owned = {record["beeftv_canvas_id"] for record in records}
    selected = {}
    files = {}
    with (
        _read_only(source / "open_ai_canvas.db") as old,
        _read_only(target / "open_ai_canvas.db") as current,
    ):
        source_schema, target_schema = _schema(old), _schema(current)
        source_owner, target_owner = (
            _owner(old, source_schema),
            _owner(current, target_schema),
        )
        resources, assets, tasks = set(), set(), set()

        def add(table, rows):
            if not rows:
                return
            bucket = selected.setdefault(table, {})
            columns = source_schema[table]["columns"]
            keys = source_schema[table]["pk"] or columns
            for row in rows:
                if (
                    table not in _CONFIG_TABLES
                    and "user_id" in row
                    and row["user_id"] != source_owner
                ):
                    raise MigrationError("引用数据不属于旧创作工作区 owner")
                bucket[tuple(row[key] for key in keys)] = row
                _scan(row, resources, assets, tasks)

        canvases = _query(old, source_schema, "canvas_projects", "id", owned)
        if {row["id"] for row in canvases} != owned:
            raise MigrationError("旧服务缺少已关联画布，未替换本地绑定")
        if any(row.get("project_id") for row in canvases):
            raise MigrationError("该画布关联了独立项目，需要先核对项目归属再迁移")
        for row in canvases:
            try:
                document = json.loads(row["payload_json"])
                if (
                    document.get("id") != row["id"]
                    or document.get("projectId", "") not in ("", None)
                    or (document.get("folderId") or "")
                    != (row.get("library_folder_id") or "")
                    or (
                        "revision" in document
                        and document["revision"] != row["revision"]
                    )
                ):
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise MigrationError("旧画布文档与归属记录不一致，未迁移数据") from None
        add("canvas_projects", canvases)
        add(
            "canvas_snapshots",
            _query(old, source_schema, "canvas_snapshots", "canvas_id", owned),
        )
        add(
            "canvas_drawings",
            _query(old, source_schema, "canvas_drawings", "canvas_id", owned),
        )
        snapshots = {row["id"] for row in selected.get("canvas_snapshots", {}).values()}
        add(
            "canvas_snapshot_resources",
            _query(
                old,
                source_schema,
                "canvas_snapshot_resources",
                "snapshot_id",
                snapshots,
            ),
        )
        folders = {row.get("library_folder_id") for row in canvases} - {"", None}
        add(
            "canvas_library_folders",
            _query(old, source_schema, "canvas_library_folders", "id", folders),
        )
        canvas_tasks = _query(old, source_schema, "tasks", "project_id", owned)
        tasks.update(row["id"] for row in canvas_tasks)
        seen_assets, seen_resources, seen_tasks = set(), set(), set()
        while (
            (assets - seen_assets)
            or (resources - seen_resources)
            or (tasks - seen_tasks)
        ):
            if len(assets) + len(resources) + len(tasks) > 50000:
                raise MigrationError("迁移引用数量超出安全范围")
            pending_assets = assets - seen_assets
            if pending_assets:
                rows = _query(old, source_schema, "assets", "id", pending_assets)
                if {row["id"] for row in rows} != pending_assets:
                    raise MigrationError("画布引用的素材资料缺失")
                seen_assets.update(pending_assets)
                add("assets", rows)
                add(
                    "asset_folders",
                    _query(
                        old,
                        source_schema,
                        "asset_folders",
                        "id",
                        {row.get("folder_id") for row in rows} - {"", None},
                    ),
                )
                versions = _query(
                    old, source_schema, "asset_versions", "asset_id", pending_assets
                )
                add("asset_versions", versions)
                add(
                    "asset_representations",
                    _query(
                        old,
                        source_schema,
                        "asset_representations",
                        "asset_version_id",
                        {row["id"] for row in versions},
                    ),
                )
            pending_tasks = tasks - seen_tasks
            if pending_tasks:
                seen_tasks.update(pending_tasks)
                terminal = [
                    row
                    for row in _query(old, source_schema, "tasks", "id", pending_tasks)
                    if row.get("status") in _TERMINAL and row.get("project_id") in owned
                ]
                add("tasks", terminal)
                terminal_ids = {row["id"] for row in terminal}
                add(
                    "task_logs",
                    _query(old, source_schema, "task_logs", "task_id", terminal_ids),
                )
                add(
                    "results",
                    _query(old, source_schema, "results", "task_id", terminal_ids),
                )
            pending_resources = resources - seen_resources
            if pending_resources:
                rows = _query(old, source_schema, "resources", "id", pending_resources)
                if {row["id"] for row in rows} != pending_resources:
                    raise MigrationError("画布引用的媒体文件记录缺失")
                seen_resources.update(pending_resources)
                for row in rows:
                    if row.get("provider") != "local" or row.get("status") != "ready":
                        raise MigrationError("画布引用的素材尚未保存在本机，未迁移数据")
                    relative = "resources/" + str(row.get("object_key") or "")
                    media = _confined(source, relative)
                    if media.stat().st_size != row.get("size"):
                        raise MigrationError("素材文件大小与旧记录不一致")
                    files[relative] = {
                        "source": media,
                        "sha256": _digest(media),
                        "bytes": media.stat().st_size,
                        "configuration": False,
                    }
                    if row.get("playback_status") == "ready" and row.get(
                        "playback_object_key"
                    ):
                        relative = "playback/" + str(row["playback_object_key"])
                        playback = _confined(source, relative)
                        files[relative] = {
                            "source": playback,
                            "sha256": _digest(playback),
                            "bytes": playback.stat().st_size,
                            "configuration": False,
                        }
                    elif row.get("playback_status") == "processing":
                        row.update(playback_status="none", playback_object_key="")
                add("resources", rows)

        if copy_model_configuration:
            for table, rows in _configuration_rows(
                old, source_schema, source_owner
            ).items():
                if rows:
                    selected[table] = {
                        tuple(row[key] for key in source_schema[table]["pk"]): row
                        for row in rows
                    }
            settings = _query(
                old, source_schema, "system_settings", "key", {"plugin_registry"}
            )
            if settings:
                selected["system_settings"] = {(row["key"],): row for row in settings}
            for name in (".settings-key", "local-model-config.json"):
                path = source / name
                if path.exists():
                    _regular(path)
                    files[name] = {
                        "source": path,
                        "sha256": _digest(path),
                        "bytes": path.stat().st_size,
                        "configuration": True,
                    }
            packages = source / "plugin-packages"
            if packages.is_symlink():
                raise MigrationError("模型插件目录不能使用符号链接")
            for path in sorted(packages.glob("*.beeftv-plugin")):
                _regular(path)
                relative = "plugin-packages/" + path.name
                files[relative] = {
                    "source": path,
                    "sha256": _digest(path),
                    "bytes": path.stat().st_size,
                    "configuration": True,
                }

        normalized = {}
        replacements = {}
        owned_tasks = {row["id"] for row in selected.get("tasks", {}).values()}
        unused_configuration = (
            not (target.parent / "run/migration.json").exists()
            and not (target / "local-model-config.json").exists()
            and not any(
                _query(current, target_schema, table)
                for table in (
                    "canvas_projects",
                    "assets",
                    "resources",
                    "tasks",
                    "model_channels",
                )
            )
        )
        for table, bucket in selected.items():
            if table not in target_schema or set(
                source_schema[table]["columns"]
            ) != set(target_schema[table]["columns"]):
                raise MigrationError("新旧创作服务数据库版本不同，未迁移数据")
            rows = []
            for raw in bucket.values():
                row = deepcopy(raw)
                for key in ("user_id", "updated_by", "created_by"):
                    if row.get(key) == source_owner:
                        row[key] = target_owner
                for key, value in row.items():
                    if key.endswith("_json") and value:
                        try:
                            row[key] = _json(
                                _rewrite(
                                    _without_unowned_tasks(
                                        json.loads(value), owned_tasks
                                    )
                                )
                            )
                        except (ValueError, TypeError):
                            raise MigrationError("迁移内容数据无法规范化") from None
                    elif isinstance(value, str):
                        row[key] = _rewrite(value)
                if (
                    table == "asset_representations"
                    and row.get("task_id") not in owned_tasks
                ):
                    row["task_id"] = None
                keys = source_schema[table]["pk"] or source_schema[table]["columns"]
                matches = current.execute(
                    f'SELECT * FROM "{table}" WHERE '
                    + " AND ".join(f'"{key}" IS ?' for key in keys),
                    tuple(row[key] for key in keys),
                ).fetchall()
                if matches and dict(matches[0]) != row:
                    if (
                        table == "system_settings"
                        and row.get("key") == "plugin_registry"
                        and copy_model_configuration
                        and replace_initialized_configuration
                        and unused_configuration
                    ):
                        replacements.setdefault(table, set()).add(
                            tuple(row[key] for key in keys)
                        )
                    else:
                        raise MigrationError("目标已有不同内容，未覆盖人工修改")
                rows.append(row)
            normalized[table] = rows
        for relative, info in files.items():
            destination = _confined(target, relative, exists=False)
            if destination.exists():
                _regular(destination)
                if _digest(destination) != info["sha256"]:
                    # A newly initialized runtime may create its encryption key
                    # before any model config. Only that unused key may change.
                    unused_key = (
                        relative == ".settings-key"
                        and not (target / "local-model-config.json").exists()
                        and not _query(current, target_schema, "model_channels")
                        and not _query(current, target_schema, "canvas_projects")
                    )
                    if not unused_key:
                        raise MigrationError("目标已有不同文件，未覆盖人工修改")
                    info["replace_unused_key"] = True
        counts = {table: len(rows) for table, rows in normalized.items()}
        manifest = {
            "version": 1,
            "project_id": identifier,
            "workspace": str(workspace),
            "status": "planned",
            "canvas_ids": sorted(owned),
            "counts": counts,
            "source_canvas_versions": _source_canvas_versions(canvases),
            "resource_bytes": sum(
                info["bytes"] for info in files.values() if not info["configuration"]
            ),
            "media": [
                {"path": relative, "sha256": info["sha256"], "bytes": info["bytes"]}
                for relative, info in sorted(files.items())
                if not info["configuration"]
            ],
            "configuration_copied": bool(copy_model_configuration),
            "configuration_replacements": ["plugin_registry"] if replacements else [],
            "unmigrated_task_references": len(seen_tasks - owned_tasks),
            "legacy_renders": _legacy_renders(records),
        }
        return normalized, files, target_schema, manifest, replacements


def _write_private(path, value):
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + path.name + "-", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def migrate_workspace(
    repository: Path,
    workspace: Path,
    *,
    source_data: Path | None = None,
    copy_model_configuration: bool = True,
    dry_run: bool = False,
    replace_initialized_configuration: bool = False,
) -> dict:
    """Stage only proven rows and media; source and local Host records stay intact.

    The initialized destination runtime must be stopped. ``dry_run`` performs
    only reads and does not create migration markers or change database state.
    Queued/running jobs are never imported, retried, canceled or submitted.
    """
    repository, workspace, identifier, source, target = _locations(
        repository, workspace, source_data
    )
    from easel.beeftv_runtime import read_instance, runtime_lock, runtime_paths

    paths = runtime_paths(repository, workspace)
    read_instance(paths)
    if dry_run:
        _offline(target)
        return _plan(
            repository,
            workspace,
            identifier,
            source,
            target,
            copy_model_configuration,
            replace_initialized_configuration,
        )[3]
    run = target.parent / "run"
    with runtime_lock(paths):
        _offline(target)
        rows, files, schema, manifest, replacements = _plan(
            repository,
            workspace,
            identifier,
            source,
            target,
            copy_model_configuration,
            replace_initialized_configuration,
        )
        installed, replaced = [], []
        with tempfile.TemporaryDirectory(prefix=".migration-", dir=run) as temporary:
            stage = Path(temporary)
            stage.chmod(0o700)
            database = target / "open_ai_canvas.db"
            staged_db = stage / "open_ai_canvas.db"
            original_db_copy = stage / "original.sqlite3"
            with (
                closing(sqlite3.connect(database)) as original,
                closing(sqlite3.connect(staged_db)) as destination,
            ):
                if original.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise MigrationError("目标数据库仍在使用，未迁移数据")
                original.backup(destination)
                with closing(sqlite3.connect(original_db_copy)) as rollback:
                    original.backup(rollback)
                original_db_copy.chmod(0o600)
                destination.execute("PRAGMA foreign_keys=ON")
                destination.execute("BEGIN IMMEDIATE")
                destination.execute("PRAGMA defer_foreign_keys=ON")
                try:
                    for table in (*_CONFIG_TABLES, "system_settings", *_BUSINESS_ORDER):
                        for row in rows.get(table, []):
                            keys = schema[table]["pk"] or schema[table]["columns"]
                            found = destination.execute(
                                f'SELECT 1 FROM "{table}" WHERE '
                                + " AND ".join(f'"{key}" IS ?' for key in keys),
                                tuple(row[key] for key in keys),
                            ).fetchone()
                            if not found:
                                columns = schema[table]["columns"]
                                destination.execute(
                                    f'INSERT INTO "{table}" ('
                                    + ",".join(f'"{key}"' for key in columns)
                                    + ") VALUES ("
                                    + ",".join("?" for _ in columns)
                                    + ")",
                                    tuple(row[key] for key in columns),
                                )
                            elif tuple(row[key] for key in keys) in replacements.get(
                                table, set()
                            ):
                                columns = schema[table]["columns"]
                                destination.execute(
                                    f'UPDATE "{table}" SET '
                                    + ",".join(f'"{key}"=?' for key in columns)
                                    + " WHERE "
                                    + " AND ".join(f'"{key}" IS ?' for key in keys),
                                    tuple(row[key] for key in columns)
                                    + tuple(row[key] for key in keys),
                                )
                    if destination.execute("PRAGMA foreign_key_check").fetchall():
                        raise MigrationError("迁移引用完整性检查失败")
                    destination.commit()
                    if (
                        destination.execute("PRAGMA integrity_check").fetchone()[0]
                        != "ok"
                    ):
                        raise MigrationError("迁移数据库完整性检查失败")
                    destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except BaseException:
                    destination.rollback()
                    raise
            staged_db.chmod(0o600)
            # Verify every byte before publishing any database reference.
            for relative, info in files.items():
                staged_file = _confined(stage, relative, exists=False)
                staged_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(info["source"], staged_file)
                staged_file.chmod(0o600)
                if _digest(staged_file) != info["sha256"]:
                    raise MigrationError("旧素材在迁移期间发生变化，未接管目标数据")
            replaced_database = False
            try:
                for relative, info in files.items():
                    destination = _confined(target, relative, exists=False)
                    if destination.exists() and not info.get("replace_unused_key"):
                        continue
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if destination.exists():
                        backup = stage / ("rollback-" + str(len(replaced)))
                        shutil.copyfile(destination, backup)
                        backup.chmod(0o600)
                        replaced.append((destination, backup))
                    else:
                        installed.append(destination)
                    os.replace(stage / relative, destination)
                _offline(target)
                _assert_source_unchanged(source, manifest["source_canvas_versions"])
                backup = run / "before-migration.sqlite3"
                if backup.is_symlink() or (backup.exists() and not backup.is_file()):
                    raise MigrationError("迁移备份路径无效")
                if not backup.exists():
                    with (
                        closing(sqlite3.connect(database)) as original,
                        closing(sqlite3.connect(backup)) as copy,
                    ):
                        original.backup(copy)
                    backup.chmod(0o600)
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(database) + suffix)
                    if sidecar.is_symlink():
                        raise MigrationError("目标数据库边界发生变化")
                    sidecar.unlink(missing_ok=True)
                os.replace(staged_db, database)
                replaced_database = True
                manifest["status"] = "complete"
                _write_private(run / "migration.json", manifest)
            except BaseException:
                if replaced_database:
                    os.replace(original_db_copy, database)
                for path in reversed(installed):
                    path.unlink(missing_ok=True)
                for path, backup in reversed(replaced):
                    os.replace(backup, path)
                raise
        return manifest
