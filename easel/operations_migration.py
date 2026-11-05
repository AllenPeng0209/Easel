"""Snapshot a locally installed CreatorHub into an owned Xiangya runtime."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

import yaml


PATH_COLUMNS = {"profile_dir", "local_path", "download_dir", "output_dir", "media_json"}
PAUSE_TABLES = (
    "monitortarget",
    "commentwatch",
    "danmakuwatch",
    "commentrule",
    "dmautoreplyrule",
    "webhooksubscription",
    "notificationchannel",
)


def import_snapshot(runtime, source_root: Path) -> dict:
    source_root = Path(source_root).resolve()
    config = (
        yaml.safe_load((source_root / "config.yaml").read_text(encoding="utf-8")) or {}
    )

    def source_path(value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else source_root / path).resolve()

    database = source_path(
        config.get("storage", {}).get("db_path", "data/creatorhub.db")
    )
    profiles = source_path(
        config.get("engine", {}).get("profiles_dir", "data/profiles")
    )
    media = source_path(config.get("engine", {}).get("media_dir", "data/media"))
    if not database.is_file() or database.is_symlink():
        raise ValueError("本机 CreatorHub 数据库不存在或不是普通文件")
    with runtime.module.get_session() as current:
        if current.exec(
            __import__("sqlmodel").select(runtime.module.DouyinAccount)
        ).first():
            raise ValueError("当前 IP 已有运营账号，请先核对已有数据")
    module_db = __import__(runtime.namespace + ".db", fromlist=["_engine"])
    module_db._engine.dispose()
    with tempfile.TemporaryDirectory(
        prefix="import-", dir=runtime.directory
    ) as temporary:
        stage = Path(temporary)
        target = stage / "operations.sqlite3"
        with (
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as old,
            sqlite3.connect(target) as new,
        ):
            old.backup(new)
        destinations = (
            (profiles, "profiles"),
            (media, "media"),
            (source_root / "data/uploads", "uploads"),
        )
        replacements = {}
        for original, name in destinations:
            if original.is_dir():
                for entry in original.rglob("*"):
                    if entry.is_symlink() and not entry.name.startswith("Singleton"):
                        raise ValueError("迁移目录含符号链接，请先核对来源")
                shutil.copytree(
                    original,
                    stage / name,
                    ignore=shutil.ignore_patterns(
                        "Singleton*",
                        "Cache",
                        "Code Cache",
                        "GPUCache",
                        "Crashpad",
                        "*.lock",
                    ),
                )
                replacements[str(original)] = str(runtime.directory / name)

        def move_path(value):
            if isinstance(value, list):
                return [move_path(item) for item in value]
            if isinstance(value, str):
                candidate = (
                    str(source_path(value))
                    if value and not Path(value).is_absolute()
                    else value
                )
                for before, after in replacements.items():
                    if candidate == before or candidate.startswith(before + "/"):
                        return after + candidate[len(before) :]
            return value

        with sqlite3.connect(target) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table in tables:
                if not table.replace("_", "").isalnum():
                    continue
                columns = {
                    row[1] for row in db.execute(f'PRAGMA table_info("{table}")')
                }
                for column in columns & PATH_COLUMNS:
                    for identity, value in db.execute(
                        f'SELECT rowid,"{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
                    ).fetchall():
                        if not isinstance(value, str):
                            continue
                        try:
                            parsed = (
                                json.loads(value) if column == "media_json" else value
                            )
                        except ValueError:
                            parsed = value
                        changed = move_path(parsed)
                        updated = (
                            json.dumps(changed, ensure_ascii=False)
                            if isinstance(changed, list)
                            else changed
                        )
                        db.execute(
                            f'UPDATE "{table}" SET "{column}"=? WHERE rowid=?',
                            (updated, identity),
                        )
                if table in PAUSE_TABLES and "enabled" in columns:
                    db.execute(f'UPDATE "{table}" SET enabled=0')
                if table in {"publishtask", "accountactiontask", "commenttask"}:
                    state = "draft" if table == "publishtask" else "canceled"
                    db.execute(
                        f"UPDATE \"{table}\" SET status=? WHERE status NOT IN ('done','canceled','uncertain')",
                        (state,),
                    )
            if "appsetting" in tables:
                db.execute(
                    "DELETE FROM appsetting WHERE key IN ('download_dir','xhs_dm_auto_reply_enabled')"
                )
            count = db.execute("SELECT COUNT(*) FROM douyinaccount").fetchone()[0]
        for _, name in destinations:
            existing = runtime.directory / name
            if (
                (stage / name).exists()
                and existing.exists()
                and any(existing.iterdir())
            ):
                raise ValueError("迁移目标浏览器或素材目录已有数据")
        destination = runtime.directory / "operations.sqlite3"
        backup = runtime.directory / "before-import.sqlite3"
        shutil.copy2(destination, backup)
        for _, name in destinations:
            incoming = stage / name
            if incoming.exists():
                existing = runtime.directory / name
                if existing.exists() and any(existing.iterdir()):
                    raise ValueError("迁移目标浏览器或素材目录已有数据")
                existing.mkdir(exist_ok=True)
                shutil.copytree(incoming, existing, dirs_exist_ok=True)
        target.replace(destination)
        destination.chmod(0o600)
        runtime.module.init_db(str(destination))
        result = {
            "accounts": count,
            "project": runtime.project,
            "automation": "paused_for_review",
        }
        (runtime.directory / "import-receipt.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        return result
