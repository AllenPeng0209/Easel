"""Classify upstream Codex failures without exposing any upstream payload text."""
from __future__ import annotations

import json
import re

from .base import HarnessError


_GENERIC = "Codex 执行失败，请检查原生登录与模型配置"


_MESSAGES = {
    "authentication": "登录已失效或账号没有访问权限，请检查登录和凭据配置。",
    "model_unavailable": "当前账号无法使用所选模型，请检查模型配置。",
    "rate_limit": "请求频率已达限制。",
    "quota_exhausted": "账号用量已达限制，请检查剩余额度。",
    "network": "模型服务连接中断，请检查网络或本机网关。",
    "timeout": "本轮请求超时。",
    "permission_denied": "当前操作缺少所需权限。",
    "session_conflict": "当前会话正在运行或属于其他底座，请核对原任务或新建会话。",
    "storage": "会话历史保存失败，请检查本地存储权限。",
    "unknown": "本轮执行状态未确认，请核对已有产物和发布回执。",
}
_PHASES = ("startup", "execution", "finalization")
_TRANSIENT = ("rate_limit", "network", "timeout")


def chat_failure(code: str = "unknown", phase: str = "execution", *, safe_to_retry: bool = False) -> dict:
    """Construct the public allowlisted error. Messages never come from an engine."""
    if not isinstance(code, str) or code not in _MESSAGES:
        code = "unknown"
    phase = phase if phase in _PHASES else "execution"
    return {"code": code, "message": _MESSAGES[code], "phase": phase,
            "retryable": safe_to_retry is True and phase == "startup" and code in _TRANSIENT}


def normalize_chat_failure(value: object) -> dict:
    if not isinstance(value, dict):
        return chat_failure()
    return chat_failure(value.get("code"), value.get("phase"),
                        safe_to_retry=value.get("retryable") is True)


class ChatFailure(HarnessError):
    def __init__(self, code: str, phase: str = "execution", *, diagnostic: str | None = None):
        self.payload = chat_failure(code, phase)
        super().__init__(diagnostic or self.payload["message"])


def classify_provider_failure(error: object) -> tuple[str, str]:
    """Return fixed guidance for structured or JSON-encoded Codex errors.

    ChatGPT credentials live outside the subprocess environment, so replacing
    environment values cannot safely redact an arbitrary upstream error message.
    The payload is used only to classify a bounded set of failure signals; none
    of its model IDs, URLs, account data or free-form text is returned to the UI.
    """
    pending = [(error, 0)]
    signals: list[str] = []
    statuses: set[int] = set()
    visited = 0
    while pending and visited < 96:
        value, depth = pending.pop()
        visited += 1
        if depth > 6:
            continue
        if isinstance(value, dict):
            for index, (key, child) in enumerate(value.items()):
                if index >= 32:
                    break
                if isinstance(key, str):
                    signals.append(re.sub(r"[^a-z0-9]", "", key[:256].casefold()))
                    if (key in {"status", "statusCode", "status_code", "httpStatusCode", "http_status_code"}
                            and type(child) is int and 100 <= child <= 599):
                        statuses.add(child)
                pending.append((child, depth + 1))
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value[:32])
        elif isinstance(value, str):
            text = value[:16_384]
            signals.append(re.sub(r"[^a-z0-9]", "", text.casefold()))
            if text.lstrip().startswith(("{", "[")):
                try:
                    nested = json.loads(text)
                except (ValueError, RecursionError):
                    continue
                pending.append((nested, depth + 1))

    def matches(*markers: str) -> bool:
        return any(marker in signal for marker in markers for signal in signals)

    if matches("modelnotfound", "unsupportedmodel", "modelisnotsupported", "modelisnotavailable"):
        return "model_unavailable", ("Codex 执行失败：当前 Codex 版本或账号无法使用所选模型。请先检查 Codex CLI 是否需要更新，"
                "或在 响芽「设置 → Agent 底座 → 模型覆盖」选择当前账号可用的模型。")
    if statuses & {401, 403} or matches(
            "authenticationfailed", "unauthorized", "invalidapikey", "invalidtoken", "tokenexpired", "tokenhasexpired"):
        return "authentication", "Codex 执行失败：登录已失效或当前账号没有访问权限，请在本机运行 codex login 重新登录并确认账号权限。"
    if 402 in statuses or matches("usagelimitexceeded", "quotaexceeded", "insufficientquota", "usageexhausted"):
        return "quota_exhausted", "Codex 执行失败：当前账号的用量已达限制，请检查 ChatGPT 的 Codex 用量。"
    if 429 in statuses or matches("ratelimitexceeded"):
        return "rate_limit", "Codex 执行失败：当前账号的用量或请求频率已达限制，请检查 ChatGPT 的 Codex 用量。"
    if 408 in statuses or matches("timedout", "timeout"):
        return "timeout", "Codex 执行失败：模型服务请求超时，请检查本机网络与代理。"
    if any(status >= 500 for status in statuses) or matches(
            "httpconnectionfailed", "httpsendfailed", "responsestreamconnectionfailed", "responsestreamdisconnected",
            "connectionfailed", "networkerror", "networkconnectionfailed", "dnserror", "websocket"):
        return "network", "Codex 执行失败：无法连接模型服务，请检查本机网络、代理与 Codex 连接。"
    return "unknown", _GENERIC


def codex_failure(error: object, phase: str = "execution") -> ChatFailure:
    code, diagnostic = classify_provider_failure(error)
    return ChatFailure(code, phase, diagnostic=diagnostic)
