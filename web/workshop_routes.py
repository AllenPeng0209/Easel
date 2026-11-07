"""Native video workshop routes for AI production, review and publication."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError

from easel.video_workshop import ASSET_SUFFIXES, WorkshopCancelAuthorizationError, WorkshopError, WorkshopStore
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

    @field_validator("title", "brief", "reference_url", "aspect_ratio")
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
    shot_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")


class Review(Versioned):
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    decision: Literal["approved", "changes_requested"]
    kind: Literal["human", "simulated"]
    notes: str = Field(default="", max_length=10000)
    publication_approval_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    publication_payload_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class Delivery(Versioned):
    path: str = Field(min_length=1, max_length=4096)


class Archive(Versioned):
    archived: bool = Field(strict=True)


class CancelRender(RequestModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    taskId: str = Field(pattern=r"^[A-Za-z0-9:_-]{1,160}$")


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


class Asset(RequestModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    kind: Literal['character', 'scene', 'prop', 'image', 'video', 'audio', 'subtitle', 'document']
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=4000)
    path: str = Field(default='', max_length=1024)
    image_prompt: str | None = Field(default=None, max_length=12000)
    appearance: str | None = Field(default=None, max_length=4000)
    styling: str | None = Field(default=None, max_length=4000)
    lighting: str | None = Field(default=None, max_length=4000)


class Shot(RequestModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    title: str = Field(min_length=1, max_length=120)
    prompt: str = Field(default='', max_length=12000)
    description: str | None = Field(default=None, max_length=12000)
    atmosphere: str | None = Field(default=None, max_length=4000)
    seconds: int = Field(strict=True, ge=1, le=60)
    asset_ids: list[str] = Field(default_factory=list, max_length=24)
    primary_frame_asset_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")


class Production(Versioned):
    raw_content: str | None = Field(default=None, max_length=100000)
    script: str = Field(default='', max_length=100000)
    assets: list[Asset] = Field(default_factory=list, max_length=200)
    shots: list[Shot] = Field(default_factory=list, max_length=200)

    def production_values(self):
        # Keep existing optional-field semantics; only an explicitly supplied
        # null primary is a clear operation, not an omitted/default field.
        values = self.model_dump(exclude={'expected_revision'}, exclude_none=True)
        for source, target in zip(self.shots, values['shots']):
            if 'primary_frame_asset_id' in source.model_fields_set:
                target['primary_frame_asset_id'] = source.primary_frame_asset_id
        return values


class Candidate(Versioned):
    path: str = Field(min_length=1, max_length=1024)


class SelectCandidate(Versioned):
    candidate_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class PublishPreparation(Versioned):
    artifact_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    platform: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    title: str = Field(min_length=1, max_length=1000)
    body: str = Field(default='', max_length=100000)
    tags: str = Field(default='', max_length=2000)
    persona: str = Field(default='', max_length=120)
    aiGenerated: bool = True


def create_router(outputs_provider: Callable[[], Path], *, publication_workflow=None, workshop_sync=None) -> APIRouter:
    router = APIRouter(prefix='/api/workshop', tags=['video-workshop'])

    def store():
        return WorkshopStore(outputs_provider())

    def perform(action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except WorkshopError as error:
            raise HTTPException(error.status, str(error)) from error
        except OSError:
            raise HTTPException(503, '视频工坊文件暂时无法读写，请保留项目后重试') from None

    def conditional(data, request):
        body = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        headers = {'ETag': etag, 'Cache-Control': 'no-store', 'Vary': 'Cookie, X-Easel-User'}
        validators = [value.strip().removeprefix('W/') for value in request.headers.get('if-none-match', '').split(',')]
        if etag in validators or '*' in validators:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type='application/json', headers=headers)

    @router.get('/status')
    def status():
        return {'state': 'running', 'version': 'native', 'message': 'AI 对话制作，工坊审阅后自动发布'}

    @router.get('/projects')
    def projects(http_request: Request, summary: bool = False):
        rows = perform(store().list)
        rows = [perform(workshop_sync, row['id']) for row in rows] if workshop_sync is not None else rows
        if summary:
            # Lifecycle receipts stay exact; scripts, asset versions and prompts
            # belong to the selected project's detail, never a polling list.
            rows = [{**row, 'script': '', 'raw_content': '', 'assets': [], 'shots': [],
                     'publication_preparation': None, 'summary': True} for row in rows]
        return conditional({'projects': rows}, http_request)

    @router.get('/jobs')
    def jobs():
        return {'projectId': project_id(), 'jobs': perform(workshop_jobs, store())}

    @router.post('/projects', status_code=201)
    def create(request: NewProject):
        return perform(store().create, request.model_dump())

    @router.get('/projects/{identifier}')
    def project(identifier: str, http_request: Request):
        current_store = store()
        if workshop_sync is not None:
            perform(workshop_sync, identifier)
        if publication_workflow is not None and getattr(http_request.state, 'project_role', None) in {'owner', 'operator'}:
            from web.workshop_publication import WorkshopPublication
            result = perform(WorkshopPublication(current_store, publication_workflow()).observe, identifier)
        else:
            result = perform(current_store.get, identifier)
        return conditional(result, http_request)

    @router.patch('/projects/{identifier}')
    def edit(identifier: str, request: EditProject):
        return perform(store().update, identifier, request.expected_revision,
                       request.model_dump(exclude_unset=True, exclude={'expected_revision'}))

    @router.put('/projects/{identifier}/production')
    def production(identifier: str, request: Production):
        return perform(store().production, identifier, request.expected_revision,
                       request.production_values())

    @router.post('/projects/{identifier}/assets/upload')
    async def upload_asset(identifier: str, file: UploadFile = File(...), expected_revision: int = Form(...),
                           asset_id: str = Form(...), kind: str = Form(...), name: str = Form(...)):
        import uuid
        current_store = store()
        current = perform(current_store.get, identifier)
        perform(current_store._expect, current, expected_revision)
        suffix = Path(file.filename or '').suffix.lower()
        if suffix not in ASSET_SUFFIXES:
            raise HTTPException(422, '请选择图片、音视频、字幕或制作文档')
        directory = current_store.outputs / ('素材导入-' + identifier[:8])
        if directory.is_symlink():
            raise HTTPException(403, '素材导入目录不能是符号链接')
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (uuid.uuid4().hex + suffix)
        try:
            count = 0
            with path.open('xb') as stream:
                while chunk := await file.read(1024 * 1024):
                    count += len(chunk)
                    if count > 50 * 1024 * 1024:
                        raise HTTPException(413, '单个素材不能超过 50 MB')
                    stream.write(chunk)
            asset = Asset.model_validate({'id': asset_id, 'kind': kind, 'name': name,
                'path': path.relative_to(current_store.outputs).as_posix()}).model_dump(exclude_none=True)
            assets = [{key: row[key] for key in ('id', 'kind', 'name', 'description', 'path') if key in row}
                      for row in current['assets'] if row['id'] != asset_id]
            assets.append(asset)
            return perform(current_store.production, identifier, expected_revision, {
                'script': current['script'], 'assets': assets,
                'shots': [{key: row[key] for key in ('id', 'title', 'prompt', 'seconds', 'asset_ids', 'primary_frame_asset_id') if key in row} for row in current['shots']]})
        except ValidationError:
            raise HTTPException(422, '素材名称或类型无效') from None
        finally:
            path.unlink(missing_ok=True)
            await file.close()

    @router.post('/projects/{identifier}/shots/{shot_id}/candidates')
    def candidate(identifier: str, shot_id: str, request: Candidate):
        return perform(store().add_candidate, identifier, request.expected_revision, shot_id, request.path)

    @router.post('/projects/{identifier}/shots/{shot_id}/select')
    def select(identifier: str, shot_id: str, request: SelectCandidate):
        return perform(store().select_candidate, identifier, request.expected_revision, shot_id, request.candidate_id)

    @router.post('/projects/{identifier}/comments')
    def comment(identifier: str, request: Comment):
        return perform(store().comment, identifier, request.expected_revision,
                       request.model_dump(exclude={'expected_revision'}, exclude_none=True))

    @router.post('/projects/{identifier}/publish/prepare')
    async def prepare_publication(identifier: str, request: PublishPreparation):
        if publication_workflow is None:
            raise HTTPException(503, '发布服务尚未配置')
        from web.workshop_publication import WorkshopPublication
        try:
            return await WorkshopPublication(store(), publication_workflow()).prepare(identifier, request.model_dump())
        except WorkshopError as error:
            raise HTTPException(error.status, str(error)) from None

    @router.post('/projects/{identifier}/reviews')
    async def review(identifier: str, request: Review):
        if publication_workflow is not None:
            from web.workshop_publication import WorkshopPublication
            try:
                return await WorkshopPublication(store(), publication_workflow()).review(identifier, request.model_dump())
            except WorkshopError as error:
                raise HTTPException(error.status, str(error)) from None
        return perform(store().review, identifier, request.expected_revision, request.artifact_id,
                       request.model_dump(include={'kind', 'decision', 'notes'}))

    @router.post('/projects/{identifier}/archive')
    def archive(identifier: str, request: Archive):
        return perform(store().archive, identifier, request.expected_revision, request.archived)

    @router.post('/projects/{identifier}/publications')
    def publication(identifier: str, request: Publication):
        return perform(store().publication, identifier, request.expected_revision, request.artifact_id,
                       request.model_dump(mode='json', exclude={'expected_revision', 'artifact_id'}))

    @router.post('/projects/{identifier}/deliverables')
    def delivery(identifier: str, request: Delivery):
        return perform(store().deliver, identifier, request.expected_revision, request.path)

    @router.post('/projects/{identifier}/renders')
    def start_render(identifier: str, request: Versioned):
        return perform(store().start_render, identifier, request.expected_revision)

    @router.get('/projects/{identifier}/renders/{render_id}')
    def get_render(identifier: str, render_id: str):
        return perform(store().render_status, identifier, render_id)

    @router.post('/projects/{identifier}/renders/{render_id}/collect')
    def collect_render(identifier: str, render_id: str, request: Versioned):
        return perform(store().collect_render, identifier, render_id, request.expected_revision)

    return router
