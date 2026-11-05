"""Identity and IP membership endpoints. Authentication is always server-side."""
from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from easel.access import AccessStore, ROLES, SESSION_COOKIE, SESSION_SECONDS


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)


class SetupInput(LoginInput):
    display_name: str = Field(default="", max_length=80)


class UserInput(SetupInput):
    is_admin: StrictBool = False


class UserUpdate(Input):
    display_name: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, max_length=1024)
    active: StrictBool | None = None
    is_admin: StrictBool | None = None


class MemberRole(Input):
    role: Literal["owner", "operator", "viewer"]


class NewMember(MemberRole):
    user_id: str = Field(min_length=1, max_length=64)


def _origin_tuple(value: str):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        return parsed.scheme, parsed.hostname.casefold(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def _write_origin(request: Request):
    """JSON clients may omit Origin; a supplied browser Origin must match exactly."""
    origin = request.headers.get("origin")
    expected = _origin_tuple(f"{request.url.scheme}://{request.url.netloc}")
    if origin is not None and (_origin_tuple(origin) is None or _origin_tuple(origin) != expected):
        raise HTTPException(403, "请从当前 响芽 页面提交此操作")
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(403, "不允许跨站提交此操作")


def _local_setup(request: Request):
    peer = request.client.host if request.client else ""
    try:
        local = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "首次管理员初始化需要从服务器本机打开 响芽")


def _actor(request: Request, store: AccessStore, *, admin: bool = False) -> dict:
    actor = getattr(request.state, "user", None)
    if not isinstance(actor, dict) or not actor.get("id"):
        raise HTTPException(401, "请先登录")
    # Do not trust a stale role from an earlier request or a browser header.
    current = store.user(actor["id"])
    if current is None or not current["active"]:
        raise HTTPException(401, "登录已失效，请重新登录")
    if admin and not current["is_admin"]:
        raise HTTPException(403, "需要平台管理员权限")
    return current


def _session_cookie(response: Response, request: Request, token: str):
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"


def create_router(get_root, get_projects) -> APIRouter:
    router = APIRouter()

    def store():
        return AccessStore(get_root())

    def ip_exists(identifier):
        if not any(project.get("id") == identifier for project in get_projects()):
            raise HTTPException(404, "IP 不存在或没有访问权限")

    def ip_actor(request, identifier, allowed):
        current_store = store()
        actor = _actor(request, current_store)
        current_store.require_role(actor["id"], identifier, allowed)
        ip_exists(identifier)
        return current_store, actor

    @router.get("/api/auth/bootstrap")
    def bootstrap(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return {"configured": store().configured()}

    @router.post("/api/auth/setup")
    def setup(req: SetupInput, request: Request, response: Response):
        _write_origin(request)
        _local_setup(request)
        current_store = store()
        user, token = current_store.setup(req.username, req.password, req.display_name,
                                          [project["id"] for project in get_projects()])
        current_store.audit(user["id"], "auth.setup")
        _session_cookie(response, request, token)
        return {"user": user}

    @router.post("/api/auth/login")
    def login(req: LoginInput, request: Request, response: Response):
        _write_origin(request)
        current_store = store()
        peer = request.client.host if request.client else "unknown"
        user, token = current_store.login(req.username, req.password, peer=peer)
        current_store.audit(user["id"], "auth.login")
        _session_cookie(response, request, token)
        return {"user": user}

    @router.get("/api/auth/me")
    def me(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return _actor(request, store())

    @router.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        _write_origin(request)
        store().logout(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                               secure=request.url.scheme == "https", samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return {"ok": True}

    # This static route must precede /api/users/{user_id}.
    @router.get("/api/users/directory")
    def directory(request: Request, response: Response):
        current_store = store()
        _actor(request, current_store)
        response.headers["Cache-Control"] = "no-store"
        return current_store.directory()

    @router.get("/api/users")
    def users(request: Request, response: Response):
        current_store = store()
        _actor(request, current_store, admin=True)
        response.headers["Cache-Control"] = "no-store"
        return current_store.users()

    @router.post("/api/users")
    def user_create(req: UserInput, request: Request):
        _write_origin(request)
        current_store = store()
        actor = _actor(request, current_store, admin=True)
        user = current_store.create_user(req.username, req.password, req.display_name,
                                         is_admin=req.is_admin, actor_id=actor["id"])
        current_store.audit(actor["id"], "user.create", target_id=user["id"])
        return user

    @router.patch("/api/users/{user_id}")
    def user_update(user_id: str, req: UserUpdate, request: Request):
        _write_origin(request)
        current_store = store()
        actor = _actor(request, current_store, admin=True)
        user = current_store.update_user(user_id, **req.model_dump(exclude_unset=True), actor_id=actor["id"])
        current_store.audit(actor["id"], "user.update", target_id=user_id)
        return user

    @router.get("/api/projects/{identifier}/members")
    def members(identifier: str, request: Request, response: Response):
        current_store, _ = ip_actor(request, identifier, ROLES)
        response.headers["Cache-Control"] = "no-store"
        return current_store.members(identifier)

    @router.post("/api/projects/{identifier}/members")
    def member_create(identifier: str, req: NewMember, request: Request):
        _write_origin(request)
        current_store, actor = ip_actor(request, identifier, {"owner"})
        member = current_store.set_member(identifier, req.user_id, req.role, actor_id=actor["id"])
        current_store.audit(actor["id"], "member.set", identifier, req.user_id)
        return member

    @router.patch("/api/projects/{identifier}/members/{user_id}")
    def member_update(identifier: str, user_id: str, req: MemberRole, request: Request):
        _write_origin(request)
        current_store, actor = ip_actor(request, identifier, {"owner"})
        if not any(member["user_id"] == user_id for member in current_store.members(identifier)):
            raise HTTPException(404, "成员不存在")
        member = current_store.set_member(identifier, user_id, req.role, actor_id=actor["id"])
        current_store.audit(actor["id"], "member.set", identifier, user_id)
        return member

    @router.delete("/api/projects/{identifier}/members/{user_id}")
    def member_delete(identifier: str, user_id: str, request: Request):
        _write_origin(request)
        current_store, actor = ip_actor(request, identifier, {"owner"})
        current_store.remove_member(identifier, user_id, actor_id=actor["id"])
        current_store.audit(actor["id"], "member.remove", identifier, user_id)
        return {"ok": True}

    return router
