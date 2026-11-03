"""Video workshop HTTP routes; BeefTV runtime operations are injected by the host."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from easel.video_workshop import WorkshopError, WorkshopStore
from easel.projects import project_id
from web.workshop_jobs import workshop_jobs


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _reference(value: str) -> str:
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
        parsed.port
    except ValueError:
        valid = False
    if not valid or any(ord(char) < 32 for char in value):
        raise ValueError("参考链接须为有效的 HTTP/HTTPS 地址")
    return value


class NewProject(RequestModel):
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(default="", max_length=50000)
    reference_url: str = Field(default="", max_length=4096)
    aspect_ratio: Literal["9:16", "16:9", "1:1", "4:5"] = "9:16"

    _check_reference = field_validator("reference_url")(_reference)


class Versioned(RequestModel):
    expected_revision: int = Field(strict=True, ge=1)


class EditProject(Versioned):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    brief: str | None = Field(default=None, max_length=50000)
    reference_url: str | None = Field(default=None, max_length=4096)
    aspect_ratio: Literal["9:16", "16:9", "1:1", "4:5"] | None = None
    beeftv_canvas_id: str | None = Field(default=None, max_length=160)

    @field_validator("title", "brief", "reference_url", "aspect_ratio", "beeftv_canvas_id")
    @classmethod
    def supplied_values_are_not_null(cls, value):
        if value is None:
            raise ValueError("显式修改值不能为 null")
        return value

    @field_validator("reference_url")
    @classmethod
    def check_reference(cls, value):
        return _reference(value)


class Comment(Versioned):
    at_seconds: float = Field(ge=0, allow_inf_nan=False)
    notes: str = Field(min_length=1, max_length=10000)
    kind: Literal["human", "simulated"]


class Review(Versioned):
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    decision: Literal["approved", "changes_requested"]
    kind: Literal["human", "simulated"]
    notes: str = Field(default="", max_length=10000)


class Delivery(Versioned):
    path: str = Field(min_length=1, max_length=4096)
    engine_revision: int | None = Field(default=None, strict=True, ge=0)


class Archive(Versioned):
    archived: bool = Field(strict=True)


class Publication(Versioned):
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    platform: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=4096)
    published_at: datetime | None = None

    _check_url = field_validator("url")(_reference)

    @field_validator("published_at")
    @classmethod
    def publication_time_is_explicit(cls, value):
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("发布时间须包含时区")
        value = value.astimezone(timezone.utc)
        if value > datetime.now(timezone.utc):
            raise ValueError("未来时间不能登记为已发布")
        return value


def create_router(outputs_provider: Callable[[], Path], runtime_status=None, canvas_creator=None, canvas_state=None,
                  *, render_start=None, render_status=None, render_collect=None) -> APIRouter:
    router = APIRouter(prefix="/api/workshop", tags=["video-workshop"])

    def store(http_request: Request) -> WorkshopStore:
        return WorkshopStore(outputs_provider(),
                             strict_canvas_bindings=isinstance(getattr(http_request.state, "user", None), dict))

    def perform(action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except WorkshopError as error:
            raise HTTPException(error.status, str(error)) from error
        except OSError:
            raise HTTPException(503, "视频工坊文件暂时无法读写，请保留项目后重试") from None

    @router.get("/status")
    def status():
        if runtime_status is not None:
            try:
                return runtime_status()
            except Exception:
                pass
        return {"state": "unavailable", "ui_url": "", "version": "", "message": "BeefTV 服务暂不可用"}

    @router.get("/projects")
    def projects(http_request: Request):
        return {"projects": perform(store(http_request).list, render_status=render_status)}

    @router.get("/jobs")
    def jobs(http_request: Request):
        return {"projectId": project_id(), "jobs": perform(workshop_jobs, store(http_request))}

    @router.post("/projects", status_code=201)
    def create(http_request: Request, request: NewProject):
        return perform(store(http_request).create, request.model_dump(), canvas_creator=canvas_creator)

    @router.get("/projects/{project_id}")
    def project(http_request: Request, project_id: str):
        return perform(store(http_request).get, project_id, canvas_state=canvas_state)

    @router.patch("/projects/{project_id}")
    def edit(http_request: Request, project_id: str, request: EditProject):
        values = request.model_dump(exclude_unset=True, exclude={"expected_revision"})
        return perform(store(http_request).update, project_id, request.expected_revision, values)

    @router.post("/projects/{project_id}/comments")
    def comment(http_request: Request, project_id: str, request: Comment):
        return perform(store(http_request).comment, project_id, request.expected_revision,
                       request.model_dump(exclude={"expected_revision"}))

    @router.post("/projects/{project_id}/canvas")
    def canvas(http_request: Request, project_id: str, request: Versioned):
        return perform(store(http_request).attach_canvas, project_id, request.expected_revision, canvas_creator=canvas_creator)

    @router.post("/projects/{project_id}/reviews")
    def review(http_request: Request, project_id: str, request: Review):
        return perform(store(http_request).review, project_id, request.expected_revision, request.artifact_id,
                       request.model_dump(exclude={"expected_revision", "artifact_id"}), canvas_state=canvas_state)

    @router.post("/projects/{project_id}/archive")
    def archive(http_request: Request, project_id: str, request: Archive):
        return perform(store(http_request).archive, project_id, request.expected_revision, request.archived)

    @router.post("/projects/{project_id}/publications")
    def publication(http_request: Request, project_id: str, request: Publication):
        return perform(store(http_request).publication, project_id, request.expected_revision, request.artifact_id,
                       request.model_dump(mode="json", exclude={"expected_revision", "artifact_id"}),
                       canvas_state=canvas_state)

    @router.post("/projects/{project_id}/deliverables")
    def delivery(http_request: Request, project_id: str, request: Delivery):
        return perform(store(http_request).deliver, project_id, request.expected_revision, request.path,
                       engine_revision=request.engine_revision, canvas_state=canvas_state)

    @router.post("/projects/{project_id}/renders")
    def start_render(http_request: Request, project_id: str, request: Versioned):
        return perform(store(http_request).start_render, project_id, request.expected_revision,
                       canvas_state=canvas_state, render_start=render_start)

    @router.get("/projects/{project_id}/renders/{render_id}")
    def get_render(http_request: Request, project_id: str, render_id: str):
        return perform(store(http_request).render_status, project_id, render_id, render_status=render_status)

    @router.post("/projects/{project_id}/renders/{render_id}/collect")
    def collect_render(http_request: Request, project_id: str, render_id: str, request: Versioned):
        return perform(store(http_request).collect_render, project_id, render_id, request.expected_revision,
                       canvas_state=canvas_state, render_status=render_status, render_collect=render_collect)

    return router
