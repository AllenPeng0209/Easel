"""Bounded platform adapters for the local interaction center.

Simulation belongs to the store and never reaches these adapters. Real channel
credentials stay in operator-provided environment variables; browser import uses
a client-scoped profile. With team access enabled, each IP uses credential
families named EASEL_INTERACTION_<UPPERCASE_IP_ID>_<CHANNEL> (DEFAULT for the
legacy space). An administrator can explicitly delegate a legacy family to a
channel through its server-written credential_binding. This module never
retries a message submission.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from typing import Mapping
from urllib.parse import urlsplit

import httpx


ROOT = Path(__file__).resolve().parents[1]
DOUYIN_API = 'https://open.douyin.com'
DOUYIN_GUIDE = 'https://open.douyin.com/platform/resource/docs/ability/interaction-management/video-comment-management-solution'
XHS_GUIDE = 'https://ad-market.xiaohongshu.com/'
_ENV_PREFIX = re.compile(r'EASEL_INTERACTION_[A-Z0-9_]{1,100}')
_LOCAL_ID = re.compile(r'[A-Za-z0-9_-]{1,100}')
_REMOTE_ID = re.compile(r'[^\s\x00-\x1f]{1,1024}')
_OPERATION_ID = re.compile(r'[A-Za-z0-9:_-]{1,160}')
_NOTE_PATH = re.compile(r'/(?:explore|discovery/item)/([A-Fa-f0-9]{24})/?')


class ChannelError(RuntimeError):
    """Only safe diagnostics may be returned by channel adapters."""


class InteractionChannels:
    def __init__(self, *, environ: Mapping[str, str] | None = None, transport=None,
                 runner=None, workspace: Path | None = None):
        self.environ = os.environ if environ is None else environ
        self.transport = transport
        self.runner = runner or subprocess.run
        self.workspace = Path(workspace or ROOT).resolve()

    @staticmethod
    def _config(channel: dict) -> dict:
        value = channel.get('config', {})
        return value if isinstance(value, dict) else {}

    def _prefix(self, channel: dict) -> str:
        value = self._config(channel).get('env_prefix', '')
        if not isinstance(value, str) or not _ENV_PREFIX.fullmatch(value):
            return ''
        if not self._managed_credentials():
            return value
        from easel.projects import project_id
        scope = project_id()
        binding = channel.get('credential_binding')
        if binding is not None:
            # This metadata is written only by a server-side administrator
            # operation; channel input schemas cannot supply or replace it.
            if (not isinstance(binding, dict) or binding.get('project_id') != scope
                    or binding.get('prefix') != value):
                return ''
            return value
        namespace = 'EASEL_INTERACTION_' + scope.upper() + '_'
        return value if value.startswith(namespace) and len(value) > len(namespace) else ''

    def _managed_credentials(self) -> bool:
        from easel.request_identity import current_user
        if current_user() is not None:
            return True
        # Periodic automation has no browser actor. Once team access is enabled,
        # an absent actor must not restore access to every host-level token.
        from easel.projects import project_id, workspace
        directory = workspace(self.workspace)
        repository = directory if project_id() == 'default' else directory.parent.parent
        path = repository / '.easel' / 'access.sqlite3'
        if not path.exists():
            return False
        database = None
        try:
            database = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            return database.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None
        except sqlite3.Error:
            return True
        finally:
            if database is not None:
                database.close()

    def _setting(self, channel: dict, suffix: str) -> str:
        prefix = self._prefix(channel)
        value = self.environ.get(prefix + '_' + suffix, '') if prefix else ''
        return value.strip() if isinstance(value, str) else ''

    def _creatorhub_binding(self, channel: dict) -> tuple[int, int, int, str] | None:
        from easel.projects import project_id

        if (channel.get('platform'), channel.get('kind')) != ('douyin', 'comment'):
            return None
        prefix = self._config(channel).get('env_prefix', '')
        namespace = 'EASEL_INTERACTION_' + project_id().upper() + '_'
        if not isinstance(prefix, str) or not _ENV_PREFIX.fullmatch(prefix) or not prefix.startswith(namespace):
            return None
        binding = prefix + '_CREATORHUB_'
        customer = self.environ.get(binding + 'CLIENT_ID', '')
        if not customer or customer != channel.get('client_id'):
            return None
        account = self.environ.get(binding + 'ACCOUNT_ID', '')
        work = self.environ.get(binding + 'WORK_ID', '')
        item = self.environ.get(binding + 'ITEM_ID', '')
        port = self.environ.get('EASEL_CREATORHUB_PORT', '8000')
        if not all(re.fullmatch(r'[1-9][0-9]{0,17}', value) for value in (account, work)):
            return None
        if not re.fullmatch(r'[0-9]{1,32}', item) or not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
            return None
        return int(port), int(account), int(work), item

    def _douyin_credentials(self, channel: dict) -> tuple[str, str, str, bool]:
        config = self._config(channel)
        token = self._setting(channel, 'ACCESS_TOKEN')
        account = self._setting(channel, 'OPEN_ID') or config.get('account_id', '')
        video = self._setting(channel, 'VIDEO_ID') or config.get('video_id', '')
        permitted = self._setting(channel, 'PERMISSION_VERIFIED').lower() in {'true', '1', 'yes'}
        valid_token = bool(token) and len(token) <= 4096 and not any(c.isspace() or ord(c) < 32 for c in token)
        valid_ids = isinstance(account, str) and isinstance(video, str) and _REMOTE_ID.fullmatch(account) and _REMOTE_ID.fullmatch(video)
        return token, account, video, bool(valid_token and valid_ids and permitted)

    def _profile_base(self, channel: dict) -> Path:
        from easel.projects import project_id
        client_id = channel.get('client_id', '')
        if not isinstance(client_id, str) or not _LOCAL_ID.fullmatch(client_id):
            raise ChannelError('客户工作区编号无效，无法选择独立浏览器目录')
        channel_id = channel.get('id', '')
        if not isinstance(channel_id, str) or not _LOCAL_ID.fullmatch(channel_id):
            raise ChannelError('通道编号无效，无法选择独立浏览器目录')
        scope = project_id()
        if not isinstance(scope, str) or not _LOCAL_ID.fullmatch(scope):
            raise ChannelError('当前项目编号无效，无法选择独立浏览器目录')
        configured = self._setting(channel, 'PROFILE_BASE')
        # A filesystem path is accepted only from the server environment, never
        # a channel value sent by a browser. Default profiles are client-scoped.
        base = Path(configured).expanduser().resolve() if configured else self.workspace / '.scratch' / 'interaction_profiles'
        return base / scope / client_id / channel_id

    def capabilities(self, channel: dict | None = None):
        if channel is None:
            return [self.capabilities({'platform': platform, 'kind': kind, 'delivery_mode': 'official'})
                    for platform in ('xiaohongshu', 'douyin') for kind in ('comment', 'dm')]
        platform = channel.get('platform', '')
        kind = channel.get('kind', '')
        base = {'platform': platform, 'kind': kind, 'can_sync': False, 'can_send': False,
                'state': 'awaiting_authorization', 'guide_url': DOUYIN_GUIDE if platform == 'douyin' else XHS_GUIDE}
        if channel.get('delivery_mode') == 'simulation':
            return {**base, 'state': 'simulation', 'message': '模拟通道仅在本机记录测试消息和回执'}
        if channel.get('enabled') is False:
            return {**base, 'state': 'disabled', 'message': '此通道已停用'}
        if channel.get('delivery_mode') == 'native':
            from easel.operations_inbox import native_capability
            return {**base, **native_capability(channel)}
        if channel.get('delivery_mode') == 'creatorhub':
            configured = self._creatorhub_binding(channel) is not None
            return {**base, 'state': 'configured' if configured else 'awaiting_authorization',
                    'can_sync': configured,
                    'message': '已绑定本机 CreatorHub。仅导入已采集的评论，回复请到抖音处理。' if configured
                    else '请在 响芽 服务端配置当前 IP、客户、账号与自有作品绑定。'}
        if kind == 'dm':
            return {**base, 'message': '私信需要平台单独准入与授权，当前尚未接通'}
        if platform == 'douyin' and kind == 'comment' and channel.get('delivery_mode') == 'official':
            *_, configured = self._douyin_credentials(channel)
            return {**base, 'state': 'configured' if configured else 'awaiting_authorization',
                    'can_sync': configured, 'can_send': configured,
                    'message': '凭据和权限配置已读取，仍需真实账号验收' if configured
                    else '需配置已获准入的企业号凭据、视频编号与权限确认；官方评论能力目前暂停新增申请'}
        if platform == 'xiaohongshu' and kind == 'comment':
            if channel.get('delivery_mode') != 'manual':
                return {**base, 'message': '小红书官方评论通道尚未接入；浏览器导入需明确选择人工通道'}
            try:
                profile_exists = (self._profile_base(channel) / 'XiaohongshuProfile').is_dir()
            except ChannelError:
                profile_exists = False
            return {**base, 'state': 'manual_only', 'can_sync': profile_exists,
                    'message': '可导入指定笔记评论；回复需人工处理，尚无按评论编号核实的发送回执'
                    if profile_exists else '需先在该客户独立浏览器目录登录；当前仅支持评论导入，发送仍需人工处理'}
        return {**base, 'message': '此平台通道尚未接入'}

    def _http(self):
        return httpx.Client(transport=self.transport, trust_env=False, follow_redirects=False,
                            timeout=httpx.Timeout(20, connect=5))

    @staticmethod
    def _business_data(response: httpx.Response) -> dict:
        if response.status_code != 200:
            raise ChannelError('平台请求未完成，请核对授权与服务状态')
        try:
            value = response.json()
            data = value.get('data')
            extra = value.get('extra', {})
            if not isinstance(data, dict) or data.get('error_code') != 0:
                raise ValueError
            if not isinstance(extra, dict) or extra.get('error_code', 0) != 0:
                raise ValueError
            return data
        except (ValueError, AttributeError):
            raise ChannelError('平台未接受请求，请核对账号权限和参数') from None

    def sync(self, channel: dict, source_url: str = '') -> list[dict]:
        if channel.get('delivery_mode') == 'simulation':
            raise ChannelError('模拟消息由互动中心本机生成，不连接真实平台')
        if channel.get('delivery_mode') == 'creatorhub':
            return self._sync_creatorhub(channel)
        if channel.get('platform') == 'xiaohongshu' and channel.get('kind') == 'comment':
            return self._sync_xhs(channel, source_url or channel.get('source_url', ''))
        capability = self.capabilities(channel)
        if not capability['can_sync']:
            raise ChannelError(capability['message'])
        token, account, video, _ = self._douyin_credentials(channel)
        items = []; seen = set(); cursors = set(); cursor = 0
        try:
            with self._http() as client:
                for _ in range(3):
                    if str(cursor) in cursors:
                        break
                    cursors.add(str(cursor))
                    response = client.get(DOUYIN_API + '/video/comment/list/',
                        params={'open_id': account, 'item_id': video, 'cursor': cursor, 'count': 50},
                        headers={'access-token': token, 'Content-Type': 'application/json'})
                    data = self._business_data(response)
                    rows = data.get('list', [])
                    if not isinstance(rows, list):
                        raise ChannelError('平台评论列表格式无法确认')
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        identity = row.get('comment_id'); text = row.get('content')
                        if not isinstance(identity, str) or not _REMOTE_ID.fullmatch(identity) or not isinstance(text, str) or not text.strip() or identity in seen:
                            continue
                        seen.add(identity)
                        items.append({'external_id': identity, 'text': text[:10000],
                            'author_name': str(row.get('nickname') or row.get('comment_user_id') or '抖音用户')[:120],
                            'conversation_external_id': identity, 'source_url': '', 'subject': '抖音视频评论'})
                    if not data.get('has_more'):
                        break
                    cursor = data.get('cursor', cursor)
                    if not isinstance(cursor, (str, int)):
                        raise ChannelError('平台评论游标格式无法确认')
        except httpx.HTTPError:
            raise ChannelError('评论同步未完成，请稍后重新读取；没有发送任何回复') from None
        return items

    @staticmethod
    def _note_url(value: str) -> tuple[str, str]:
        try:
            parsed = urlsplit(value)
            match = _NOTE_PATH.fullmatch(parsed.path)
            if (parsed.scheme != 'https' or parsed.hostname not in {'www.xiaohongshu.com', 'xiaohongshu.com'}
                    or parsed.username or parsed.password or parsed.port not in (None, 443)
                    or parsed.fragment or not match or any(ord(c) < 32 or c.isspace() for c in value)):
                raise ValueError
            return value, 'https://www.xiaohongshu.com/explore/' + match.group(1)
        except (ValueError, TypeError):
            raise ChannelError('请提供小红书完整 HTTPS 笔记链接，不支持短链或其他网站') from None

    def _sync_xhs(self, channel: dict, value: str) -> list[dict]:
        source_url, public_url = self._note_url(value)
        if channel.get('delivery_mode') != 'manual':
            raise ChannelError('小红书官方评论通道尚未接入，未切换为浏览器操作')
        if channel.get('enabled') is False:
            raise ChannelError('此通道已停用')
        profile_base = self._profile_base(channel)
        if not (profile_base / 'XiaohongshuProfile').is_dir():
            raise ChannelError('请先在该客户独立浏览器目录完成小红书登录')
        temporary_root = self.workspace / '.scratch' / 'interaction_imports'
        temporary_root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=temporary_root, prefix='comments-') as name:
                directory = Path(name); os.chmod(directory, 0o700)
                output = directory / 'comments.json'
                output.touch(mode=0o600, exist_ok=False)
                # Access-bearing source URLs travel over stdin, not the OS
                # process argument list. The child only runs this fixed script.
                argv = [sys.executable, '-c',
                        'from easel.interaction_channels import _xhs_fetch_from_stdin; _xhs_fetch_from_stdin()']
                payload = json.dumps({'source_url': source_url, 'profile_base': str(profile_base), 'output': str(output)})
                completed = self.runner(argv, input=payload, cwd=str(ROOT), shell=False,
                                        capture_output=True, text=True, timeout=90)
                if completed.returncode != 0 or output.stat().st_size > 4 * 1024 * 1024:
                    raise ValueError
                data = json.loads(output.read_text(encoding='utf-8'))
                rows = data.get('comments')
                if not isinstance(rows, list):
                    raise ValueError
        except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
            raise ChannelError('小红书评论导入未完成，请检查独立登录态和笔记访问权限') from None
        items = []; seen = set()
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            identity = row.get('id'); text = row.get('content')
            if not isinstance(identity, str) or not _REMOTE_ID.fullmatch(identity) or not isinstance(text, str) or not text.strip() or identity in seen:
                continue
            seen.add(identity)
            parent = row.get('parent')
            thread = parent if isinstance(parent, str) and _REMOTE_ID.fullmatch(parent) else identity
            items.append({'external_id': identity, 'text': text[:10000], 'author_name': str(row.get('nickname') or '小红书用户')[:120],
                          'conversation_external_id': thread, 'source_url': public_url, 'subject': '小红书笔记评论'})
        return items

    def _sync_creatorhub(self, channel: dict) -> list[dict]:
        binding = self._creatorhub_binding(channel)
        if not binding or channel.get('enabled') is False:
            raise ChannelError('CreatorHub 通道未绑定或已停用')
        port, account_id, work_id, item_id = binding
        origin = f'http://127.0.0.1:{port}'

        def read(client, path, **params):
            response = client.get(origin + path, params=params)
            response.raise_for_status()
            rows = response.json()
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ChannelError('CreatorHub 返回的资料格式不正确')
            return rows

        try:
            with self._http() as client:
                accounts = read(client, '/api/accounts', platform='douyin')
                if not any(row.get('id') == account_id and row.get('platform') == 'douyin'
                           and row.get('status') == 'active' and (row.get('has_read_login') is True
                                                                or row.get('has_creator') is True)
                           for row in accounts):
                    raise ChannelError('绑定的 CreatorHub 账号不可用')
                works = read(client, '/api/account-works', account_id=account_id)
                work = next((row for row in works if row.get('id') == work_id
                             and row.get('account_id') == account_id and row.get('platform') == 'douyin'
                             and row.get('item_id') == item_id), None)
                if work is None:
                    raise ChannelError('CreatorHub 作品与账号绑定不一致')
                comments = read(client, f'/api/account-works/{work_id}/comments', limit=200)
        except (httpx.HTTPError, ValueError):
            raise ChannelError('无法读取本机 CreatorHub，请检查服务与账号状态') from None

        path = 'note' if work.get('media_type') == 'images' else 'video'
        source_url = f'https://www.douyin.com/{path}/{item_id}'
        subject = str(work.get('desc') or '抖音自有作品评论')[:300]
        items = []
        for row in reversed(comments[:200]):
            comment_id = row.get('comment_id')
            if (row.get('watch_id') != 0 or row.get('platform') != 'douyin' or row.get('aweme_id') != item_id
                    or not isinstance(comment_id, str) or not re.fullmatch(r'[0-9]{1,32}', comment_id)):
                raise ChannelError('CreatorHub 评论来源与作品绑定不一致')
            content = row.get('text')
            if not isinstance(content, str) or not content.strip():
                continue
            items.append({'external_id': f'creatorhub:douyin:{item_id}:{comment_id}', 'text': content[:20000],
                          'author_name': str(row.get('user_nickname') or '访客')[:256],
                          'source_url': source_url, 'subject': subject})
        return items

    def send(self, channel: dict, conversation: dict, draft: dict, operation_id: str) -> dict:
        def receipt(status: str, message: str, external_id: str = '') -> dict:
            return {'status': status, 'external_message_id': external_id, 'receipt_id': operation_id,
                    'message': message, 'mode': channel.get('delivery_mode', 'manual')}
        if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
            return receipt('failed', '发送操作编号无效')
        capability = self.capabilities(channel)
        if not capability['can_send']:
            return receipt('failed', capability['message'])
        target = conversation.get('last_inbound_external_id', '')
        text = draft.get('text', '')
        if not isinstance(target, str) or not _REMOTE_ID.fullmatch(target):
            return receipt('failed', '缺少精确评论编号，未按昵称猜测发送目标')
        if not isinstance(text, str) or not text.strip() or len(text) > 2000 or '\x00' in text:
            return receipt('failed', '回复内容为空或超过平台通道限制')
        token, account, video, _ = self._douyin_credentials(channel)
        if token in text:
            return receipt('failed', '回复含有通道凭据，未发送')
        try:
            with self._http() as client:
                response = client.post(DOUYIN_API + '/video/comment/reply/', params={'open_id': account},
                    headers={'access-token': token, 'Content-Type': 'application/json'},
                    json={'item_id': video, 'comment_id': target, 'content': text})
        except httpx.HTTPError:
            return receipt('unknown', '发送结果待核对，已保留原操作编号，未自动重发')
        if response.status_code in (400, 401, 403, 404, 405, 422, 429):
            return receipt('failed', '平台未接受回复，请核对权限或参数')
        if response.status_code != 200:
            return receipt('unknown', '平台返回结果不明，请核对原消息，未自动重发')
        try:
            data = self._business_data(response)
        except ChannelError:
            try:
                payload = response.json()
                rejected = isinstance(payload.get('data'), dict) and payload['data'].get('error_code') not in (None, 0)
            except (ValueError, AttributeError):
                rejected = False
            return receipt('failed' if rejected else 'unknown', '平台未接受回复，请核对权限或参数' if rejected else '平台发送回执无法确认，请人工核对')
        identity = data.get('comment_id')
        if not isinstance(identity, str) or not _REMOTE_ID.fullmatch(identity):
            return receipt('unknown', '平台未返回明确回复编号，请人工核对，未自动重发')
        return receipt('sent', '平台已返回回复编号', identity)


def channel_capabilities(channel: dict | None = None):
    return InteractionChannels().capabilities(channel)


def _xhs_fetch_from_stdin():
    """Private subprocess entry: no URL token appears in the OS argv."""
    import runpy
    payload = json.load(sys.stdin)
    source_url, _ = InteractionChannels._note_url(payload['source_url'])
    script = ROOT / 'skills' / 'shared' / 'scripts' / 'xhs_comment.py'
    sys.argv = [str(script), 'fetch', '--url', source_url, '--profile-base', payload['profile_base'],
                '--no-proxy', '--scroll', '4', '--max', '100', '--out', payload['output']]
    runpy.run_path(str(script), run_name='__main__')


def sync_channel(channel: dict, source_url: str = '') -> list[dict]:
    return InteractionChannels().sync(channel, source_url)


def send_reply(channel: dict, conversation: dict, draft: dict, operation_id: str) -> dict:
    return InteractionChannels().send(channel, conversation, draft, operation_id)
