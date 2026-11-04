"""Embedded social operations, with a database, model registry and engine per IP.

The upstream adapters rely on module state. Each owned runtime loads the pinned
package into its own Python namespace and ORM registry. Requests and background
workers therefore retain the same explicit project owner for their lifetime.
No HTTP service, environment mutation or working-directory switch is involved.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
from pathlib import Path
import re
import sys
import uuid


VENDOR = Path(__file__).parent / "vendor" / "creatorhub"
PROJECT_ID = re.compile(r"(?:default|[a-f0-9]{16})")


class OperationsRuntime:
    def __init__(self, root: Path, project: str, namespace: str):
        if not PROJECT_ID.fullmatch(project):
            raise ValueError("IP 编号无效")
        workspace = root if project == "default" else root / "projects" / project
        self.directory = workspace / ".easel" / "operations"
        if self.directory.is_symlink():
            raise ValueError("运营数据目录不能使用符号链接")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.directory.resolve().is_relative_to(workspace.resolve()):
            raise ValueError("运营数据目录必须位于当前 IP 内")
        self.namespace = namespace
        self.project = project
        self.lock = asyncio.Lock()
        self.lifespan = None
        self.started = False
        specification = importlib.util.spec_from_file_location(
            namespace, VENDOR / "__init__.py", submodule_search_locations=[str(VENDOR)]
        )
        package = importlib.util.module_from_spec(specification)
        sys.modules[namespace] = package
        specification.loader.exec_module(package)
        config = importlib.import_module(namespace + ".config")
        settings = config.Config()
        settings.engine.interaction_only = True
        settings.db_path = str(self.directory / "operations.sqlite3")
        settings.engine.profiles_dir = str(self.directory / "profiles")
        settings.engine.media_dir = str(self.directory / "media")
        # This function belongs exclusively to this package instance. The
        # original loader remains available in the pinned source for migration.
        config.load_config = lambda path=None: settings
        self.module = importlib.import_module(namespace + ".main")
        self.module.app.user_middleware.clear()
        self.app = self.module.app
        self.module.init_db(settings.db_path)
        Path(settings.db_path).chmod(0o600)
        self.workspace = workspace
        from easel.account_connections import AccountConnections

        self.connections = AccountConnections(self)

    async def start(self):
        async with self.lock:
            if not self.started:
                lifespan = self.module.lifespan(self.app)
                try:
                    await lifespan.__aenter__()
                except BaseException:
                    if self.module.engine is not None:
                        await self.module.engine.stop()
                    if self.module.im_receiver is not None:
                        await self.module.im_receiver.stop_all()
                    task = self.module._webhook_delivery_task
                    if task is not None:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    if self.module.browser is not None:
                        await self.module.browser.stop()
                    raise
                self.lifespan = lifespan
                self.started = True

    async def close(self):
        await self.connections.close()
        async with self.lock:
            if self.lifespan is not None:
                await self.lifespan.__aexit__(None, None, None)
                self.lifespan = None
            self.started = False
            signer = sys.modules.get(self.namespace + ".platforms.xhs.creator_sign")
            if signer is not None:
                signer.close_signers()
            database = importlib.import_module(self.namespace + ".db")
            if database._engine is not None:
                database._engine.dispose()


class OperationsManager:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.namespace = "easel._operations_" + uuid.uuid4().hex
        self.runtimes: dict[str, OperationsRuntime] = {}
        self.lock = asyncio.Lock()

    async def get(self, project: str, *, start: bool = True) -> OperationsRuntime:
        async with self.lock:
            if project not in self.runtimes:
                self.runtimes[project] = OperationsRuntime(
                    self.root, project, self.namespace + "_" + project
                )
            runtime = self.runtimes[project]
        if start:
            await runtime.start()
        return runtime

    async def import_local(self, project: str, source_root: Path):
        from easel.operations_migration import import_snapshot

        runtime = await self.get(project, start=False)
        async with runtime.lock:
            if runtime.started:
                raise ValueError("请在启动该 IP 运营引擎前完成迁移")
            return await asyncio.to_thread(import_snapshot, runtime, source_root)

    async def resume(self):
        from easel.projects import _registry

        for item in _registry(self.root):
            project = item["id"]
            workspace = (
                self.root if project == "default" else self.root / "projects" / project
            )
            if (workspace / ".easel/operations/operations.sqlite3").is_file():
                await self.get(project)

    async def close(self):
        for runtime in tuple(self.runtimes.values()):
            await runtime.close()
        for name in list(sys.modules):
            if name.startswith(self.namespace + "_"):
                del sys.modules[name]
        self.runtimes.clear()
