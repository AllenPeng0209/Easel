"""Read native model catalogs without starting a conversation."""
from __future__ import annotations

import asyncio
import os
import signal

from easel import __version__

from .base import HarnessError, RunRequest
from .commands import base_command, codex_override
from .catalog_cache import catalog_ttl, protected_catalog
from .rpc import RPC


def valid_model_id(value):
    return isinstance(value, str) and 0 < len(value) <= 240 and value == value.strip() and not any(
        ord(character) < 32 or 127 <= ord(character) <= 159 or 0xD800 <= ord(character) <= 0xDFFF
        for character in value)


async def _codex_models(request: RunRequest, *, include_config: bool = False) -> tuple[list[dict], str | None]:
    command = (codex_override(request.env) or base_command("codex")) + ["app-server"]
    ttl = catalog_ttl(request.env)
    if not ttl:
        return await _probe_codex_models(request, command, include_config=include_config)
    # Do not retain a caller's large prompt or Host callbacks in the probe queue.
    metadata_request = RunRequest('', request.cwd, dict(request.env), sandbox='read-only')
    return await protected_catalog(metadata_request, command, include_config,
        lambda: _probe_codex_models(metadata_request, command, include_config=include_config, bounded=True), ttl)


async def _probe_codex_models(request: RunRequest, command: list[str], *, include_config: bool, bounded=False):
    async def reject(method, params):
        raise HarnessError("读取模型列表时不允许执行工具")

    models = []
    configured = None
    cursor = None
    async with RPC(request, command,
                   lambda method, params: None, reject) as rpc:
        await rpc.call("initialize", {
            "clientInfo": {"name": "easel", "version": __version__},
            "capabilities": {"experimentalApi": True}}, timeout=15)
        await rpc.send({"method": "initialized"})
        if include_config:
            response = await rpc.call("config/read", {"cwd": str(request.cwd), "includeLayers": False}, timeout=10)
            config = response.get("config") if isinstance(response, dict) else None
            if not isinstance(config, dict):
                raise HarnessError("Codex 返回了无效的有效模型配置")
            configured = config.get("model")
            if configured is not None and not valid_model_id(configured):
                raise HarnessError("Codex 有效模型配置无效")
        for page in range(20):
            params = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            response = await rpc.call("model/list", params, timeout=15)
            if not isinstance(response, dict) or not isinstance(response.get("data"), list):
                raise HarnessError("Codex 返回了无效的模型列表")
            if bounded and len(response['data']) > 100:
                raise HarnessError("模型目录超过请求分页大小，请检查原生配置")
            for item in response.get("data") or []:
                if not isinstance(item, dict):
                    continue
                identifier = item.get("model") or item.get("id")
                if not item.get("hidden") and valid_model_id(identifier):
                    label = item.get("displayName")
                    models.append({"id": identifier,
                                   "label": label if valid_model_id(label) else identifier,
                                   "default": bool(item.get("isDefault"))})
            following = response.get("nextCursor")
            if bounded and following and (following == cursor or page == 19):
                raise HarnessError("模型目录分页未完整结束，请检查原生配置")
            if not following or following == cursor:
                break
            cursor = following
    return models, configured


async def codex_model_profile(request: RunRequest) -> tuple[list[dict], str | None]:
    """Read only the effective model ID and visible catalog from Codex."""
    models, configured = await _codex_models(request, include_config=True)
    return _unique_models(models), configured


async def available_models(runtime: str, request: RunRequest) -> list[dict]:
    if runtime == "codex":
        models, _ = await _codex_models(request)
    elif runtime == "opencode":
        env = {**request.env, "EASEL_ROOT": str(request.cwd.resolve()),
               "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        process = await asyncio.create_subprocess_exec(
            *base_command("opencode"), "models",
            cwd=str(request.cwd), env=env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=os.name != "nt")
        try:
            async def read_output():
                chunks = []
                size = 0
                while chunk := await process.stdout.read(65536):
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise HarnessError("OpenCode 模型列表过大")
                    chunks.append(chunk)
                return b"".join(chunks)

            output = await asyncio.wait_for(read_output(), 15)
            await asyncio.wait_for(process.wait(), 5)
            if process.returncode or len(output) > 1024 * 1024:
                raise HarnessError("OpenCode 模型列表读取失败，请检查原生 CLI 配置")
        finally:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.returncode is None:
                process.kill()
            await process.wait()
        models = [{"id": identifier, "label": identifier, "default": False}
                  for identifier in output.decode("utf-8").splitlines()
                  if valid_model_id(identifier) and "/" in identifier and not any(
                      character.isspace() for character in identifier)]
    else:
        raise HarnessError("此底座不支持原生模型列表")
    return _unique_models(models)


def _unique_models(models):
    unique = {}
    for item in models:
        unique.setdefault(item["id"], item)
    return list(unique.values())
