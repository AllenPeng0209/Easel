"""Classify upstream Codex failures without exposing any upstream payload text."""
from __future__ import annotations

import json
import re


_GENERIC = "Codex 执行失败，请检查原生登录与模型配置"


def codex_failure_message(error: object) -> str:
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
        return ("Codex 执行失败：当前 Codex 版本或账号无法使用所选模型。请先检查 Codex CLI 是否需要更新，"
                "或在 Easel「设置 → Agent 底座 → 模型覆盖」选择当前账号可用的模型。")
    if statuses & {401, 403} or matches(
            "authenticationfailed", "unauthorized", "invalidapikey", "invalidtoken", "tokenexpired", "tokenhasexpired"):
        return "Codex 执行失败：登录已失效或当前账号没有访问权限，请在本机运行 codex login 重新登录并确认账号权限。"
    if 429 in statuses or matches("ratelimitexceeded", "usagelimitexceeded", "quotaexceeded", "insufficientquota", "usageexhausted"):
        return "Codex 执行失败：当前账号的用量或请求频率已达限制，请稍后重试，并检查 ChatGPT 的 Codex 用量。"
    if 408 in statuses or any(status >= 500 for status in statuses) or matches(
            "httpconnectionfailed", "httpsendfailed", "responsestreamconnectionfailed", "responsestreamdisconnected",
            "connectionfailed", "networkerror", "networkconnectionfailed", "timedout", "timeout", "dnserror", "websocket"):
        return "Codex 执行失败：无法连接模型服务，请检查本机网络、代理与 Codex 连接后重试。"
    return _GENERIC
