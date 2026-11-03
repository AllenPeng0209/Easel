"""Cookie authentication and per-IP authorization at the original HTTP boundary.

Mount outside ProjectScopeMiddleware: neither legacy default-IP aliases nor a
URL rewrite may escape the same authorization decision. Unknown member routes
fail closed until their capability is classified here.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_PROJECT_ID = re.compile(r"default|[a-f0-9]{16}")
_PUBLIC = frozenset({("GET", "/api/auth/bootstrap"),
                     ("POST", "/api/auth/login"),
                     ("POST", "/api/auth/setup")})


def role_permissions(role: str | None, is_admin: bool = False) -> dict[str, bool]:
    """One vocabulary for server decisions and UI capability hints."""
    member = role in {"owner", "operator", "viewer"}
    operator = role in {"owner", "operator"}
    return {"view": member, "operate": operator, "manage_accounts": role == "owner",
            "manage_members": role == "owner", "manage_ip": role == "owner",
            "manage_platform": bool(is_admin)}


def _project_resource(path: str) -> tuple[str | None, str, str]:
    """Return IP, normalized resource and registry/member special handling."""
    if path == "/api/projects":
        return None, "projects", "registry"
    if path.startswith("/api/projects/"):
        suffix = path[len("/api/projects/"):]
        identifier, separator, resource = suffix.partition("/")
        if not _PROJECT_ID.fullmatch(identifier):
            return None, "", "invalid"
        if not separator:
            return identifier, "", "project"
        if resource == "members" or resource.startswith("members/"):
            return identifier, resource, "members"
        # Registry routes must never be recursively reinterpreted after a rewrite.
        if resource == "projects" or resource.startswith("projects/"):
            return None, "", "invalid"
        return identifier, resource, "resource"
    return "default", path.removeprefix("/api/"), "resource"


def _same_origin(request: Request) -> bool:
    if request.headers.get("sec-fetch-site", "").lower() in {"cross-site", "same-site"}:
        return False
    origin = request.headers.get("origin")
    if origin is None:
        # Cookie authentication remains mandatory. Non-browser clients may omit
        # Origin; browsers' cross-site requests carry Sec-Fetch-Site above.
        return True
    try:
        source, target = urlsplit(origin), urlsplit(str(request.url))
        if (source.scheme not in {"http", "https"} or not source.hostname
                or source.username or source.password or source.query or source.fragment
                or source.path not in {"", "/"}):
            return False
        source_port = source.port or (443 if source.scheme == "https" else 80)
        target_port = target.port or (443 if target.scheme == "https" else 80)
        return (source.scheme, source.hostname, source_port) == (target.scheme, target.hostname, target_port)
    except ValueError:
        return False


def _output_parts(value: str) -> list[str] | None:
    """Reject encoded traversal before role checking, not after normalization."""
    decoded = value
    for _ in range(5):
        if (not decoded or decoded.startswith(("/", "\\")) or "\\" in decoded
                or any(ord(character) < 32 for character in decoded)):
            return None
        parts = decoded.split("/")
        if any(not part or part.startswith(".") for part in parts):
            return None
        expanded = unquote(decoded)
        if expanded == decoded:
            return parts
        decoded = expanded
    return None


def _output_allowed(role: str, resource: str, request: Request) -> bool:
    if resource.startswith("media/"):
        value = resource[len("media/"):]
    elif resource.startswith("output/"):
        value = resource[len("output/"):]
    elif resource == "artifacts/archive":
        value = request.query_params.get("path", "")
    else:
        return True
    parts = _output_parts(value)
    if parts is None:
        return False
    top = parts[0]
    if not top.startswith("_") or top == "_inbox":
        return True
    # Stored transcripts, debug logs and databases have controlled APIs; the
    # generic file routes are never another route to those protected records.
    if top in {"_sessions", "_debug", "_interactions", "_access", "_harness"}:
        return False
    if role == "owner":
        return top in {"_login", "_publish", "_analytics", "_profile_build"}
    # An operator may download an explicitly created manual publication package,
    # while QR codes, status files and account browser data remain owner-only.
    return role == "operator" and top == "_publish" and parts[-1].lower().endswith(".zip")


def _is_global(resource: str) -> bool:
    return (resource in {"env", "clipper", "skill", "workshop/status"}
            or resource.startswith(("settings/", "env/", "gateway/")))


def _capability(method: str, resource: str, kind: str) -> str | None:
    """Classify existing endpoints, including GETs that start tools or write state."""
    read = method in {"GET", "HEAD"}
    if kind == "members":
        return "view" if read else "manage_members"
    if kind == "project":
        return "view" if read else "manage_ip"
    if resource == "chat/sessions" or re.fullmatch(r"chat/sessions/[^/]+", resource):
        return "operate"
    if re.fullmatch(r"chat/subagents/[^/]+/[^/]+", resource):
        return "operate" if read else None
    if resource in {'chat/business-objects', 'chat/session-reference', 'chat/commands', 'chat/attachment-search'}:
        return 'operate' if read else None
    if resource in {"upload/validate", "chat/attachment-select"}:
        return "operate" if method == "POST" else None
    if re.fullmatch(r'publication-approvals/[a-f0-9]{32}(?:/decision)?', resource):
        return 'operate' if read or method == 'POST' else None
    if re.fullmatch(r'publish/[^/]+/prepare', resource):
        return 'operate' if method == 'POST' else None
    if resource == 'chat/artifact-related' or re.fullmatch(
            r"chat/artifacts/[a-f0-9]{32}(?:/(?:content|related|versions|compare|archive)|/archive/[0-9]+/(?:content|related))?", resource):
        return "operate" if read else None
    if (resource.startswith(("login/", "logout/"))
            or re.fullmatch(r"accounts/[^/]+/(?:credentials|mp-login(?:/status)?)", resource)
            or (not read and re.fullmatch(r"interactions/channels(?:/[^/]+)?", resource))):
        return "manage_accounts"
    if read:
        if re.fullmatch(r"accounts/[^/]+/whoami|analytics/(?!platforms$)[^/]+", resource):
            return "operate"
        if resource in {"status", "personas", "skills", "outputs", "artifacts/archive", "accounts",
                        "analytics/platforms", "publishing-policy", "upload/limits", "schedule",
                        "schedule/context", "ideas", "trends", "interactions/bootstrap", "workshop/projects"}:
            return "view"
        if (re.fullmatch(r"persona/[^/]+(?:/files)?", resource)
                or re.fullmatch(r"skill/[^/]+", resource)
                or re.fullmatch(r"chat/last/[^/]+|chat/jobs/[^/]+/stream", resource)
                or re.fullmatch(r"publish/[^/]+/status|profile/build/status/[^/]+", resource)
                or re.fullmatch(r"interactions/conversations/[^/]+", resource)
                or re.fullmatch(r"workshop/projects/[^/]+(?:/renders/[^/]+)?", resource)
                or resource.startswith(("output/", "media/"))):
            return "view"
        return None
    if method == "OPTIONS":
        # Authenticated same-origin preflights have no business mutation.
        return "view"
    if (resource in {"chat", "chat/stream", "chat/stop", "chat/steer", "chat/question/answer", "chat/question/status",
                     "upload", "upload/local", "profile/build", "schedule", "ideas", "workshop/projects"}
            or re.fullmatch(r"persona/[^/]+(?:/file)?|session/[^/]+", resource)
            or re.fullmatch(r"schedule/[^/]+|ideas/[^/]+|publish/[^/]+(?:/sms)?|manual-publish/[^/]+/export", resource)
            or resource.startswith("output/")
            or re.fullmatch(r"workshop/projects/[^/]+(?:/(?:comments|canvas|reviews|archive|publications|deliverables|renders)(?:/[^/]+/collect)?)?", resource)
            or re.fullmatch(r"interactions/(?:clients|channels|channels/[^/]+(?:/sync)?|simulation|conversations/[^/]+/(?:draft|action)|drafts/[^/]+(?:/(?:approve|send))?|knowledge(?:/[^/]+)?|rules|automation/run)", resource)):
        return "operate"
    return None


class AccessControlMiddleware:
    def __init__(self, app, get_root):
        self.app, self.get_root = app, get_root

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"].rstrip("/") or "/"
        if path != "/api" and not path.startswith("/api/"):
            return await self.app(scope, receive, send)
        request = Request(scope)
        method = request.method
        streaming = False
        stream_closed = False
        live_access = None

        async def reject(status: int, detail: str):
            return await JSONResponse({"detail": detail}, status_code=status,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)

        async def protected_send(message):
            nonlocal streaming, stream_closed
            if stream_closed:
                return
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() not in {b"cache-control", b"x-content-type-options"}]
                streaming = any(key.lower() == b"content-type" and value.lower().startswith(b"text/event-stream")
                                for key, value in headers)
                headers.extend([(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")])
                message = {**message, "headers": headers}
            elif (streaming and message["type"] == "http.response.body" and message.get("body")
                  and live_access is not None and not live_access()):
                # The initial role check cannot authorize an hours-long stream.
                # Finish on the first chunk after logout, disabling or revocation;
                # do not forward a stale token or an already-queued private event.
                stream_closed = True
                return await send({"type": "http.response.body", "more_body": False,
                                   "body": 'event: error\ndata: "访问权限已变化，请重新登录或打开空间"\n\n'.encode("utf-8")})
            await send(message)

        cross_origin = request.headers.get("sec-fetch-site", "").lower() in {"cross-site", "same-site"}
        if (method not in _SAFE_METHODS or cross_origin) and not _same_origin(request):
            return await reject(403, "请从当前工作台提交操作")
        if (method, path) in _PUBLIC:
            return await self.app(scope, receive, protected_send)
        token = request.cookies.get("easel_session", "")
        if not token:
            return await reject(401, "请先登录")
        from easel.access import AccessStore
        store = AccessStore(self.get_root())
        user = store.authenticate_token(token)
        if not user or not user.get("active", False):
            return await reject(401, "登录已失效，请重新登录")
        expected_user = request.headers.get("x-easel-user")
        if expected_user and expected_user != user["id"]:
            return await reject(401, "登录账号已变化，请重新打开当前空间")
        scope.setdefault("state", {})["user"] = user
        project, resource, kind = _project_resource(path)
        is_admin = bool(user.get("is_admin"))
        if path in {"/api/auth/me", "/api/auth/logout", "/api/users/directory"}:
            allowed = ((path in {"/api/auth/me", "/api/users/directory"} and method in {"GET", "HEAD"})
                       or (path in {"/api/auth/logout"} and method == "POST"))
            if not allowed:
                return await reject(403, "当前账号没有执行此操作的权限")
        elif path == "/api/users" or path.startswith("/api/users/"):
            if not is_admin:
                return await reject(403, "只有平台管理员可以管理用户")
        elif kind == "registry":
            if method not in {"GET", "HEAD", "POST"}:
                return await reject(403, "当前账号没有执行此操作的权限")
        elif kind == "invalid":
            return await reject(403, "无权访问该 IP")
        elif _is_global(resource):
            if not is_admin:
                return await reject(403, "只有平台管理员可以操作平台设置或本机工具")
            if resource in {"clipper", "skill", "workshop/status"}:
                role = store.role(user["id"], project)
                if role not in {"owner", "operator", "viewer"}:
                    return await reject(403, "你不是该 IP 的成员")
                scope["state"].update(project_role=role, project_id=project)
        else:
            role = store.role(user["id"], project)
            if role not in {"owner", "operator", "viewer"}:
                return await reject(403, "你不是该 IP 的成员")
            capability = _capability(method, resource, kind)
            if capability is None or not role_permissions(role, is_admin).get(capability, False):
                return await reject(403, "当前角色没有执行此操作的权限")
            if not _output_allowed(role, resource, request):
                return await reject(403, "该文件仅能通过受控的工作区接口读取")
            scope["state"].update(project_role=role, project_id=project)

        def still_authorized():
            current = store.authenticate_token(token)
            if not current or not current.get("active", False) or current["id"] != user["id"]:
                return False
            if _is_global(resource):
                if not current.get("is_admin"):
                    return False
            if scope["state"].get("project_id") is not None:
                current_role = store.role(current["id"], project)
                required = _capability(method, resource, kind)
                if _is_global(resource):
                    return current_role in {"owner", "operator", "viewer"}
                return bool(required and role_permissions(current_role, bool(current.get("is_admin"))).get(required))
            return True

        live_access = still_authorized
        from easel.request_identity import actor_context
        with actor_context(user):
            await self.app(scope, receive, protected_send)
