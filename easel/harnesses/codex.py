"""Codex native app-server adapter; credentials remain in Codex."""
from __future__ import annotations

import asyncio
import json
import re

from easel import __version__
from easel.agent_policy import requests_secret

from .base import Descriptor, HarnessError, RunRequest
from .commands import base_command, codex_override
from .diagnostics import codex_failure_message
from .events import EventProjector
from .permissions import can_auto_approve
from .rpc import connection
from .workspace import harness_instructions


# thread/start is the only native API that accepts dynamicTools. This tool is
# deliberately eager and independent of request_user_input's collaboration-mode
# gate. Old native threads are resumed unchanged, never silently replaced.
EASEL_ASK_USER_TOOL = {
    "type": "function", "name": "easel_ask_user", "deferLoading": False,
    "description": (
        "Ask one to three necessary questions through Easel's visible input cards, then continue the task using "
        "the returned answers. Use this tool instead of request_user_input, which may be unavailable in the "
        "current mode. For ordinary reversible preferences supply reasonable defaultAnswers and mark a recommended "
        "option: Easel may use defaults after its server-side waiting period. Do not ask unnecessary preferences; "
        "complete the work autonomously when sufficient information is known. Permissions, public publishing, "
        "deletion and payments still require real human authorization; this tool never grants execution permission. "
        "Never request passwords, API keys, login codes or other secret credentials."
    ),
    "inputSchema": {
        "type": "object", "additionalProperties": False, "required": ["questions"],
        "properties": {"questions": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "question"],
            "properties": {
                "id": {"type": "string", "minLength": 1, "maxLength": 120},
                "header": {"type": "string", "maxLength": 100},
                "question": {"type": "string", "minLength": 1, "maxLength": 3000},
                "multiSelect": {"type": "boolean"}, "requiresHuman": {"type": "boolean"},
                "isSecret": {"type": "boolean"},
                "defaultAnswers": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 1000}},
                "options": {"type": "array", "maxItems": 12, "items": {
                    "type": "object", "additionalProperties": False, "required": ["label"], "properties": {
                        "label": {"type": "string", "minLength": 1, "maxLength": 240},
                        "description": {"type": "string", "maxLength": 1000},
                        "recommended": {"type": "boolean"},
                    }}},
            },
        }}},
    },
}


# Schema and description migrated from deepseek-ai/deepseek-harness (MIT,
# Copyright (c) 2026 DeepSeek), commit ce7073d51254c26cd63b22a60835b8f948deb447:
# packages/deliverables/tool-present/src/index.ts:38-58. The native dynamic-tool
# envelope uses JSON Schema required arrays instead of DSH's required:true
# parameter descriptors. Source notice: integrations/deepseek-harness/LICENSE.
EASEL_PRESENT_TOOL = {
    "type": "function", "name": "present", "deferLoading": False,
    "description": (
        "Declare existing files as final deliverables for the user. "
        "Hosts may show presented files only after your turn ends, so call it once the files are final. "
        "If the user needs to review files earlier, such as before a tool call that waits for their answer, link them in your message instead. "
        "Use it when the user needs a separate file, especially Office documents, spreadsheets, and slide decks; "
        "prefer your final response when that suffices. The user opens the current files; their contents are not copied. "
        "In Easel, save files under the current IP workspace outputs directory; the host validates access and returns canonical metadata."
    ),
    "inputSchema": {
        "type": "object", "additionalProperties": False, "required": ["files"],
        "properties": {"files": {
            "type": "array", "minItems": 1, "maxItems": 8,
            "description": "Usually the 1-2 most important deliverables; at most 4 per call.",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["path"],
                "properties": {
                    "path": {"type": "string", "description": "Path of an existing regular file. Relative paths use the Session working directory."},
                    "description": {"type": "string", "description": "Brief description for the user."},
                },
            },
        }},
    },
}


# Reuse the native eager-tool envelope and host-callback separation migrated
# above from DeepSeek Harness tool-present (MIT, ce7073d). The publication schema
# itself is Easel-owned: the host applies the one-shot/fail-closed lifecycle of
# interaction/user-approval rather than treating an ask_user answer as a grant.
_PUBLICATION_PLATFORMS = ('xiaohongshu', 'douyin', 'kuaishou', 'weixin-channels',
                          'zhihu', 'bilibili', 'wechat-oa')
_PUBLICATION_TEXT_LIMITS = {'title': 1000, 'body': 100000, 'tags': 2000, 'persona': 120}
EASEL_PUBLISH_TOOL = {
    'type': 'function', 'name': 'easel_publish', 'deferLoading': False,
    'description': (
        'Request publication through Easel\'s typed host workflow. The host resolves the current IP account, '
        'freezes the exact content and immutable artifact versions, and requires real human approval before '
        'any external submission. Supply only the listed content fields and artifact IDs returned by present. '
        'Do not provide paths, commands, URLs, account identities or claimed approvals. A pending, rejected or '
        'submitted receipt is not proof that content was published. Never bypass this workflow with shell, '
        'browser, scripts or generic ask_user approval.'
    ),
    'inputSchema': {
        'type': 'object', 'additionalProperties': False, 'required': ['platform'],
        'properties': {
            'platform': {'type': 'string', 'enum': list(_PUBLICATION_PLATFORMS)},
            **{name: {'type': 'string', 'maxLength': limit} for name, limit in _PUBLICATION_TEXT_LIMITS.items()},
            'artifactIds': {'type': 'array', 'maxItems': 32, 'uniqueItems': True,
                            'items': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}},
        },
        'anyOf': [{'required': [name], 'properties': {name: {'minLength': 1}}} for name in ('title', 'body')],
    },
}


def _publication_payload(arguments) -> dict:
    if not isinstance(arguments, dict) or set(arguments) - {'platform', 'artifactIds', *_PUBLICATION_TEXT_LIMITS}:
        raise ValueError('invalid publication arguments')
    platform = arguments.get('platform')
    if not isinstance(platform, str) or platform not in _PUBLICATION_PLATFORMS:
        raise ValueError('invalid publication platform')
    payload = {'platform': platform}
    for name, limit in _PUBLICATION_TEXT_LIMITS.items():
        value = arguments.get(name, '')
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('invalid publication text')
        payload[name] = value
    if not payload['title'].strip() and not payload['body'].strip():
        raise ValueError('empty publication content')
    artifacts = arguments.get('artifactIds', [])
    if (not isinstance(artifacts, list) or len(artifacts) > 32
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value) for value in artifacts)
            or len(set(artifacts)) != len(artifacts)):
        raise ValueError('invalid publication artifact IDs')
    return {**payload, 'artifactIds': list(artifacts)}


def _publication_failure(*, unsupported=False) -> dict:
    message = (
        'The typed publication host channel is not enabled on this route. No publication was confirmed. '
        'Do not substitute shell, browser or generic question approval.'
        if unsupported else
        'The host did not accept this publication request. Supply only a supported platform, title/body, '
        'tags, persona and immutable artifact IDs using the declared schema. No publication was confirmed.'
    )
    return {'success': False, 'contentItems': [{'type': 'inputText', 'text': message}]}


def _publication_instructions(host_enabled: bool) -> str:
    if host_enabled:
        return ('\n\n## Easel typed publication workflow\nWhen easel_publish is present in the native tool list, '
                'public publication must use it. Codex may restore already registered tools when resuming '
                'an existing thread; a fresh client connection does not mean those tools are absent. '
                'Its host owns the exact payload and real human one-shot approval. Shell/browser/script '
                'execution permission and easel_ask_user answers are not publication approval; do not '
                'use either to replace or bypass this tool. Never invent a publication subject, account '
                'identity, approval or receipt. A rejection, cancellation or unavailable host stops the '
                'external action; finish independent preparation without claiming publication. '
                'If an older native thread never registered easel_publish and its tool list lacks it, '
                'keep the original thread and history; use a new conversation with the tool enabled '
                'or an explicit supported migration. Do not claim that resume can append new tool definitions.')
    return ('\n\n## Easel typed publication workflow\nThis request has no enabled typed publication host channel. '
            'Preserve the existing thread and history; do not publish through shell, browser or scripts, '
            'even if a native tool definition was retained. '
            'Generic ask_user answers never substitute for an exact-content publication approval.')


def _present_files(arguments) -> list[dict]:
    if not isinstance(arguments, dict) or set(arguments) != {"files"}:
        raise ValueError("invalid present arguments")
    files = arguments["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= 8:
        raise ValueError("invalid file count")
    for file in files:
        if (not isinstance(file, dict) or set(file) - {"path", "description"}
                or not isinstance(file.get("path"), str) or not file["path"].strip()
                or ("description" in file and not isinstance(file["description"], str))):
            raise ValueError("invalid presented file")
    return files


def _dynamic_questions(arguments) -> list[dict]:
    schema = EASEL_ASK_USER_TOOL["inputSchema"]["properties"]["questions"]["items"]
    if not isinstance(arguments, dict) or set(arguments) != {"questions"}:
        raise ValueError("invalid questions")
    questions = arguments["questions"]
    if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
        raise ValueError("invalid questions")
    identifiers = set()
    normalized = []
    for question in questions:
        if not isinstance(question, dict) or set(question) - set(schema["properties"]):
            raise ValueError("invalid question")
        identifier, text = question.get("id"), question.get("question")
        if (not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 120
                or identifier in identifiers or identifier in {"__proto__", "constructor", "prototype"}
                or any(ord(character) < 32 for character in identifier)
                or not isinstance(text, str) or not text.strip() or len(text) > 3000):
            raise ValueError("invalid question")
        identifiers.add(identifier)
        if "header" in question and (not isinstance(question["header"], str) or len(question["header"]) > 100):
            raise ValueError("invalid header")
        for flag in ("multiSelect", "requiresHuman", "isSecret"):
            if flag in question and not isinstance(question[flag], bool):
                raise ValueError("invalid question flag")
        options = question.get("options", [])
        if not isinstance(options, list) or len(options) > 12:
            raise ValueError("invalid options")
        labels = set()
        for option in options:
            if (not isinstance(option, dict) or set(option) - {"label", "description", "recommended"}
                    or not isinstance(option.get("label"), str) or not option["label"].strip()
                    or len(option["label"]) > 240 or option["label"] in labels
                    or ("description" in option and (not isinstance(option["description"], str) or len(option["description"]) > 1000))
                    or ("recommended" in option and not isinstance(option["recommended"], bool))):
                raise ValueError("invalid option")
            labels.add(option["label"])
        defaults = question.get("defaultAnswers", [])
        if (not isinstance(defaults, list) or len(defaults) > 12
                or any(not isinstance(value, str) or not value.strip() or len(value) > 1000
                       or (labels and value not in labels) for value in defaults)):
            raise ValueError("invalid defaults")
        normalized.append({"questionId": identifier, **{key: value for key, value in question.items() if key != "id"}})
    if requests_secret(normalized):
        raise ValueError("secret input is not supported")
    return normalized


class CodexAdapter:
    descriptor = Descriptor("codex", "Codex", "app-server", "npm install -g @openai/codex；codex login")

    async def run(self, request: RunRequest, emit, ask, bind):
        if request.sandbox not in ("read-only", "workspace-write"):
            raise HarnessError("Codex 只允许只读或工作区写入权限")
        completion = asyncio.get_running_loop().create_future()
        thread_id = request.native_id
        turn_id = ""
        streamed: set[str] = set()
        events = EventProjector(emit, request.env)

        def notify(method, params):
            def _thread_scoped() -> bool:
                # 无 threadId/turnId 的是全局事件（连接级/认证失败常在 turn/start 前发出）；
                # 带 id 的必须匹配本会话。放行判定独立于 turn_id 是否已闩锁。
                if params.get("threadId") is None and params.get("turnId") is None:
                    return True
                return bool(turn_id) and params.get("threadId") == thread_id \
                    and params.get("turnId", turn_id) == turn_id

            # 致命 error 事件常不带 thread 上下文（如 turn/start 前的认证失败）；
            # 放在 id 过滤之前处理，否则只能等外层 wait_for 超时（30min+）才报错。
            if method == "error":
                if params.get("willRetry", False) or not _thread_scoped():
                    return
                if not completion.done():
                    completion.set_exception(HarnessError(codex_failure_message(params.get("error"))))
                return

            if not turn_id or params.get("threadId") != thread_id or params.get("turnId", turn_id) != turn_id:
                return
            events.codex(method, params)
            if method == "item/agentMessage/delta":
                streamed.add(params.get("itemId", ""))
                emit("token", params.get("delta", ""))
            elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
                emit("thinking", params.get("delta", ""))
            elif method == "item/started":
                item = params.get("item") or {}
                if item.get("type") not in ("agentMessage", "reasoning", "userMessage"):
                    events.activity(f"工具：{item.get('type', 'tool')}")
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("id") not in streamed:
                    emit("token", item.get("text", ""))
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("id") != turn_id or completion.done():
                    return
                if turn.get("status") == "completed":
                    completion.set_result(None)
                else:
                    completion.set_exception(HarnessError(codex_failure_message(turn.get("error"))))

        async def respond(method, params):
            if params.get("threadId") != thread_id or (
                    turn_id and params.get("turnId", turn_id) != turn_id):
                raise HarnessError("不属于当前会话的请求")
            if method == "item/tool/call":
                if params.get('tool') == 'easel_publish':
                    # Native Codex persists dynamic tool definitions and routes
                    # them again after thread/resume, including across processes.
                    # A process-local registration cache is not an authority or
                    # a reliable capability test. The typed host still owns all
                    # actor/IP checks, exact approval and durable execution.
                    if request.publish is None or params.get('namespace') not in (None, ''):
                        return _publication_failure(unsupported=True)
                    if not turn_id or params.get('turnId') != turn_id:
                        raise HarnessError('发布工具请求不属于当前回合')
                    try:
                        payload = _publication_payload(params.get('arguments'))
                        call_id = params.get('callId')
                        if (not isinstance(call_id, str) or not call_id.strip()
                                or len(call_id) > 256 or any(ord(character) < 32 for character in call_id)):
                            raise ValueError('invalid publication call identity')
                        receipt = await request.publish(payload, call_id)
                        if not isinstance(receipt, dict) or not receipt:
                            raise ValueError('invalid host publication receipt')
                        text = json.dumps(receipt, ensure_ascii=False, allow_nan=False)
                    except Exception:
                        # Validation/host failures must not echo credentials or
                        # host paths. CancelledError inherits BaseException and
                        # deliberately propagates to the native RPC cleanup.
                        return _publication_failure()
                    return {'success': True, 'contentItems': [{'type': 'inputText', 'text': text}]}
                if params.get("tool") == "present" and params.get("namespace") in (None, ""):
                    try:
                        files = _present_files(params.get("arguments"))
                        call_id = params.get("callId")
                        if (request.present is None or not isinstance(call_id, str) or not call_id
                                or any(ord(character) < 32 for character in call_id)):
                            raise ValueError("present is unavailable")
                        metadata = await request.present(files, call_id)
                        if not isinstance(metadata, list) or not metadata or any(not isinstance(file, dict) for file in metadata):
                            raise ValueError("present was not accepted")
                        result = json.dumps({"files": metadata}, ensure_ascii=False, allow_nan=False)
                    except (ValueError, TypeError, OSError, HarnessError):
                        # Do not echo rejected host paths, credentials or model
                        # metadata, and do not swallow CancelledError.
                        return {"success": False, "contentItems": [{"type": "inputText", "text":
                            "File delivery was not accepted. Supply 1-8 existing regular files using only path and optional description, "
                            "save them under the current IP outputs directory, and retry. No delivery was confirmed."}]}
                    return {"success": True, "contentItems": [{"type": "inputText", "text": result}]}
                if params.get("tool") != "easel_ask_user" or params.get("namespace") not in (None, ""):
                    raise HarnessError("不支持的 Codex 动态工具")
                try:
                    normalized = _dynamic_questions(params.get("arguments"))
                except ValueError:
                    return {"success": False, "contentItems": [{"type": "inputText", "text":
                        "Invalid question input. Supply 1-3 uniquely identified non-secret questions using only the declared schema. "
                        "Never request credentials; use the native terminal for authentication."}]}
                answers = await ask(normalized)
                return {"success": True, "contentItems": [{"type": "inputText", "text": json.dumps(
                    {"answers": answers}, ensure_ascii=False)}]}
            if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                if can_auto_approve(method, params, request):
                    emit("activity", "Codex：已自动通过普通操作授权")
                    return {"decision": "accept"}
                detail = params.get("command") or params.get("reason") or params.get("itemId", "")
                answers = await ask([{
                    "questionId": "permission", "header": "执行授权",
                    "question": f"Codex 请求额外权限（仅本次）：{str(detail)[:3000]}" + (
                        "\n此操作不属于可自动批准的普通操作，请确认风险。" if request.codex_auto_approve else ""),
                    "options": [{"label": "允许本次"}, {"label": "拒绝"}],
                }])
                return {"decision": "accept" if answers.get("permission") == ["允许本次"] else "decline"}
            if method == "item/tool/requestUserInput":
                questions = params.get("questions") or []
                if any(question.get("isSecret") for question in questions):
                    raise HarnessError("请在终端完成敏感凭证输入")
                normalized = [{**question, "questionId": question["id"]} for question in questions]
                answers = await ask(normalized)
                return {"answers": {key: {"answers": value} for key, value in answers.items()}}
            raise HarnessError("不支持的 Codex 客户端请求")

        command = list(request.command) if request.command else (
            codex_override(request.env) or base_command("codex")) + ["app-server"]
        async with connection(request, command, notify, respond) as rpc:
            if rpc.initialized is None:
                rpc.initialized = await rpc.call("initialize", {
                    "clientInfo": {"name": "easel", "version": __version__},
                    "capabilities": {"experimentalApi": True}})
                await rpc.send({"method": "initialized"})
            options = {"cwd": str(request.cwd), "approvalPolicy": "on-request",
                       "sandbox": request.sandbox,
                       "developerInstructions": harness_instructions(request) + _publication_instructions(
                           request.publish is not None)}
            if request.model:
                options["model"] = request.model
            if thread_id and rpc.native_id == thread_id:
                result = {"thread": {"id": thread_id}}
            elif thread_id:
                result = await rpc.call("thread/resume", {**options, "threadId": thread_id})
            else:
                tools = [EASEL_ASK_USER_TOOL]
                if request.present is not None:
                    tools.append(EASEL_PRESENT_TOOL)
                if request.publish is not None:
                    tools.append(EASEL_PUBLISH_TOOL)
                result = await rpc.call("thread/start", {**options, "dynamicTools": tools})
            resumed_id = (result.get("thread") or {}).get("id", "")
            if request.native_id and resumed_id != request.native_id:
                raise HarnessError("Codex 返回了不同的原生会话 ID，不会替换已有历史")
            thread_id = resumed_id
            if not thread_id:
                raise HarnessError("Codex 未返回原生会话 ID")
            bind(thread_id)
            rpc.native_id = thread_id
            policy = {"type": "readOnly"} if request.sandbox == "read-only" else {
                "type": "workspaceWrite", "writableRoots": [str(request.cwd)],
                "networkAccess": False}
            params = {"threadId": thread_id, "input": [{"type": "text", "text": request.prompt}],
                      "approvalPolicy": "on-request", "sandboxPolicy": policy}
            if request.model:
                params["model"] = request.model
            early: list[tuple[str, dict]] = []
            original_notify = rpc.notify
            rpc.notify = lambda method, data: early.append((method, data))
            result = await rpc.call("turn/start", params)
            turn_id = (result.get("turn") or {}).get("id", "")
            if not turn_id:
                raise HarnessError("Codex 未返回本轮 ID")
            rpc.notify = original_notify
            for method, data in early:
                notify(method, data)
            try:
                await rpc.wait_notification(completion)
            except asyncio.CancelledError:
                try:
                    await rpc.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=2)
                except HarnessError:
                    pass
                raise
            finally:
                if completion.done() and not completion.cancelled():
                    completion.exception()
