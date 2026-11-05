"""Project-scoped in-process API and UI for Xiangya's embedded social engine."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException

from easel.host_state import publication_state_directory
from easel.projects import project_id
from easel.publication_approvals import (
    PublicationApprovalError,
    PublicationApprovalStore,
)


OWNER_PATH = re.compile(r"/api/(?:login(?:/|$)|accounts(?:/|$))")
FILE_FIELDS = {
    "download_dir",
    "output_dir",
    "local_path",
    "profile_dir",
    "profiles_dir",
    "media_dir",
}


def interaction_path(method: str, path: str) -> bool:
    if path in {"/status", "/inbox/options"}:
        return method in {"GET", "HEAD"}
    if path == "/approvals":
        return method == "POST"
    if re.fullmatch(r"/approvals/[a-f0-9]{32}", path):
        return method == "POST"
    if method in {"GET", "HEAD"}:
        return bool(
            re.fullmatch(
                r"/api/(?:accounts|accounts/connect/poll|login/browser/poll|account-works|account-works/\d+/comments|dm/(?:inbox|conversations|messages|stream)|account-actions|comment-tasks)",
                path,
            )
        )
    if method == "POST":
        return bool(
            re.fullmatch(
                r"/api/(?:accounts/connect/start|accounts/\d+/(?:refresh-profile|works/sync|dm/sync)|account-works/\d+/comments/sync|account-actions(?:/\d+/(?:approve|run-now|cancel))?|comment-tasks/\d+/(?:approve|run-now|cancel))",
                path,
            )
        )
    return False


def operations_capability(method: str, resource: str):
    if resource == "operations/status":
        return "view"
    path = "/" + resource.removeprefix("operations/").lstrip("/")
    if OWNER_PATH.match(path) and not (
        path == "/api/accounts" and method in {"GET", "HEAD"}
    ):
        return "manage_accounts"
    return "operate"


def needs_approval(method: str, path: str) -> bool:
    if method not in {"POST", "PUT", "PATCH"} or path.endswith(
        ("/cancel", "/batch-delete")
    ):
        return False
    return bool(
        re.match(
            r"/api/(?:account-actions(?:/|$)|comment-tasks(?:/|$))",
            path,
        )
        and path != "/api/publish/upload"
    )


def validate_paths(runtime, values):
    allowed = (runtime.directory.resolve(), (runtime.workspace / "outputs").resolve())

    def validate(value):
        if not isinstance(value, str):
            raise ValueError("文件路径格式无效")
        resolved = Path(value).expanduser().resolve()
        if not any(resolved == root or root in resolved.parents for root in allowed):
            raise ValueError("文件和下载目录必须位于当前 IP 的运营目录或内容库内")

    def visit(item):
        if not isinstance(item, dict):
            return
        for name, value in item.items():
            if name in FILE_FIELDS and value:
                validate(value)
            elif name == "media_paths" and isinstance(value, list):
                for path in value:
                    validate(path)
                    if Path(path).suffix.lower() not in {
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".webp",
                        ".gif",
                        ".mp4",
                        ".mov",
                        ".webm",
                        ".mp3",
                        ".wav",
                        ".m4a",
                    }:
                        raise ValueError("发布素材必须为媒体文件")
            elif isinstance(value, dict):
                visit(value)

    visit(values)


def operation_subject(runtime, actor, project, method, path, body):
    module = runtime.module
    snapshot = None
    match = re.fullmatch(
        r"/api/(publish|account-actions|comment-tasks|comment-rules)/(\d+)(?:/(?:approve|run-now))?",
        path,
    )
    if match:
        model = {
            "publish": module.PublishTask,
            "account-actions": module.AccountActionTask,
            "comment-tasks": module.CommentTask,
            "comment-rules": module.CommentRule,
        }[match[1]]
        with module.get_session() as session:
            row = session.get(model, int(match[2]))
            snapshot = row.model_dump(mode="json") if row else None
    if snapshot and snapshot.get("media_json"):
        validate_paths(runtime, {"media_paths": json.loads(snapshot["media_json"])})
    account_id = body.get("account_id") or (snapshot or {}).get("account_id")
    account = None
    if account_id:
        with module.get_session() as session:
            row = session.get(module.DouyinAccount, account_id)
            if row:
                account = {
                    "id": row.id,
                    "platform": row.platform,
                    "identity": row.sec_uid,
                    "name": row.nickname,
                }
    return {
        "platform": {"id": "operations-" + str(account_id or "tasks")},
        "project": {"id": project},
        "actorId": actor["id"],
        "account": account,
        "request": {"method": method, "path": path, "body": body},
        "task": snapshot,
    }


class OperationsMount:
    def __init__(self, get_manager, before_account_change=None):
        self.get_manager = get_manager
        self.before_account_change = before_account_change

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        request = Request(scope, receive)
        actor = scope.get("state", {}).get("user")
        if actor is None:
            return await JSONResponse({"detail": "请先登录"}, status_code=401)(
                scope, receive, send
            )
        project = project_id()
        base = (
            "/api/operations"
            if project == "default"
            else f"/api/projects/{project}/operations"
        )
        path = scope["path"].split("/operations", 1)[-1] or "/"
        manager = self.get_manager()
        if hasattr(manager, "__await__"):
            manager = await manager
        if path == "/status":
            if request.method not in {"GET", "HEAD"}:
                return await JSONResponse(
                    {"detail": "请求方法不支持"}, status_code=405
                )(scope, receive, send)
            return await JSONResponse(
                {
                    "embedded": True,
                    "project": project,
                    "running": project in manager.runtimes
                    and manager.runtimes[project].started,
                    "scope": "interaction",
                    "platforms": ["douyin", "xhs"],
                }
            )(scope, receive, send)
        if not interaction_path(request.method, path):
            return await JSONResponse(
                {"detail": "请使用互动中心的评论与私信功能"},
                status_code=410 if path in {"/", "/workbench"} else 404,
            )(scope, receive, send)
        runtime = await manager.get(project, start=False)
        if path == "/inbox/options" and request.method == "GET":
            from sqlmodel import select

            m = runtime.module
            with m.get_session() as db:
                accounts = [
                    {
                        "id": str(a.id),
                        "name": a.nickname or str(a.id),
                        "platform": "xiaohongshu"
                        if a.platform == "xhs"
                        else a.platform,
                    }
                    for a in db.exec(select(m.DouyinAccount)).all()
                    if a.platform in ("douyin", "xhs")
                ]
                works = [
                    {
                        "account_id": str(w.account_id),
                        "value": w.item_id,
                        "name": w.desc or w.item_id,
                    }
                    for w in db.exec(select(m.AccountWork)).all()
                ]
                conversations = [
                    {
                        "account_id": str(c.account_id),
                        "value": c.conv_id,
                        "name": c.peer_nickname or c.conv_id,
                    }
                    for c in db.exec(select(m.DmConversation)).all()
                ]
            return await JSONResponse(
                {"accounts": accounts, "works": works, "conversations": conversations}
            )(scope, receive, send)
        approval = None
        store = PublicationApprovalStore(
            publication_state_directory(manager.root, project) / "operations"
        )
        try:
            body = {}
            if request.method not in {"GET", "HEAD"}:
                parts = []
                size = 0
                async for part in request.stream():
                    size += len(part)
                    if size > 2 * 1024 * 1024:
                        return await JSONResponse(
                            {"detail": "请求内容过大"}, status_code=413
                        )(scope, receive, send)
                    parts.append(part)
                raw = b"".join(parts)
                body = json.loads(raw) if raw else {}
                if not isinstance(body, dict):
                    raise ValueError("请求内容必须为对象")
                validate_paths(runtime, body)
                if path == "/api/account-actions" and body.get("action") != "send_dm":
                    raise ValueError("当前仅支持回复私信")

                async def replay_body():
                    return {"type": "http.request", "body": raw, "more_body": False}

                receive = replay_body
            if path == '/api/accounts/connect/start' and request.method == 'POST':
                platform = request.query_params.get('platform', '')
                if platform not in {'douyin', 'xhs'}:
                    raise ValueError('该平台暂不支持统一互动连接')
                raw_account = request.query_params.get('account_id')
                account_id = int(raw_account) if raw_account else None
                if account_id is not None and account_id <= 0:
                    raise ValueError('账号编号无效')
                if self.before_account_change is not None:
                    self.before_account_change(platform)
                await runtime.start()
                result = await runtime.connections.start(platform, account_id=account_id, new_account=account_id is None)
                return await JSONResponse(result)(scope, receive, send)
            if path == '/api/accounts/connect/poll' and request.method == 'GET':
                result = runtime.connections.poll(request.query_params.get('task_id', ''))
                return await JSONResponse(result)(scope, receive, send)
            if path == "/approvals" and request.method == "POST":
                method, target, payload = body["method"], body["path"], body["body"]
                if (
                    not isinstance(payload, dict)
                    or not interaction_path(method, target)
                    or not needs_approval(method, target)
                ):
                    raise ValueError("该操作无需此审核流程")
                validate_paths(runtime, payload)
                subject = operation_subject(
                    runtime, actor, project, method, target, payload
                )
                key = request.headers.get("idempotency-key", "")
                record = store.create(
                    subject,
                    actor_id=actor["id"],
                    project_id=project,
                    idempotency_key=key,
                    request_hash=hashlib.sha256(
                        json.dumps(subject, sort_keys=True).encode()
                    ).hexdigest()
                    if key
                    else "",
                )
                return await JSONResponse(record)(scope, receive, send)
            decision = re.fullmatch(r"/approvals/([a-f0-9]{32})", path)
            if decision and request.method == "POST":
                record = store.decide(
                    decision[1],
                    body["approved"],
                    body["payloadHash"],
                    actor_id=actor["id"],
                    project_id=project,
                )
                return await JSONResponse(record)(scope, receive, send)
            if needs_approval(request.method, path):
                identity = request.headers.get("x-easel-operation-approval", "")
                if not identity:
                    return await JSONResponse(
                        {"detail": "请先审核当前平台操作"}, status_code=409
                    )(scope, receive, send)
                subject = operation_subject(
                    runtime, actor, project, request.method, path, body
                )
                approval = store.claim(
                    identity, subject, actor_id=actor["id"], project_id=project
                )
                if not approval["claimed"]:
                    result = approval.get("result") or {}
                    return await JSONResponse(
                        result.get("response")
                        or {"detail": "该操作已提交，请核对原任务"},
                        status_code=result.get("httpStatus", 409),
                    )(scope, receive, send)
        except HTTPException as error:
            return await JSONResponse({'detail': error.detail}, status_code=error.status_code)(scope, receive, send)
        except (KeyError, ValueError, PublicationApprovalError) as error:
            return await JSONResponse(
                {"detail": str(error)}, status_code=getattr(error, "status_code", 422)
            )(scope, receive, send)
        inner = {**scope, "path": path, "raw_path": path.encode(), "root_path": ""}
        if request.method not in {"GET", "HEAD"} and raw:
            inner["headers"] = [
                (key, value)
                for key, value in scope.get("headers", [])
                if key.lower() != b"content-type"
            ] + [(b"content-type", b"application/json")]
        status = 500
        chunks = []
        json_response = False
        response_start = None
        response_parts = []

        def scope_urls(value):
            if isinstance(value, dict):
                return {key: scope_urls(item) for key, item in value.items()}
            if isinstance(value, list):
                return [scope_urls(item) for item in value]
            if isinstance(value, str) and value.startswith(("/api/", "/static/")):
                return base + value
            return value

        async def forward(message):
            nonlocal status, json_response, response_start
            if message["type"] == "http.response.start":
                status = message["status"]
                json_response = any(
                    key.lower() == b"content-type" and b"application/json" in value
                    for key, value in message.get("headers", [])
                )
                if json_response:
                    response_start = message
                    return
            elif message["type"] == "http.response.body":
                if approval:
                    chunks.append(message.get("body", b""))
                if json_response:
                    response_parts.append(message.get("body", b""))
                    if message.get("more_body"):
                        return
                    raw = b"".join(response_parts)
                    try:
                        data = json.loads(raw)
                        if path == '/api/accounts' and isinstance(data, list):
                            from easel.account_sessions import load_session

                            for account in data:
                                if account.get('platform') not in {'douyin', 'xhs'}:
                                    continue
                                linked = load_session(runtime.workspace, account['platform'])
                                if linked and linked['account_id'] == account.get('id'):
                                    account['capabilities'] = linked['capabilities']
                        encoded = json.dumps(
                            scope_urls(data),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode()
                    except ValueError:
                        encoded = raw
                    headers = [
                        (key, value)
                        for key, value in response_start.get("headers", [])
                        if key.lower() != b"content-length"
                    ]
                    await send({**response_start, "headers": headers})
                    await send(
                        {
                            "type": "http.response.body",
                            "body": encoded,
                            "more_body": False,
                        }
                    )
                    return
            await send(message)

        startup_failed = False
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                try:
                    await runtime.start()
                except BaseException as error:
                    startup_failed = True
                    if not isinstance(error, Exception):
                        raise
                    return await JSONResponse(
                        {"detail": "互动引擎暂时无法启动，请稍后重试"}, status_code=503
                    )(scope, receive, forward)
            await runtime.app(inner, receive, forward)
        finally:
            if approval:
                try:
                    result = json.loads(b"".join(chunks)) if chunks else {}
                except ValueError:
                    result = {}
                receipt = {
                    "status": "failed"
                    if startup_failed
                    else "submitted"
                    if status < 400
                    else "unknown"
                    if status >= 500
                    else "failed",
                    "httpStatus": status,
                    "response": result,
                }
                if startup_failed:
                    receipt["notStarted"] = True
                store.finish(
                    approval["id"],
                    approval["operationId"],
                    receipt,
                    actor_id=actor["id"],
                    project_id=project,
                )
