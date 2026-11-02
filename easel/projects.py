"""Local creator spaces. Scope is per request/task, never a process-wide selection."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import MutableMapping, MutableSet
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

_project = ContextVar("easel_project", default="default")
_root = ContextVar("easel_project_repository", default=None)
_registry_lock = threading.RLock()


def project_id() -> str:
    return _project.get()


@contextmanager
def project_context(identifier: str, root: Path | None = None):
    token = _project.set(identifier)
    root_token = _root.set(root)
    try:
        yield
    finally:
        _root.reset(root_token)
        _project.reset(token)


def workspace(repository: Path) -> Path:
    root = _root.get() or repository
    return root if project_id() == "default" else root / "projects" / project_id()


def space_path(repository: Path, legacy: Path, relative: str) -> Path:
    return legacy if project_id() == "default" else workspace(repository) / relative


def scoped_key(key):
    return key if project_id() == "default" else (project_id(), key)


class ProjectMap(MutableMapping):
    """Keep in-flight work and identity caches separate even with identical IDs."""
    def __init__(self):
        self.data = {}

    def __getitem__(self, key):
        return self.data[scoped_key(key)]

    def __setitem__(self, key, value):
        self.data[scoped_key(key)] = value

    def __delitem__(self, key):
        del self.data[scoped_key(key)]

    def __iter__(self):
        identifier = project_id()
        return iter([key[1] if isinstance(key, tuple) else key for key in self.data
                     if (isinstance(key, tuple) and key[0] == identifier)
                     or (not isinstance(key, tuple) and identifier == "default")])

    def __len__(self):
        return sum(1 for _ in self)


class ProjectSet(MutableSet):
    def __init__(self):
        self.data = set()

    def __contains__(self, key):
        return scoped_key(key) in self.data

    def __iter__(self):
        return iter([key[1] if isinstance(key, tuple) else key for key in self.data
                     if (isinstance(key, tuple) and key[0] == project_id())
                     or (not isinstance(key, tuple) and project_id() == "default")])

    def __len__(self):
        return sum(1 for _ in self)

    def add(self, key):
        self.data.add(scoped_key(key))

    def discard(self, key):
        self.data.discard(scoped_key(key))


def context_thread(*, target, args=(), **kwargs):
    context = copy_context()
    return threading.Thread(target=lambda: context.run(target, *args), **kwargs)


def _registry(root: Path) -> list[dict]:
    path = root / "projects" / "_registry.json"
    if path.exists():
        # Corruption must not silently overwrite or orphan existing projects.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("invalid registry")
            return [{**item, "name": "默认 IP"} if item.get("id") == "default"
                    and item.get("name") == "默认项目" else item for item in data]
        except (ValueError, OSError):
            raise HTTPException(500, "IP 列表无法读取，请检查本地数据文件") from None
    return [{"id": "default", "name": "默认 IP", "description": "原有账号、对话与内容", "createdAt": 0}]


def _save(root: Path, items: list[dict]):
    path = root / "projects" / "_registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _validate(name: str, description: str):
    if not name.strip() or len(name.strip()) > 60 or any(ord(c) < 32 for c in name):
        raise HTTPException(400, "IP 名称需为 1–60 个字符")
    if len(description) > 500:
        raise HTTPException(400, "IP 简介不能超过 500 个字符")


class ProjectInput(BaseModel):
    name: str
    description: str = ""


class ProjectUpdate(BaseModel):
    name: str
    description: str | None = None


def create_router(get_root):
    router = APIRouter()

    @router.get("/api/projects")
    async def projects_list(request: Request):
        from easel.access import AccessStore
        from web.access_control import role_permissions
        actor = getattr(request.state, 'user', None)
        if not actor:
            raise HTTPException(401, '请先登录')
        access = AccessStore(get_root())
        with _registry_lock:
            result = []
            for item in _registry(get_root()):
                role = access.role(actor['id'], item['id'])
                if role:
                    result.append({**item, 'role': role, 'permissions': role_permissions(role, actor['is_admin'])})
            return result

    @router.post("/api/projects")
    async def projects_create(req: ProjectInput, request: Request):
        from easel.access import AccessStore
        actor = getattr(request.state, 'user', None)
        if not actor:
            raise HTTPException(401, '请先登录')
        _validate(req.name, req.description)
        root = get_root()
        with _registry_lock:
            items = _registry(root)
            item = {"id": uuid.uuid4().hex[:16], "name": req.name.strip(),
                    "description": req.description.strip(), "createdAt": int(time.time())}
            directory = root / "projects" / item["id"]
            directory.mkdir(parents=True, mode=0o700)
            for name in ("outputs", "profiles", "browser"):
                (directory / name).mkdir(mode=0o700)
            # Reuse tools, but keep all writable creator data inside this space.
            for name in ("skills", "scripts", ".venv", "agents"):
                if (root / name).exists():
                    (directory / name).symlink_to(root / name, target_is_directory=True)
            documents = root / 'agents' / 'workspace'
            for document in documents.glob('*.md'):
                (directory / document.name).write_text(document.read_text(encoding='utf-8'), encoding='utf-8')
            items.append(item)
            AccessStore(root).add_owner(item['id'], actor['id'])
            _save(root, items)
            return {**item, 'role': 'owner'}

    @router.patch("/api/projects/{identifier}")
    async def projects_update(identifier: str, req: ProjectUpdate, request: Request):
        from easel.access import AccessStore
        actor = getattr(request.state, 'user', None)
        if not actor:
            raise HTTPException(401, '请先登录')
        AccessStore(get_root()).require_role(actor['id'], identifier, {'owner'})
        _validate(req.name, req.description or "")
        with _registry_lock:
            items = _registry(get_root())
            item = next((item for item in items if item["id"] == identifier), None)
            if item is None:
                raise HTTPException(404, "IP 不存在")
            item["name"] = req.name.strip()
            if req.description is not None:
                item["description"] = req.description.strip()
            _save(get_root(), items)
            return item

    return router


class ProjectScopeMiddleware:
    """URL-scoped requests also work for native images, downloads and SSE streams."""
    def __init__(self, app, get_root):
        self.app, self.get_root = app, get_root

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        match = re.fullmatch(r"/api/projects/([^/]+)/(.+)", path)
        identifier = "default"
        if match:
            identifier, resource = match.groups()
            if not re.fullmatch(r"default|[a-f0-9]{16}", identifier) or not any(
                    item["id"] == identifier for item in _registry(self.get_root())):
                return await JSONResponse({"detail": "IP 不存在"}, status_code=404)(scope, receive, send)
            # Do not expose registry operations inside a project URL.
            if resource == "projects" or resource.startswith("projects/"):
                return await JSONResponse({"detail": "无效 IP 路径"}, status_code=404)(scope, receive, send)
            if resource != 'members' and not resource.startswith('members/'):
                scope = {**scope, "path": "/api/" + resource,
                         "raw_path": ("/api/" + resource).encode("utf-8")}
        with project_context(identifier, self.get_root()):
            await self.app(scope, receive, send)
