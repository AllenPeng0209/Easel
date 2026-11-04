"""Revoking host-tool privileges must terminate actual native RPC processes."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from easel.access import AccessStore
from easel.harnesses import rpc
from easel.harnesses import store as harness_store
from easel.harnesses.acp import ACPAdapter
from easel.harnesses.codex import CodexAdapter
from easel.projects import project_context
from easel.request_identity import ActorMap, ActorSet, actor_context
from web import app as web


pytestmark = pytest.mark.access_integration
FIXTURE = Path(__file__).parent / "fixtures" / "harness_rpc.py"


@pytest.mark.parametrize("runtime,method", [("codex", "turn/start"), ("acp", "session/prompt")])
def test_revoking_admin_stops_running_native_process_and_releases_session(tmp_path, monkeypatch, runtime, method):
    """A previously authorized child cannot retain host tools after demotion."""
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(web, "SESSIONS_DIR", tmp_path / "outputs" / "_sessions")
    monkeypatch.setattr(web, "OPENCLAW_SESSIONS_DIR", tmp_path / "openclaw-sessions")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr(web, "ENV_FILE", env)
    monkeypatch.delenv("EASEL_AGENT_RUNTIME", raising=False)
    monkeypatch.delenv("EASEL_CHAT_TRANSPORT", raising=False)
    monkeypatch.setattr(web, "_RUNNING_CHAT", ActorMap())
    monkeypatch.setattr(web, "_session_locks", ActorMap())
    monkeypatch.setattr(web, "_HARNESS_QUESTIONS", ActorMap())
    monkeypatch.setattr(web, "_STOPPED_CHAT", ActorSet())
    monkeypatch.setattr(web, "_HARNESS_TASKS", set())
    audit = tmp_path / "native-audit.jsonl"
    monkeypatch.setenv("FAKE_HARNESS_MODE", "hang")
    monkeypatch.setenv("FAKE_HARNESS_AUDIT", str(audit))
    if runtime == "codex":
        import easel.harnesses.codex as adapter_module
        monkeypatch.setattr(adapter_module, "base_command", lambda _: [sys.executable, str(FIXTURE), "codex"])
        adapter = CodexAdapter()
    else:
        import easel.harnesses.acp as adapter_module
        monkeypatch.setattr(adapter_module, "acp_command", lambda _: [sys.executable, str(FIXTURE), "acp"])
        adapter = ACPAdapter()
    # The real adapters and RPC subprocess are exercised; only executable discovery
    # selects the deterministic fixture instead of a paid/provider-backed CLI.
    monkeypatch.setattr(web.harnesses, "adapter", lambda _: adapter)
    config = {"runtime": runtime, "models": {}, "sandbox": "read-only", "codex_auto_approve": False}
    harness_store.write_json(web.OUTPUTS_DIR / "_harness" / "config.json", config)
    access = AccessStore(tmp_path)
    original, _ = access.setup("original-admin", "Original admin test password", project_ids=["default"])
    replacement = access.create_user("replacement-admin", "Replacement admin test password",
                                     is_admin=True, actor_id=original["id"])

    async def scenario():
        session = "revoked-native-" + runtime
        events = []
        task = None
        with project_context("default", tmp_path), actor_context(original):
            snapshot = web._turn_file("web:" + session)
            native_key = web._native_session_key(session)
            try:
                task = asyncio.create_task(web._run_harness_chat(
                    web.ChatRequest(message="wait for permission change", sessionId=session),
                    session, "wait for permission change", runtime, config,
                    lambda kind, text: events.append((kind, text)), timeout=20))

                async def wait_for_child():
                    while True:
                        if audit.exists() and any(json.loads(line).get("method") == method
                                                  for line in audit.read_text(encoding="utf-8").splitlines()):
                            pool = rpc._POOLS.get(asyncio.get_running_loop(), {})
                            entry = next((entry for key, entry in pool.items() if key[1] == native_key), None)
                            if entry is not None:
                                return entry["rpc"].process
                        if task.done():
                            raise AssertionError(f"Native task exited before revocation: {events}")
                        await asyncio.sleep(0.01)

                process = await asyncio.wait_for(wait_for_child(), timeout=2)
                assert process.returncode is None
                access.update_user(original["id"], is_admin=False, actor_id=replacement["id"])

                completed, _ = await asyncio.wait({task}, timeout=3)
                assert task in completed, "Native task retained host privileges after its administrator was demoted"
                await task
                assert process.returncode is not None, "Revoked native subprocess is still running"
                assert not any(key[1] == native_key for key in rpc._POOLS.get(asyncio.get_running_loop(), {}))
                assert web._RUNNING_CHAT.get(session) is None
                assert task not in web._HARNESS_TASKS
                assert not web._session_lock(session).locked()
                probe = web._CrossProcLock(session)
                try:
                    assert probe.acquire(timeout=0), "Revocation left the cross-process session lock held"
                finally:
                    probe.release()
                result = json.loads(snapshot.read_text(encoding="utf-8"))
                assert result["stop_reason"] == "permission_revoked"
                assert result["clean_end"] is False
            finally:
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=4)
                await rpc.close_sessions(native_key)

    asyncio.run(asyncio.wait_for(scenario(), timeout=8))
