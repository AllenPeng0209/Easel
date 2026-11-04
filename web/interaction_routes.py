"""Local interaction-center routes; external transports are injected by the host."""
from __future__ import annotations

from pathlib import Path
import re
import sqlite3
import asyncio
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from easel.interaction_center import InteractionError, InteractionStore
from easel.projects import project_id
from easel.request_identity import current_user


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


ID = r'^[a-f0-9]{32}$'


def safe_url(value):
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password
        parsed.port
    except ValueError:
        valid = False
    if not valid or any(ord(c) < 32 for c in value):
        raise ValueError('请输入有效的 HTTP/HTTPS 地址')
    return value


def safe_config(value):
    if set(value) - {'env_prefix', 'account_id', 'video_id', 'profile_name', 'conversation_id'}:
        raise ValueError('通道设置只接受环境变量引用和账号元数据，不接受密钥或文件路径')
    if any(len(v) > 256 or any(ord(c) < 32 for c in v) for v in value.values()):
        raise ValueError('通道设置格式无效')
    if value.get('env_prefix') and not re.fullmatch(r'EASEL_INTERACTION_[A-Z0-9_]{1,80}', value['env_prefix']):
        raise ValueError('环境变量前缀须使用 EASEL_INTERACTION_ 开头')
    if value.get('profile_name') and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value['profile_name']):
        raise ValueError('浏览器配置名称格式无效')
    return value


class Scoped(Input):
    client_id: str = Field(pattern=ID)


class Versioned(Scoped):
    expected_revision: int = Field(strict=True, ge=1)


class NewClient(Input):
    name: str = Field(min_length=1, max_length=120)


class NewChannel(Scoped):
    name: str = Field(min_length=1, max_length=120)
    platform: Literal['xiaohongshu', 'douyin']
    kind: Literal['comment', 'dm']
    delivery_mode: Literal['simulation', 'manual', 'official', 'creatorhub', 'native'] = 'manual'
    account_label: str = Field(default='', max_length=120)
    source_url: str = Field(default='', max_length=4096)
    enabled: bool = Field(default=True, strict=True)
    config: dict[str, str] = Field(default_factory=dict, max_length=4)
    _url = field_validator('source_url')(safe_url)
    _config = field_validator('config')(safe_config)

    @model_validator(mode='after')
    def creatorhub_comment_binding(self):
        if self.delivery_mode == 'native':
            target = 'video_id' if self.kind == 'comment' else 'conversation_id'
            if (self.source_url or set(self.config) != {'account_id',target}
                    or not re.fullmatch(r'[1-9][0-9]{0,17}',self.config.get('account_id',''))
                    or not self.config.get(target,'')):
                raise ValueError('请选择内置账号及其作品或会话')
        if self.delivery_mode == 'creatorhub' and (
                (self.platform, self.kind) != ('douyin', 'comment') or self.source_url
                or set(self.config) != {'env_prefix'} or not self.config['env_prefix']):
            raise ValueError('CreatorHub 首期仅支持绑定服务端配置的抖音自有作品评论')
        return self


class ChannelPatch(Scoped):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    account_label: str | None = Field(default=None, max_length=120)
    source_url: str | None = Field(default=None, max_length=4096)
    enabled: bool | None = Field(default=None, strict=True)
    config: dict[str, str] | None = Field(default=None, max_length=4)

    @field_validator('name', 'account_label', 'source_url', 'enabled', 'config')
    @classmethod
    def non_null(cls, value):
        if value is None:
            raise ValueError('修改字段不能为 null')
        return value

    _url = field_validator('source_url')(safe_url)
    _config = field_validator('config')(safe_config)


class DraftEdit(Versioned):
    text: str = Field(min_length=1, max_length=10000)
    source: Literal['human', 'agent'] = 'human'


class Approval(Versioned):
    review_kind: Literal['human', 'agent', 'simulated'] = 'human'


class Send(Versioned):
    operation_id: str = Field(pattern=r'^[A-Za-z0-9:_-]{1,160}$')


class Action(Versioned):
    action: Literal['takeover', 'resolve', 'reopen']


class Knowledge(Scoped):
    question: str = Field(min_length=1, max_length=300)
    keywords: list[str] = Field(min_length=1, max_length=30)
    answer: str = Field(min_length=1, max_length=10000)
    auto_send: bool = Field(default=False, strict=True)
    enabled: bool = Field(default=True, strict=True)

    @field_validator('keywords')
    @classmethod
    def keywords_valid(cls, values):
        if any(not v.strip() or len(v) > 100 for v in values):
            raise ValueError('关键词不能为空或过长')
        return list(dict.fromkeys(v.strip() for v in values))


class KnowledgePatch(Scoped):
    question: str | None = Field(default=None, min_length=1, max_length=300)
    keywords: list[str] | None = Field(default=None, min_length=1, max_length=30)
    answer: str | None = Field(default=None, min_length=1, max_length=10000)
    auto_send: bool | None = Field(default=None, strict=True)
    enabled: bool | None = Field(default=None, strict=True)

    @field_validator('question', 'keywords', 'answer', 'auto_send', 'enabled')
    @classmethod
    def non_null(cls, value):
        if value is None:
            raise ValueError('修改字段不能为 null')
        return value

    _keywords = field_validator('keywords')(Knowledge.keywords_valid)


class Rules(Scoped):
    mode: Literal['manual', 'assist', 'auto_faq']
    enabled: bool = Field(strict=True)
    blocked_keywords: list[str] | None = Field(default=None, max_length=100)
    max_auto_per_hour: int | None = Field(default=None, strict=True, ge=1, le=1000)
    max_auto_per_run: int | None = Field(default=None, strict=True, ge=1, le=100)

    @field_validator('blocked_keywords')
    @classmethod
    def blocked_valid(cls, values):
        if values is not None and any(not v.strip() or len(v) > 100 for v in values):
            raise ValueError('转人工关键词格式无效')
        return values


class Sync(Scoped):
    source_url: str = Field(default='', max_length=4096)
    _url = field_validator('source_url')(safe_url)


def create_router(outputs_provider, *, send_adapter=None, sync_adapter=None, capabilities=None, native_sync=None):
    router = APIRouter(prefix='/api/interactions', tags=['interaction-center'])

    def store(*, initialize_default_client=True):
        return InteractionStore(Path(outputs_provider()), initialize_default_client=initialize_default_client)

    def channel_values(values: dict, previous: dict | None = None) -> dict:
        actor = current_user()
        if actor is None or 'config' not in values:
            return values
        prefix = values['config'].get('env_prefix', '')
        scope = project_id()
        if actor.get('is_admin'):
            # Explicit administrator delegation survives requests and workers,
            # while remaining tied to this IP and this exact credential family.
            return {**values, 'credential_binding': {'project_id': scope, 'prefix': prefix} if prefix else None}
        namespace = 'EASEL_INTERACTION_' + scope.upper() + '_'
        if not prefix or prefix.startswith(namespace) and len(prefix) > len(namespace):
            return {**values, 'credential_binding': None}
        binding = previous.get('credential_binding') if previous else None
        if (isinstance(binding, dict) and binding.get('project_id') == scope and binding.get('prefix') == prefix
                and previous.get('config', {}).get('env_prefix') == prefix):
            # An owner may maintain other account metadata after an administrator
            # delegates a legacy family, but cannot switch that family.
            return values
        raise HTTPException(403, '该凭据引用不属于当前 IP，请使用当前 IP 专属前缀或请平台管理员配置通道')

    def perform(action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except InteractionError as error:
            raise HTTPException(error.status, str(error)) from error
        except (OSError, sqlite3.Error):
            raise HTTPException(503, '互动中心数据暂时不可读写，请保留记录后重试') from None

    @router.get('/bootstrap')
    def bootstrap(http_request: Request, client_id: str | None = Query(default=None, pattern=ID)):
        readonly = getattr(http_request.state, 'project_role', None) == 'viewer'
        return perform(store(initialize_default_client=not readonly).bootstrap, client_id, capabilities)

    @router.post('/clients')
    def create_client(request: NewClient):
        return perform(store().create_client, request.name)

    @router.post('/channels')
    def create_channel(request: NewChannel):
        return perform(store().create_channel, request.client_id,
                       channel_values(request.model_dump(exclude={'client_id'})), capabilities)

    @router.patch('/channels/{channel_id}')
    def update_channel(channel_id: str, request: ChannelPatch):
        instance = store()
        previous = next((channel for channel in perform(instance.bootstrap, request.client_id)['channels']
                         if channel['id'] == channel_id), None)
        if previous is None:
            raise HTTPException(404, '通道不存在或不属于当前客户')
        return perform(instance.update_channel, request.client_id, channel_id,
                       channel_values(request.model_dump(exclude={'client_id'}, exclude_unset=True), previous), capabilities)

    @router.post('/channels/{channel_id}/sync')
    async def sync_channel(channel_id: str, request: Sync):
        instance=store()
        channels=perform(instance.bootstrap, request.client_id)['channels']
        channel=next((item for item in channels if item['id']==channel_id),None)
        if channel is not None and channel['delivery_mode']=='native':
            if request.source_url or native_sync is None:
                raise HTTPException(400,'内置通道使用已绑定的账号、作品或会话')
            try:
                return await native_sync(instance,request.client_id,channel_id)
            except InteractionError as error:
                raise HTTPException(error.status,str(error)) from error
        return await asyncio.to_thread(perform, instance.sync_channel, request.client_id, channel_id, request.source_url, sync_adapter, capabilities)

    @router.post('/simulation')
    def simulation(request: Scoped):
        return perform(store().simulation, request.client_id)

    @router.get('/conversations/{conversation_id}')
    def conversation(conversation_id: str, client_id: str = Query(pattern=ID)):
        return perform(store().conversation, client_id, conversation_id)

    @router.post('/conversations/{conversation_id}/draft')
    def create_draft(conversation_id: str, request: Versioned):
        return perform(store().create_draft, request.client_id, conversation_id, request.expected_revision)

    @router.post('/conversations/{conversation_id}/action')
    def action(conversation_id: str, request: Action):
        return perform(store().action, request.client_id, conversation_id, request.expected_revision, request.action)

    @router.patch('/drafts/{draft_id}')
    def edit_draft(draft_id: str, request: DraftEdit):
        return perform(store().edit_draft, request.client_id, draft_id, request.expected_revision, request.text, request.source)

    @router.post('/drafts/{draft_id}/approve')
    def approve(draft_id: str, request: Approval):
        return perform(store().approve_draft, request.client_id, draft_id, request.expected_revision, request.review_kind)

    @router.post('/drafts/{draft_id}/send')
    def send(draft_id: str, request: Send):
        return perform(store().send_draft, request.client_id, draft_id, request.expected_revision, request.operation_id, send_adapter, capabilities)

    @router.post('/knowledge')
    def add_knowledge(request: Knowledge):
        return perform(store().add_knowledge, request.client_id, request.model_dump(exclude={'client_id'}))

    @router.patch('/knowledge/{knowledge_id}')
    def edit_knowledge(knowledge_id: str, request: KnowledgePatch):
        return perform(store().add_knowledge, request.client_id, request.model_dump(exclude={'client_id'}, exclude_unset=True), knowledge_id)

    @router.put('/rules')
    def rules(request: Rules):
        return perform(store().update_rules, request.client_id, request.model_dump(exclude={'client_id'}, exclude_none=True))

    @router.post('/automation/run')
    def run(request: Scoped):
        return perform(store().tick, request.client_id, send_adapter, capabilities)

    return router
