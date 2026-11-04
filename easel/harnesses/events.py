"""Native protocol -> durable, bounded conversation events.

The stable call identity, paired lifecycle and parent edge follow the DeepSeek
Harness conversation contract (MIT, Copyright (c) 2026 DeepSeek):
  packages/client/ui-chat/src/client/model/tool-call-tree.ts
  packages/client/ui-tool/src/client/tool/models/tool-call-model.ts
at ce7073d51254c26cd63b22a60835b8f948deb447.  Native Codex/ACP field
translation and credential filtering are Easel's host boundary adaptation.
The upstream notice is retained in integrations/deepseek-harness/LICENSE.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Callable


_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|(?:id|access|refresh|session|auth)[_-]?token|secret|password|passwd|"
    r"authorization|cookie|credential|private[_-]?key|session[_-]?token|^token$|^auth$)", re.I)
_ASSIGNMENT = re.compile(
    r"(\b(?:[\w-]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd|"
    r"cookie|authorization|private[_-]?key)[\w-]*|token)\b[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)", re.I)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]+=*", re.I)
_API_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}")
_URL_PASSWORD = re.compile(r"(https?://)[^\s/@:]+:[^\s/@]+@", re.I)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----[\s\S]*?(?:-----END (?P=label)-----|$)")
_PRIVATE_KEY_START = re.compile(r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
MAX_TEXT = 32000
_TRUNCATED = "\n[truncated: remaining content omitted]"

_CONFIRMED_TOOL_STATUSES = ("completed", "failed", "declined", "interrupted")
_SETTLED_TOOL_STATUSES = (*_CONFIRMED_TOOL_STATUSES, "unconfirmed")
_LATE_TOOL_CONTEXT = ("input", "title", "toolKind", "locations", "startedAt")
_NATIVE_TOOL_MARKERS = ("nativeBusinessTool", "nativePublicationTool", "nativeWebSearch")
_CODEX_STATUSLESS_TOOLS = ("webSearch", "imageView", "sleep", "functionCallOutput")
_WEB_ACTION_TEXT_FIELDS = {
    "search": ("query",),
    "openPage": ("url",),
    "findInPage": ("url", "pattern"),
    "other": (),
}
_ACP_TOOL_STATUSES = {
    "pending": "preparing", "in_progress": "running", "completed": "completed", "failed": "failed",
}
_TOKEN_USAGE_FIELDS = ("inputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens",
                       "reasoningOutputTokens", "totalTokens")


def _token_count(value, *, capacity=False) -> int | None:
    return value if type(value) is int and (1 if capacity else 0) <= value < 2**53 else None


def _token_breakdown(value: dict) -> dict:
    return {key: count for key in _TOKEN_USAGE_FIELDS
            if (count := _token_count(value.get(key))) is not None}

_COLLAB_TOOLS = frozenset({
    "spawnAgent", "sendInput", "resumeAgent", "wait", "closeAgent",
    "sendMessage", "followupTask", "interruptAgent", "listAgents",
})
_NATIVE_SUBAGENT_STATUS = {
    "pendingInit": "queued", "running": "running", "completed": "completed",
    "errored": "failed", "interrupted": "interrupted", "shutdown": "closed",
    "notFound": "unavailable",
}
SUBAGENT_STATUSES = frozenset({*_NATIVE_SUBAGENT_STATUS.values(), "unknown"})


def valid_native_subagent_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) is not None


def native_subagent_descriptors(item, *, parent_id=None, live=False) -> list[dict]:
    """Translate completed native collab observations into private edge candidates.

    A historical child-page read supplies its verified parent_id and discovers
    only spawn edges. Live callers supply the notification envelope's parent
    and also receive status observations for already known edges.
    """
    if not isinstance(item, dict):
        return []
    if item.get("type") == "subAgentActivity":
        # Current Codex reports spawn in this item, without a senderThreadId.
        # Only the trusted envelope or verified native history supplies a parent.
        child, kind = item.get("agentThreadId"), item.get("kind")
        if not valid_native_subagent_id(parent_id) or not valid_native_subagent_id(child) or child == parent_id:
            return []
        if kind == "started":
            action, status = "spawn", "running"
        elif live and kind in ("completed", "interrupted"):
            action, status = "update", kind
        else:
            return []
        return [{"parentThreadId": parent_id, "childThreadId": child,
                 "action": action, "status": status}]
    if item.get("type") != "collabAgentToolCall" or item.get("status") != "completed":
        return []
    sender, tool = item.get("senderThreadId"), item.get("tool")
    receivers = item.get("receiverThreadIds")
    if (not valid_native_subagent_id(sender) or not isinstance(tool, str) or tool not in _COLLAB_TOOLS
            or not isinstance(receivers, list)
            or parent_id is not None and (sender != parent_id or not live and tool != "spawnAgent")):
        return []
    action = "spawn" if tool == "spawnAgent" else "update"
    states = item.get("agentsStates")
    if not isinstance(states, dict):
        states = {}
    seen = set()
    result = []
    for child in receivers:
        if not valid_native_subagent_id(child) or child == sender or child in seen:
            continue
        seen.add(child)
        state = states.get(child)
        state = state.get("status") if isinstance(state, dict) else None
        status = _NATIVE_SUBAGENT_STATUS.get(state) if isinstance(state, str) else None
        if status is not None or action == "spawn":
            result.append({"parentThreadId": sender, "childThreadId": child,
                           "action": action, "status": status or "unknown"})
    return result


def _now() -> int:
    return int(time.time() * 1000)


def _codex_tool_status(kind, native_status, completed: bool) -> str:
    if native_status in ("failed", "declined", "interrupted"):
        return native_status
    if not completed:
        return "running"
    if native_status == "completed" or (native_status is None and kind in _CODEX_STATUSLESS_TOOLS):
        return "completed"
    return "unconfirmed"


def _web_search_input(item: dict) -> dict:
    query = item.get("query")
    result = {"query": query} if "query" in item and (query is None or isinstance(query, str)) else {}
    action = item.get("action")
    if action is None and "action" in item:
        result["action"] = None
    if not isinstance(action, dict):
        return result
    kind = action.get("type")
    if not isinstance(kind, str) or kind not in _WEB_ACTION_TEXT_FIELDS:
        return result
    normalized = {"type": kind}
    for key in _WEB_ACTION_TEXT_FIELDS[kind]:
        value = action.get(key)
        if key in action and (value is None or isinstance(value, str)):
            normalized[key] = value
    queries = action.get("queries")
    if kind == "search" and "queries" in action:
        if queries is None or (isinstance(queries, list) and all(isinstance(query, str) for query in queries[:101])):
            normalized["queries"] = queries
    result["action"] = normalized
    return result


def _valid_tool_parent(value, child_id) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 240 and value != child_id
            and value.strip() == value and not re.search(r"[\x00-\x1f\x7f-\x9f]", value))


def _valid_file_change_path(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not re.search(r"[\x00-\x1f\x7f-\x9f]", value)


def _file_changes(value) -> list[dict]:
    """Retain only the native FileUpdateChange fields that were actually supplied."""
    if not isinstance(value, list):
        return []
    changes = []
    for item in value:
        if not isinstance(item, dict) or not _valid_file_change_path(item.get("path")):
            continue
        change = {"path": item["path"]}
        if isinstance(item.get("diff"), str):
            change["diff"] = item["diff"]
        kind = item.get("kind")
        if isinstance(kind, dict) and kind.get("type") in ("add", "delete", "update"):
            mutation = {"type": kind["type"]}
            if kind["type"] == "update" and "move_path" in kind:
                target = kind["move_path"]
                if target is None or _valid_file_change_path(target):
                    mutation["move_path"] = target
            change["kind"] = mutation
        changes.append(change)
    return changes


class EventProjector:
    """Fold partial native updates, then emit complete safe call snapshots."""

    def __init__(self, emit: Callable, env: dict[str, str]):
        self.emit = emit
        self.calls: dict[str, dict] = {}
        self.context_compactions: dict[str, str] = {}
        self.private_streams: dict[str, str] = {}
        self.secrets = sorted({value for key, value in env.items()
                               if re.search(r"KEY|TOKEN|SECRET|PASSWORD|COOKIE|CREDENTIAL", key, re.I) and isinstance(value, str)
                               and 8 <= len(value) <= 65536}, key=len, reverse=True)

    def _safe(self, value):
        truncated = False
        remaining = 1000

        def clean(item, depth=0):
            nonlocal truncated, remaining
            remaining -= 1
            if remaining < 0 or depth > 10:
                truncated = True
                return "[truncated]"
            if isinstance(item, str):
                text = item
                for secret in self.secrets:
                    text = text.replace(secret, "[REDACTED]")
                text = _BEARER.sub("Bearer [REDACTED]", text)
                text = _API_TOKEN.sub("[REDACTED]", text)
                text = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
                text = _JWT.sub("[REDACTED TOKEN]", text)
                text = _URL_PASSWORD.sub(r"\1[REDACTED]@", text)
                text = _ASSIGNMENT.sub(r"\1[REDACTED]", text)
                if len(text) > MAX_TEXT:
                    truncated = True
                    return text[:MAX_TEXT] + _TRUNCATED
                return text
            if item is None or isinstance(item, (bool, int, float)):
                return item
            if isinstance(item, dict):
                result = {}
                for index, (key, val) in enumerate(item.items()):
                    if index >= 100 or remaining < 0:
                        truncated = True
                        result["_truncated"] = True
                        break
                    label = str(key)[:240]
                    result[label] = "[REDACTED]" if _SECRET_KEY.search(label) else clean(val, depth + 1)
                return result
            if isinstance(item, (list, tuple)):
                result = [clean(value, depth + 1) for value in item[:100]]
                if len(item) > 100:
                    truncated = True
                    result.append("[truncated]")
                return result
            # Native JSON should never reach this branch. A bounded name keeps
            # malformed values inspectable without invoking arbitrary __str__.
            return f"[unsupported {type(item).__name__}]"

        return clean(value), truncated

    def event(self, kind: str, **fields):
        payload, truncated = self._safe({"id": uuid.uuid4().hex, "kind": kind, **fields})
        if truncated:
            payload["truncated"] = True
        self.emit("agent_event", payload)

    def activity(self, text: str):
        """Keep the legacy textual summary on the same credential boundary."""
        safe, _ = self._safe(text)
        self.emit("activity", safe)

    def tool(self, call_id, **fields):
        if not isinstance(call_id, str) or not call_id or len(call_id) > 240:
            return
        previous = self.calls.get(call_id, {})
        before, after = previous.get("status"), fields.get("status")
        if (before == "unconfirmed" and after not in _SETTLED_TOOL_STATUSES
                or before == "running" and after == "preparing"):
            return
        if before in _CONFIRMED_TOOL_STATUSES:
            # The first confirmed result is the durable observation. Reordered
            # call data can only fill fields that were missing when it arrived.
            context = {key: fields[key] for key in _LATE_TOOL_CONTEXT
                       if key not in previous and fields.get(key) is not None}
            if "parentCallId" not in previous and _valid_tool_parent(fields.get("parentCallId"), call_id):
                context["parentCallId"] = fields["parentCallId"]
            if previous.get("name") in (None, "tool") and isinstance(fields.get("name"), str):
                context["name"] = fields["name"]
            if context.get("name", previous.get("name")) == fields.get("name"):
                context.update({key: True for key in _NATIVE_TOOL_MARKERS
                                if key not in previous and fields.get(key) is True})
            if not context:
                return
            fields = context
        if before == "unconfirmed" and after in _CONFIRMED_TOOL_STATUSES:
            previous = {key: value for key, value in previous.items()
                        if key not in ("exitCode", "durationMs", "completedAt")}
        # Preserve explicit null observations where the provider distinguishes them from omission.
        nullable_fields = ("exitCode", "output") if fields.get("nativeWebSearch") else ("exitCode",)
        merged = {**previous, "callId": call_id,
                  **{key: value for key, value in fields.items() if value is not None or key in nullable_fields}}
        if merged.get("status") == "running" and "startedAt" not in merged:
            merged["startedAt"] = _now()
        if merged.get("status") in _CONFIRMED_TOOL_STATUSES and "completedAt" not in merged:
            merged["completedAt"] = _now()
        # Store only the safe, bounded state too: an unlimited stream cannot
        # retain secrets or accumulate unbounded output between notifications.
        safe, truncated = self._safe(merged)
        if truncated or previous.get("truncated"):
            safe["truncated"] = True
        self.calls[call_id] = safe
        self.event("tool", **safe)

    @staticmethod
    def _plan_steps(items, *fields):
        if not isinstance(items, list):
            return None
        return [{field: item[field] for field in fields if field in item}
                if isinstance(item, dict) else None for item in items[:101]]

    def codex(self, method: str, params: dict):
        if method == "turn/plan/updated":
            self.event("plan", turnId=params.get("turnId"), source="codex",
                       steps=self._plan_steps(params.get("plan"), "step", "status"), explanation=params.get("explanation"))
            return
        if method == "thread/tokenUsage/updated":
            raw = params.get("tokenUsage")
            if not isinstance(raw, dict):
                return
            usage = {field: _token_breakdown(value) for field in ("total", "last")
                     if isinstance(value := raw.get(field), dict)}
            capacity = _token_count(raw.get("modelContextWindow"), capacity=True)
            if "modelContextWindow" in raw:
                usage["modelContextWindow"] = capacity
            # Codex's own context meter uses last.totalTokens directly for the
            # active context. The thread total is accumulated accounting only.
            used = usage.get("last", {}).get("totalTokens")
            self.event("usage", usage=usage, capacity=capacity, contextUsage={
                "source": "codex", "usedTokens": used, "capacityTokens": capacity})
            return
        if method == "thread/compacted":
            if not self.context_compactions:
                self.event("context", status="completed", source="codex", contextReset=True, summary="上下文已整理")
            return
        if method in ("item/commandExecution/outputDelta", "item/fileChange/outputDelta"):
            command_output = method == "item/commandExecution/outputDelta"
            identifier = params.get("itemId")
            prior = self.calls.get(identifier, {})
            if prior.get("status") in _SETTLED_TOOL_STATUSES:
                return
            delta = params.get("delta")
            if isinstance(delta, str):
                existing = prior.get("output") if isinstance(prior.get("output"), str) else ""
                private_label = self.private_streams.get(identifier)
                if private_label:
                    end = f"-----END {private_label}-----"
                    if end in delta:
                        delta = delta.split(end, 1)[1]
                        self.private_streams.pop(identifier, None)
                    else:
                        delta = ""
                pending = _PRIVATE_KEY_START.search(existing + delta)
                if pending and f"-----END {pending[1]}-----" not in (existing + delta)[pending.end():]:
                    self.private_streams[identifier] = pending[1]
                self.tool(identifier, name=prior.get("name", "bash" if command_output else "apply_patch"),
                          status=prior.get("status", "running"), output=existing + delta,
                          outputStream="merged" if command_output else None,
                          fileChangeApplied=None if command_output else prior.get("fileChangeApplied", False))
            return
        if method == "item/mcpToolCall/progress":
            identifier = params.get("itemId")
            prior = self.calls.get(identifier, {})
            if prior.get("status") in _SETTLED_TOOL_STATUSES:
                return
            self.tool(identifier, name=prior.get("name", "mcp"), status=prior.get("status", "running"),
                      progress=params.get("message"))
            return
        if method not in ("item/started", "item/completed"):
            return
        item = params.get("item") or {}
        if not isinstance(item, dict):
            return
        kind = item.get("type")
        completed = method == "item/completed"
        if kind in ("agentMessage", "userMessage", "reasoning", "hookPrompt", "enteredReviewMode", "exitedReviewMode",
                    "subAgentActivity"):
            return
        if kind == "plan":
            self.event("plan", callId=item.get("id"), text=item.get("text", ""),
                       status="completed" if completed else "running")
            return
        if kind == "contextCompaction":
            call_id = item.get("id")
            status = "completed" if completed else "running"
            if not isinstance(call_id, str) or not call_id or len(call_id) > 240:
                return
            if self.context_compactions.get(call_id) in (status, "completed"):
                return
            self.context_compactions[call_id] = status
            self.event("context", callId=call_id, status=status,
                       source="codex", contextReset=True,
                       summary="上下文已整理" if completed else "正在整理上下文")
            return
        identifier = item.get("id")
        native_status = item.get("status")
        status = _codex_tool_status(kind, native_status, completed)
        if kind == "collabAgentToolCall":
            if not isinstance(identifier, str) or not identifier or len(identifier) > 240:
                return
            # Lineage IDs and payloads belong exclusively to the Host registry.
            # Keep the paired tool row with a stable opaque call ID.
            call_id = "codex-collab:" + hashlib.sha256(identifier.encode("utf-8", "surrogatepass")).hexdigest()
            tool = item.get("tool")
            self.tool(call_id, name=tool if isinstance(tool, str) and tool in _COLLAB_TOOLS else kind,
                      status=status)
            return
        duration = item.get("durationMs")
        fields = {"name": item.get("tool") or kind or "tool", "status": status,
                  "parentCallId": item.get("parentCallId") or params.get("parentCallId"),
                  "startedAt": params.get("startedAtMs"), "completedAt": params.get("completedAtMs"),
                  "durationMs": duration if type(duration) is int and 0 <= duration < 2**53 else None}
        if kind == "commandExecution":
            command, cwd, output = item.get("command"), item.get("cwd"), item.get("aggregatedOutput")
            fields.update(name="bash", outputStream="merged", output=output if isinstance(output, str) else None)
            if isinstance(command, str):
                fields["input"] = {"command": command, "cwd": cwd if isinstance(cwd, str) else None}
            if completed and "exitCode" in item:
                exit_code = item["exitCode"]
                if exit_code is None or type(exit_code) is int and -(2**31) <= exit_code < 2**31:
                    fields["exitCode"] = exit_code
                    if status not in ("interrupted", "declined") and exit_code not in (None, 0):
                        fields["status"] = "failed"
        elif kind == "fileChange":
            fields.update(name="apply_patch", status=status,
                          fileChangeApplied=completed and native_status == "completed",
                          input={"changes": _file_changes(item.get("changes"))})
        elif kind in ("mcpToolCall", "dynamicToolCall"):
            fields.update(input=item.get("arguments"),
                          output=item.get("result") if kind == "mcpToolCall" else item.get("contentItems"))
            if (kind == "dynamicToolCall" and item.get("namespace") in (None, "")
                    and item.get("tool") == "easel_business_objects"):
                fields["nativeBusinessTool"] = True
            if (kind == "dynamicToolCall" and item.get("namespace") in (None, "")
                    and item.get("tool") in ("easel_publish", "easel_publication_status")):
                fields["nativePublicationTool"] = True
            error, result = item.get("error"), item.get("result")
            if error:
                fields["error"] = error
            if (status not in ("interrupted", "declined") and
                    (error or isinstance(result, dict) and result.get("isError") is True
                     or item.get("success") is False)):
                fields["status"] = "failed"
        elif kind == "webSearch":
            fields.update(name="web_search", nativeWebSearch=True, input=_web_search_input(item))
            fields.pop("durationMs", None)
            results = item.get("results")
            if "results" in item and (results is None or isinstance(results, list)):
                fields["output"] = results
        elif kind == "imageView":
            fields.update(name="read_image", input={"path": item.get("path")})
        else:
            fields.update(input={key: value for key, value in item.items()
                                 if key not in ("id", "type", "status", "result", "output", "parentCallId")},
                          output=item.get("result", item.get("output")))
        self.tool(identifier, **fields)

    def acp(self, update: dict):
        kind = update.get("sessionUpdate")
        if kind == "usage_update":
            used = _token_count(update.get("used"))
            capacity = _token_count(update.get("size"), capacity=True)
            self.event("usage", capacity=capacity, contextUsage={
                "source": "acp", "usedTokens": used, "capacityTokens": capacity})
            return
        if kind == "plan":
            self.event("plan", source="acp", steps=self._plan_steps(update.get("entries"), "content", "status", "priority"))
            return
        if kind not in ("tool_call", "tool_call_update"):
            return
        identifier = update.get("toolCallId")
        prior = self.calls.get(identifier, {})
        parent = update.get("parentCallId")
        if not _valid_tool_parent(parent, identifier):
            parent = None
            if "parentCallId" not in prior:
                # claude-agent-acp's stampParentToolUseId reports an explicit
                # provider edge in this namespace. ACP v1 itself has no edge.
                metadata = update.get("_meta")
                claude = metadata.get("claudeCode") if isinstance(metadata, dict) else None
                provider_parent = claude.get("parentToolUseId") if isinstance(claude, dict) else None
                if _valid_tool_parent(provider_parent, identifier):
                    parent = provider_parent
        native_status = update.get("status")
        status = prior.get("status", "preparing" if native_status is None else "unknown")
        if isinstance(native_status, str):
            status = _ACP_TOOL_STATUSES.get(native_status, status)
        name, title, tool_kind = (update.get(field) for field in ("name", "title", "kind"))
        name = name if isinstance(name, str) and name else None
        title = title if isinstance(title, str) else None
        tool_kind = tool_kind if isinstance(tool_kind, str) else None
        content = update.get("content")
        content = content if isinstance(content, list) else None
        output = update.get("rawOutput")
        output_source = "raw" if output is not None else None
        if output is None and content is not None and prior.get("outputSource") != "raw":
            output, output_source = content, "content"
        self.tool(identifier, name=name or prior.get("name") or tool_kind or title or "tool",
                  title=title, toolKind=tool_kind, status=status, input=update.get("rawInput"),
                  output=output, outputSource=output_source, content=content,
                  locations=update.get("locations"), parentCallId=parent)
