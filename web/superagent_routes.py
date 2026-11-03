"""Authenticated task intake and operator actions. The Host defines workflow steps."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from easel.access import AccessStore
from easel.projects import _registry, project_context
from easel.superagent_runtime import DRAFT_STEPS
from easel.superagent_tasks import SuperAgentTaskError, SuperAgentTaskStore



class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: str = Field(pattern=r"^(default|[a-f0-9]{16})$")


class DraftTask(Scope):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(min_length=1, max_length=16000)
    sessionId: str | None = Field(default=None, min_length=1, max_length=256)
    requestId: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,160}$")


class Answer(Scope):
    questionId: str = Field(pattern=r"^[a-f0-9]{32}$")
    answer: str = Field(min_length=1, max_length=2000)


class Publish(Scope):
    approvalId: str = Field(pattern=r"^[a-f0-9]{32}$")


def task_store(root):
    return SuperAgentTaskStore(root / "outputs" / "_superagent")


def projects_for(root, actor_id):
    access = AccessStore(root)
    return [{"id": row["id"], "name": row["name"], "canOperate": role in ("owner", "operator")}
            for row in _registry(root) if (role := access.role(actor_id, row["id"])) is not None]


def create_router(get_root, publication_workflow):
    router = APIRouter(prefix="/api/superagent/tasks", tags=["Super Agent"])

    def actor(request):
        user = getattr(request.state, "user", None)
        if not user:
            raise HTTPException(401, "请先登录")
        return user["id"]

    def scope(request, project_id):
        actor_id = actor(request)
        root = get_root()
        if not any(row["id"] == project_id for row in _registry(root)):
            raise HTTPException(404, "IP 不存在")
        AccessStore(root).require_role(actor_id, project_id, {"owner", "operator"})
        return {"actor_id": actor_id, "project_id": project_id}

    async def operate(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except SuperAgentTaskError as error:
            raise HTTPException(error.status_code, str(error)) from None

    @router.get("")
    async def list_tasks(request: Request):
        actor_id = actor(request)
        root = get_root()
        def read():
            projects = projects_for(root, actor_id)
            store = task_store(root)
            tasks = [task for row in projects if row["canOperate"]
                     for task in store.list(actor_id=actor_id, project_id=row["id"])]
            return {"projects": projects, "tasks": sorted(tasks, key=lambda item: item["updatedAt"], reverse=True)}
        return await asyncio.to_thread(read)

    @router.post("")
    async def create_task(request: Request, body: DraftTask):
        bindings = scope(request, body.projectId)
        steps = DRAFT_STEPS
        if body.sessionId is not None:
            from web.chat_session_references import valid_session_reference_id
            if not valid_session_reference_id(body.sessionId):
                raise HTTPException(422, "无效的来源会话")
            steps = [{**DRAFT_STEPS[0], "payload": {"sessionId": body.sessionId}}, *DRAFT_STEPS[1:]]
        return await operate(task_store(get_root()).create, title=body.title, brief=body.brief,
                             steps=steps, request_id=body.requestId, **bindings)

    @router.post("/publish")
    async def create_publish(request: Request, body: Publish):
        bindings = scope(request, body.projectId)
        with project_context(body.projectId, get_root()):
            approval = publication_workflow().get(body.approvalId)
        if approval["state"] not in {"approved", "executing", "consumed"}:
            raise HTTPException(409, "发布需要已确认的精确批准")
        return await operate(task_store(get_root()).create, title="执行已批准的发布",
                             steps=[{"kind": "publish", "payload": {"approvalId": body.approvalId}}],
                             request_id=f"publish:{body.approvalId}", **bindings)

    @router.post("/{task_id}/answer")
    async def answer_task(request: Request, task_id: str, body: Answer):
        return await operate(task_store(get_root()).answer, task_id, body.questionId, body.answer,
                             **scope(request, body.projectId))

    @router.post("/{task_id}/stop")
    async def stop_task(request: Request, task_id: str, body: Scope):
        return await operate(task_store(get_root()).stop, task_id, **scope(request, body.projectId))

    @router.post("/{task_id}/retry")
    async def retry_task(request: Request, task_id: str, body: Scope):
        return await operate(task_store(get_root()).retry, task_id, **scope(request, body.projectId))

    return router
