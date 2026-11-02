"""Protocol-level tests: the real adapters must preserve tool facts for the UI."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from easel.harnesses.acp import ACPAdapter
from easel.harnesses.base import RunRequest
from easel.harnesses.codex import CodexAdapter


def run_wire(tmp_path: Path, runtime: str, notifications: list[dict], *, all_events=False):
    """A local JSON-RPC peer supplies native notifications, without a model/network."""
    script = tmp_path / "structured_wire.py"
    script.write_text('''import json,sys
events=json.loads(sys.argv[1])
def send(value):
 print(json.dumps(value),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get("method")
 if method=="initialize":
  send({"id":m["id"],"result":{"protocolVersion":1,"agentCapabilities":{}}})
 elif method=="thread/start":
  send({"id":m["id"],"result":{"thread":{"id":"thread-1"}}})
 elif method=="session/new":
  send({"id":m["id"],"result":{"sessionId":"session-1"}})
 elif method in ("turn/start","session/prompt"):
  for e in events: send(e)
  if method=="turn/start":
   send({"method":"turn/completed","params":{"threadId":"thread-1","turnId":"turn-1","turn":{"id":"turn-1","status":"completed"}}})
   send({"id":m["id"],"result":{"turn":{"id":"turn-1"}}})
  else: send({"id":m["id"],"result":{"stopReason":"end_turn"}})
''', encoding="utf-8")
    request = RunRequest(
        "hello", tmp_path, {**os.environ, "OPENAI_API_KEY": "sk-native-private-1234567890"},
        command=(sys.executable, str(script), json.dumps(notifications)), timeout=10,
    )
    events = []

    async def reject(questions):
        raise AssertionError("No permission requests expected")

    asyncio.run((CodexAdapter() if runtime == "codex" else ACPAdapter()).run(
        request, lambda *event: events.append(event), reject, lambda native_id: None))
    return events if all_events else [payload for kind, payload in events if kind == "agent_event"]


def codex(method, **params):
    return {"method": method, "params": {"threadId": "thread-1", "turnId": "turn-1", **params}}


def acp(**update):
    return {"method": "session/update", "params": {"sessionId": "session-1", "update": update}}


def test_codex_command_keeps_one_call_with_incremental_output_and_actual_exit(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/started", startedAtMs=1000, item={"id": "cmd-1", "type": "commandExecution",
              "command": "python render.py", "cwd": str(tmp_path), "status": "inProgress"}),
        codex("item/commandExecution/outputDelta", itemId="cmd-1", delta="frame 1\n"),
        codex("item/commandExecution/outputDelta", itemId="cmd-1", delta="codec unavailable\n"),
        codex("item/completed", completedAtMs=2500, item={"id": "cmd-1", "type": "commandExecution",
              "command": "python render.py", "cwd": str(tmp_path), "status": "completed",
              "aggregatedOutput": "frame 1\ncodec unavailable\n", "exitCode": 7, "durationMs": 1500}),
    ])
    calls = [event for event in events if event.get("kind") == "tool"]
    assert len(calls) == 4
    assert {call["callId"] for call in calls} == {"cmd-1"}
    assert len({call["id"] for call in calls}) == 4
    assert calls[0]["name"] == "bash"
    assert calls[0]["input"]["command"] == "python render.py"
    assert calls[1]["output"] == "frame 1\n"
    assert calls[2]["output"] == "frame 1\ncodec unavailable\n"
    assert calls[-1]["status"] == "failed"
    assert calls[-1]["exitCode"] == 7
    assert calls[-1]["startedAt"] == 1000
    assert calls[-1]["completedAt"] == 2500


def test_codex_preserves_file_diff_and_mcp_parent_but_filters_foreign_thread(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "patch-1", "type": "fileChange", "status": "completed",
              "changes": [{"path": "caption.srt", "kind": {"type": "update"}, "diff": "-old\n+new"}]}),
        codex("item/completed", item={"id": "child-1", "type": "mcpToolCall", "server": "media",
              "tool": "probe", "arguments": {"path": "clip.mp4"}, "parentCallId": "patch-1",
              "status": "completed", "result": {"content": [{"type": "text", "text": "duration: 30"}]}}),
        {"method": "item/completed", "params": {"threadId": "other-thread", "turnId": "turn-1",
            "item": {"id": "foreign", "type": "commandExecution", "command": "private", "status": "completed"}}},
    ])
    tools = [event for event in events if event.get("kind") == "tool"]
    assert [event["callId"] for event in tools] == ["patch-1", "child-1"]
    assert tools[0]["name"] == "apply_patch"
    assert tools[0]["input"]["changes"][0]["diff"] == "-old\n+new"
    assert tools[1]["name"] == "probe"
    assert tools[1]["parentCallId"] == "patch-1"
    assert tools[1]["input"] == {"path": "clip.mp4"}
    assert tools[1]["output"]["content"][0]["text"] == "duration: 30"


def test_codex_plan_usage_and_compaction_are_not_misreported_as_tools(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("turn/plan/updated", explanation="先验证输入", plan=[{"step": "检查素材", "status": "in_progress"}]),
        codex("thread/tokenUsage/updated", tokenUsage={"total": {"inputTokens": 20, "outputTokens": 4},
              "last": {"inputTokens": 10, "outputTokens": 4}, "modelContextWindow": 1000}),
        codex("item/completed", item={"id": "compact-1", "type": "contextCompaction"}),
    ])
    assert [event["kind"] for event in events] == ["plan", "usage", "context"]
    assert events[0]["steps"] == [{"step": "检查素材", "status": "in_progress"}]
    assert events[1]["usage"]["total"]["inputTokens"] == 20
    assert events[1]["capacity"] == 1000
    assert events[2]["status"] == "completed"


def test_acp_partial_updates_keep_tool_input_title_and_result(tmp_path):
    events = run_wire(tmp_path, "acp", [
        acp(sessionUpdate="tool_call", toolCallId="read-1", title="读取素材", kind="read", status="pending",
            rawInput={"path": "notes.md"}, locations=[{"path": "notes.md", "line": 2}]),
        acp(sessionUpdate="tool_call_update", toolCallId="read-1", status="in_progress"),
        acp(sessionUpdate="tool_call_update", toolCallId="read-1", status="completed",
            content=[{"type": "content", "content": {"type": "text", "text": "source facts"}}]),
    ])
    tools = [event for event in events if event.get("kind") == "tool"]
    assert len(tools) == 3
    assert [event["status"] for event in tools] == ["preparing", "running", "completed"]
    assert all(event["callId"] == "read-1" and event["name"] == "read" for event in tools)
    assert tools[-1]["input"] == {"path": "notes.md"}
    assert tools[-1]["title"] == "读取素材"
    assert tools[-1]["output"][0]["content"]["text"] == "source facts"


def test_tools_redact_env_secrets_and_sensitive_fields_before_emit(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "secret-1", "type": "mcpToolCall", "server": "test", "tool": "lookup",
              "arguments": {"api_key": "unlisted-private-value", "nested": {"password": "private-password"},
                            "query": "public"}, "status": "completed",
              "result": {"token": "unlisted-token", "content": [{"type": "text", "text":
                    "OPENAI_API_KEY=sk-native-private-1234567890 Authorization: Bearer unlisted-bearer-value"}]}}),
    ])
    assert events
    rendered = json.dumps(events)
    for secret in ("unlisted-private-value", "private-password", "unlisted-token", "sk-native-private-1234567890", "unlisted-bearer-value"):
        assert secret not in rendered
    assert events[0]["input"]["query"] == "public"
    assert events[0]["input"]["api_key"] == "[REDACTED]"


def test_tool_output_is_bounded_and_explicitly_marks_truncation(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "long-1", "type": "commandExecution", "command": "build",
              "status": "completed", "exitCode": 0, "aggregatedOutput": "x" * 100000}),
    ])
    assert events
    assert events[0]["truncated"] is True
    assert len(events[0]["output"]) < 40000
    assert "truncated" in events[0]["output"].lower()


def test_interrupted_command_is_not_relabelled_failed_by_its_signal_exit_code(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "cancel-1", "type": "commandExecution", "command": "render",
              "status": "interrupted", "exitCode": 143, "aggregatedOutput": "cancelled"}),
    ])
    assert events[0]["status"] == "interrupted"
    assert events[0]["exitCode"] == 143


def test_mcp_error_result_cannot_be_displayed_as_completed(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "mcp-1", "type": "mcpToolCall", "server": "media", "tool": "render",
              "arguments": {}, "status": "completed", "result": {"isError": True,
              "content": [{"type": "text", "text": "missing encoder"}]}}),
    ])
    assert events[0]["status"] == "failed"
    assert events[0]["output"]["content"][0]["text"] == "missing encoder"


def test_unknown_tool_remains_inspectable_and_opaque_secrets_are_redacted(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "future-1", "type": "futureTool", "status": "completed",
              "details": {"path": "public.txt", "accessToken": "private-camel-token"},
              "result": {"newShape": {"count": 3}}}),
    ])
    assert events[0]["name"] == "futureTool"
    assert events[0]["input"]["details"]["path"] == "public.txt"
    assert "private-camel-token" not in json.dumps(events)
    assert events[0]["output"] == {"newShape": {"count": 3}}


def test_acp_plan_and_foreign_session_are_separated_from_current_tools(tmp_path):
    events = run_wire(tmp_path, "acp", [
        acp(sessionUpdate="plan", entries=[{"content": "collect source", "priority": "high", "status": "pending"}]),
        {"method": "session/update", "params": {"sessionId": "foreign", "update": {
            "sessionUpdate": "tool_call", "toolCallId": "private", "title": "private", "status": "completed"}}},
    ])
    assert len(events) == 1
    assert events[0]["kind"] == "plan"
    assert events[0]["steps"] == [{"content": "collect source", "priority": "high", "status": "pending"}]


def test_legacy_activity_does_not_bypass_tool_redaction(tmp_path):
    events = run_wire(tmp_path, "acp", [
        acp(sessionUpdate="tool_call", toolCallId="read-1", title="lookup api_key=private-activity-key",
            kind="read", status="pending", rawInput={"path": "public.txt"}),
    ], all_events=True)
    assert "private-activity-key" not in json.dumps(events)
    assert any(kind == "activity" and "lookup" in value for kind, value in events)


def test_private_key_blocks_and_unlabelled_jwt_do_not_enter_tool_history(tmp_path):
    private = "-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic-private-key-material\n-----END OPENSSH PRIVATE KEY-----"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0LXVzZXIifQ.syntheticSignature0123456789"
    events = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "secret-file", "type": "commandExecution", "command": "cat config",
              "status": "completed", "exitCode": 0, "aggregatedOutput": "public before\n" + private + "\n" + jwt + "\npublic after"}),
    ])
    text = events[0]["output"]
    assert "synthetic-private-key-material" not in text
    assert jwt not in text
    assert "public before" in text and "public after" in text


def test_private_key_start_is_redacted_before_its_end_arrives(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/commandExecution/outputDelta", itemId="secret-file",
              delta="-----BEGIN RSA PRIVATE KEY-----\nfirst-private-chunk"),
    ])
    assert events and "first-private-chunk" not in events[0]["output"]


def test_private_key_continuation_chunks_remain_redacted_until_the_end_marker(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/commandExecution/outputDelta", itemId="secret-file",
              delta="-----BEGIN RSA PRIVATE KEY-----\nfirst-private-chunk"),
        codex("item/commandExecution/outputDelta", itemId="secret-file", delta="second-private-chunk"),
        codex("item/commandExecution/outputDelta", itemId="secret-file",
              delta="\n-----END RSA PRIVATE KEY-----\npublic tail"),
    ])
    assert len(events) == 3
    rendered = json.dumps(events)
    assert "first-private-chunk" not in rendered
    assert "second-private-chunk" not in rendered
    assert "public tail" in events[-1]["output"]
