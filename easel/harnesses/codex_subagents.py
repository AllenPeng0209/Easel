"""Read recent messages from a Host-authorized Codex child via private stdio."""
from __future__ import annotations

import asyncio

from easel import __version__

from .base import HarnessError, RunRequest
from .commands import base_command, codex_override
from .events import EventProjector, native_subagent_descriptors, valid_native_subagent_id
from .rpc import RPC

_MAX_ITEMS = 50
_MAX_CONTENT = 4000
_READ_ONLY_FLAGS = (
    '--disable', 'shell_tool',
    '--disable', 'apps',
    '--disable', 'plugins',
    '--disable', 'hooks',
    '--disable', 'multi_agent',
    '--disable', 'browser_use',
    '--disable', 'browser_use_external',
    '--disable', 'in_app_browser',
    '--disable', 'code_mode',
    '-c', 'web_search="disabled"',
    '-c', 'mcp_servers={}',
)


def _message(entry: object, projector: EventProjector) -> dict | None:
    if not isinstance(entry, dict) or not isinstance(entry.get('turnId'), str):
        return None
    item = entry.get('item')
    if not isinstance(item, dict) or not isinstance(item.get('id'), str):
        return None
    if item.get('type') == 'userMessage' and isinstance(item.get('content'), list):
        role = 'user'
        text = '\n\n'.join(part['text'] for part in item['content'][:_MAX_ITEMS]
                         if isinstance(part, dict) and part.get('type') == 'text'
                         and isinstance(part.get('text'), str))
    elif item.get('type') == 'agentMessage' and isinstance(item.get('text'), str):
        role, text = 'assistant', item['text']
    else:
        return None
    if not text.strip():
        return None
    safe, _ = projector._safe(text)
    return {'role': role, 'content': safe[:_MAX_CONTENT]}


async def _read(request: RunRequest, parent_id: str, child_id: str) -> dict:
    command = list(request.command) if request.command else (
        (codex_override(request.env) or base_command('codex')) + list(_READ_ONLY_FLAGS) + ['app-server'])
    projector = EventProjector(lambda *_: None, request.env)

    async def reject(*_args):
        raise HarnessError('读取子 Agent 记录期间禁止工具请求')

    async with RPC(request, command, lambda *_: None, reject) as rpc:
        await rpc.call('initialize', {
            'clientInfo': {'name': 'easel', 'version': __version__},
            'capabilities': {'experimentalApi': True},
        }, timeout=5)
        await rpc.send({'method': 'initialized'})
        metadata = await rpc.call('thread/read', {'threadId': child_id, 'includeTurns': False}, timeout=5)
        thread = metadata.get('thread') if isinstance(metadata, dict) else None
        if (not isinstance(thread, dict) or thread.get('id') != child_id
                or thread.get('parentThreadId') != parent_id):
            raise HarnessError('子 Agent 的原生身份无法核验')
        page = await rpc.call('thread/items/list', {
            'threadId': child_id, 'limit': _MAX_ITEMS, 'sortDirection': 'desc',
        }, timeout=5)
        if not isinstance(page, dict) or not isinstance(page.get('data'), list):
            raise HarnessError('无效的子 Agent 消息记录')
        cursor = page.get('nextCursor')
        if cursor is not None and not isinstance(cursor, str):
            raise HarnessError('无效的子 Agent 消息游标')
        items = page['data']
        messages = []
        children = {}
        for entry in reversed(items[:_MAX_ITEMS]):
            if (message := _message(entry, projector)) is not None:
                messages.append(message)
            if isinstance(entry, dict) and isinstance(entry.get('turnId'), str):
                for child in native_subagent_descriptors(entry.get('item'), parent_id=child_id):
                    children[child['childThreadId']] = child
        # Private observations go to the Host registry. Only the message
        # projection and page indicator belong in a public response.
        return {'messages': messages, 'hasMore': bool(cursor) or len(items) > _MAX_ITEMS,
                'observedChildren': list(children.values())[-_MAX_ITEMS:]}


async def read_subagent(request: RunRequest, *, parent_id: str, child_id: str) -> dict:
    """Read a scoped child edge after the caller checks its local registry and access."""
    if not valid_native_subagent_id(parent_id) or not valid_native_subagent_id(child_id) or child_id == parent_id:
        raise HarnessError('无效的子 Agent 会话身份')
    try:
        return await asyncio.wait_for(_read(request, parent_id, child_id), timeout=15)
    except (HarnessError, OSError, asyncio.TimeoutError):
        raise HarnessError('子 Agent 消息暂不可用') from None
