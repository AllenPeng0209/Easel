"""Protocol-level tests: the real adapters must preserve tool facts for the UI."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

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
    assert all(call["outputStream"] == "merged" for call in calls)
    assert "exitCode" not in calls[0]
    assert calls[1]["output"] == "frame 1\n"
    assert calls[2]["output"] == "frame 1\ncodec unavailable\n"
    assert calls[-1]["status"] == "failed"
    assert calls[-1]["exitCode"] == 7
    assert calls[-1]["startedAt"] == 1000
    assert calls[-1]["completedAt"] == 2500
    assert calls[-1]["durationMs"] == 1500


def test_codex_command_preserves_explicit_null_exit_only_on_terminal_record(tmp_path):
    command = {"id": "cmd-null", "type": "commandExecution", "command": "render", "exitCode": None}
    started, completed = run_wire(tmp_path, "codex", [
        codex("item/started", item={**command, "status": "inProgress"}),
        codex("item/completed", item={**command, "status": "completed", "durationMs": 0}),
    ])
    assert "exitCode" not in started
    assert "exitCode" in completed and completed["exitCode"] is None
    assert completed["durationMs"] == 0


@pytest.mark.parametrize("invalid_duration", [True, -1, "42", 1.5, 2**53])
def test_codex_command_ignores_invalid_timing_and_exit_metadata(tmp_path, invalid_duration):
    [event] = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "cmd-invalid", "type": "commandExecution",
              "command": "render", "status": "completed", "exitCode": "7",
              "durationMs": invalid_duration, "aggregatedOutput": ""}),
    ])
    assert event["status"] == "completed"
    assert event["output"] == ""
    assert "exitCode" not in event
    assert "durationMs" not in event


@pytest.mark.parametrize("kind,native_status", [
    ("commandExecution", None), ("commandExecution", "inProgress"), ("mcpToolCall", "success"),
    ("dynamicToolCall", None), ("collabAgentToolCall", "inProgress"), ("imageGeneration", "future"),
])
def test_status_bearing_codex_terminal_without_explicit_result_stays_unconfirmed(tmp_path, kind, native_status):
    [event] = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "tool", "type": kind, "status": native_status}),
    ])
    assert event["status"] == "unconfirmed"


@pytest.mark.parametrize("kind", ["webSearch", "imageView", "sleep", "functionCallOutput"])
def test_known_statusless_codex_tools_complete_on_native_terminal_frame(tmp_path, kind):
    started, finished = run_wire(tmp_path, "codex", [
        codex("item/started", item={"id": "tool", "type": kind}),
        codex("item/completed", item={"id": "tool", "type": kind}),
    ])
    assert started["status"] == "running" and finished["status"] == "completed"


def test_codex_decline_interruption_and_failure_do_not_get_overwritten_by_late_progress(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "cmd", "type": "commandExecution", "status": "declined",
                                      "exitCode": 1, "aggregatedOutput": "known"}),
        codex("item/started", item={"id": "cmd", "type": "commandExecution", "status": "inProgress"}),
        codex("item/commandExecution/outputDelta", itemId="cmd", delta="stale duplicate"),
        codex("item/completed", item={"id": "cmd", "type": "commandExecution", "status": "future",
                                      "aggregatedOutput": "unconfirmed duplicate"}),
        codex("item/completed", item={"id": "dynamic", "type": "dynamicToolCall",
                                      "status": "interrupted", "success": False}),
        codex("item/completed", item={"id": "mcp", "type": "mcpToolCall", "status": "failed"}),
        codex("item/mcpToolCall/progress", itemId="mcp", message="stale progress"),
    ])
    assert [event["status"] for event in events] == ["declined", "interrupted", "failed"]
    assert events[0]["output"] == "known" and events[0]["exitCode"] == 1


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


def test_codex_file_change_keeps_native_patch_and_marks_only_applied_terminal(tmp_path):
    patch = "@@ -1 +1 @@\n-old\n+new\n"
    change = {"path": "caption.srt", "kind": {"type": "update", "move_path": "new-caption.srt"},
              "diff": patch, "artifactId": "unverified"}
    call = {"id": "edit", "type": "fileChange", "changes": [change]}
    started, applied = run_wire(tmp_path, "codex", [
        codex("item/started", item={**call, "status": "inProgress"}),
        codex("item/completed", item={**call, "status": "completed"}),
    ])
    assert started["fileChangeApplied"] is False and started["status"] == "running"
    assert applied["fileChangeApplied"] is True and applied["status"] == "completed"
    assert applied["input"]["changes"] == [{"path": "caption.srt", "diff": patch,
        "kind": {"type": "update", "move_path": "new-caption.srt"}}]
    assert "artifactId" not in json.dumps(applied)


@pytest.mark.parametrize("native_status,expected", [
    (None, "unconfirmed"), ("success", "unconfirmed"), ("inProgress", "unconfirmed"),
    ("failed", "failed"), ("declined", "declined"),
])
def test_file_change_without_native_completion_never_claims_applied(tmp_path, native_status, expected):
    [event] = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "edit", "type": "fileChange", "status": native_status,
              "changes": [{"path": "file.txt", "diff": "-attempt\n", "kind": {"type": "delete"}}]}),
    ])
    assert event["fileChangeApplied"] is False
    assert event["status"] == expected
    assert event["input"]["changes"][0]["diff"] == "-attempt\n"


def test_applied_terminal_without_diff_does_not_backfill_attempted_changes(tmp_path):
    [started, ended] = run_wire(tmp_path, "codex", [
        codex("item/started", item={"id": "edit", "type": "fileChange", "status": "inProgress",
              "changes": [{"path": "file.txt", "diff": "+proposed\n", "kind": {"type": "add"}}]}),
        codex("item/completed", item={"id": "edit", "type": "fileChange", "status": "completed"}),
    ])
    assert started["input"]["changes"][0]["diff"] == "+proposed\n"
    assert ended["fileChangeApplied"] is True
    assert ended["input"]["changes"] == []


def test_file_change_normalizes_only_valid_observed_metadata(tmp_path):
    [event] = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "edit", "type": "fileChange", "status": "completed",
              "changes": [
                  {"path": "a.txt", "diff": "", "kind": {"type": "update", "move_path": None}},
                  {"path": "b.txt", "diff": {"unsupported": True}, "kind": {"type": "update", "move_path": "\nwrong"}},
                  {"path": "c.txt", "diff": "+actual\n", "kind": {"type": "unknown"}},
                  {"path": "d.txt", "kind": {"type": "add", "move_path": "wrong.txt"}},
                  {"path": "", "diff": "+unattributed"},
              ]}),
    ])
    assert event["input"]["changes"] == [
        {"path": "a.txt", "diff": "", "kind": {"type": "update", "move_path": None}},
        {"path": "b.txt", "kind": {"type": "update"}},
        {"path": "c.txt", "diff": "+actual\n"},
        {"path": "d.txt", "kind": {"type": "add"}},
    ]


def test_codex_file_change_progress_and_stale_start_preserve_terminal_evidence(tmp_path):
    native = {"id": "edit", "type": "fileChange", "changes": [{"path": "file.txt", "kind": {"type": "add"},
                                                               "diff": "recorded"}]}
    events = run_wire(tmp_path, "codex", [
        codex("item/fileChange/outputDelta", itemId="edit", delta="working"),
        codex("item/completed", item={**native, "status": "completed"}),
        codex("item/started", item={**native, "status": "inProgress", "changes": []}),
        codex("item/fileChange/outputDelta", itemId="edit", delta="done"),
    ])
    assert len(events) == 2
    assert events[0]["fileChangeApplied"] is False
    assert events[-1]["fileChangeApplied"] is True and events[-1]["status"] == "completed"
    assert events[-1]["input"]["changes"][0]["diff"] == "recorded"


def test_codex_plan_usage_and_compaction_are_not_misreported_as_tools(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("turn/plan/updated", explanation="先验证输入", plan=[{"step": "检查素材", "status": "inProgress", "assignee": "未验证身份"}]),
        codex("thread/tokenUsage/updated", tokenUsage={"total": {"inputTokens": 20, "outputTokens": 4},
              "last": {"inputTokens": 10, "outputTokens": 4}, "modelContextWindow": 1000}),
        codex("item/completed", item={"id": "compact-1", "type": "contextCompaction"}),
    ])
    assert [event["kind"] for event in events] == ["plan", "usage", "context"]
    assert events[0]["steps"] == [{"step": "检查素材", "status": "inProgress"}]
    assert events[0]["turnId"] == "turn-1" and events[0]["source"] == "codex"
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


def test_acp_uses_exact_status_and_keeps_programmatic_name_and_raw_context(tmp_path):
    content = [{"type": "content", "content": {"type": "text", "text": "display only"}}]
    events = run_wire(tmp_path, "acp", [
        acp(sessionUpdate="tool_call", toolCallId="call", title="Reading", name="read_file", kind="read",
            rawInput={"path": "first.txt"}, rawOutput={"provider": "raw"}, status="pending"),
        acp(sessionUpdate="tool_call_update", toolCallId="call", status="in_progress",
            rawOutput=None, content=content, kind="other"),
        acp(sessionUpdate="tool_call_update", toolCallId="call", status="success"),
        acp(sessionUpdate="tool_call_update", toolCallId="call", status="completed"),
        acp(sessionUpdate="tool_call_update", toolCallId="call", status="pending",
            rawInput={"path": "stale.txt"}, rawOutput="stale output"),
        acp(sessionUpdate="tool_call", toolCallId="unknown", title="Unknown", status="succeeded"),
    ])
    assert [event["status"] for event in events] == ["preparing", "running", "running", "completed", "unknown"]
    result = events[-2]
    assert result["name"] == "read_file" and result["title"] == "Reading" and result["toolKind"] == "other"
    assert result["input"] == {"path": "first.txt"} and result["output"] == {"provider": "raw"}
    assert result["content"] == content


def test_claude_acp_parent_extension_survives_terminal_update_in_same_session(tmp_path):
    foreign = acp(sessionUpdate="tool_call", toolCallId="foreign", title="Foreign",
                  _meta={"claudeCode": {"parentToolUseId": "parent"}})
    foreign["params"]["sessionId"] = "other-session"
    events = run_wire(tmp_path, "acp", [
        acp(sessionUpdate="tool_call", toolCallId="child", title="Child read", kind="read",
            status="in_progress", _meta={"claudeCode": {"parentToolUseId": "parent", "extra": "private metadata"}}),
        foreign,
        acp(sessionUpdate="tool_call_update", toolCallId="child", status="failed", rawOutput="read failed"),
    ])
    assert [event["callId"] for event in events] == ["child", "child"]
    assert [event["parentCallId"] for event in events] == ["parent", "parent"]
    assert events[-1]["status"] == "failed" and events[-1]["output"] == "read failed"
    assert "private metadata" not in json.dumps(events)


def test_acp_prefers_valid_explicit_and_existing_edges_to_claude_fallback(tmp_path):
    meta = {"claudeCode": {"parentToolUseId": "provider-parent"}}
    events = run_wire(tmp_path, "acp", [
        acp(sessionUpdate="tool_call", toolCallId="explicit-child", title="Explicit",
            parentCallId="explicit-parent", _meta=meta),
        acp(sessionUpdate="tool_call_update", toolCallId="explicit-child", status="completed", _meta=meta),
        acp(sessionUpdate="tool_call", toolCallId="provider-child", title="Provider",
            parentCallId="provider-child", _meta=meta),
        acp(sessionUpdate="tool_call_update", toolCallId="provider-child", status="completed",
            _meta={"claudeCode": {"parentToolUseId": "conflicting-replay-parent"}}),
    ])
    assert [event["parentCallId"] for event in events] == [
        "explicit-parent", "explicit-parent", "provider-parent", "provider-parent",
    ]


def test_acp_does_not_synthesize_parent_or_accept_malformed_provider_edges(tmp_path):
    events = run_wire(tmp_path, "acp", [
        acp(sessionUpdate="tool_call", toolCallId="root", title="Root task", status="in_progress"),
        acp(sessionUpdate="tool_call", toolCallId="ordinary", title="Unrelated call"),
        acp(sessionUpdate="tool_call", toolCallId="self", title="Self edge",
            _meta={"claudeCode": {"parentToolUseId": "self"}}),
        acp(sessionUpdate="tool_call", toolCallId="control", title="Bad edge",
            _meta={"claudeCode": {"parentToolUseId": "root\nother"}}),
        acp(sessionUpdate="tool_call", toolCallId="invalid-explicit", title="Bad explicit",
            parentCallId={"id": "root"}, _meta={"otherProvider": {"parentToolUseId": "root"}}),
    ])
    assert len(events) == 5
    assert all("parentCallId" not in event for event in events)


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


def test_interrupted_command_does_not_infer_signal_from_exit_code_or_text(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("item/completed", item={"id": "cancel-1", "type": "commandExecution", "command": "render",
              "status": "interrupted", "exitCode": 143,
              "aggregatedOutput": "cancelled\n[killed by signal: SIGTERM]"}),
    ])
    assert events[0]["status"] == "interrupted"
    assert events[0]["exitCode"] == 143
    assert events[0]["output"] == "cancelled\n[killed by signal: SIGTERM]"
    assert not {"signal", "stdout", "stderr"}.intersection(events[0])


def test_acp_execute_keeps_provider_output_without_inventing_terminal_fields(tmp_path):
    raw = {"stdout": "a", "stderr": "b", "signal": "SIGTERM", "exitCode": 7}
    [event] = run_wire(tmp_path, "acp", [
        acp(sessionUpdate="tool_call", toolCallId="exec-1", kind="execute", status="completed",
            rawInput={"command": "render"}, rawOutput=raw),
    ])
    assert event["output"] == raw
    assert not {"outputStream", "exitCode", "signal", "stdout", "stderr"}.intersection(event)


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


def test_codex_plan_distinguishes_missing_snapshot_from_explicit_reset(tmp_path):
    events = run_wire(tmp_path, "codex", [
        codex("turn/plan/updated"),
        codex("item/completed", item={"id": "plan-text", "type": "plan", "text": "全部完成"}),
        codex("turn/plan/updated", plan=[]),
    ])
    assert [event.get("steps") for event in events] == [None, None, []]
    assert events[1]["text"] == "全部完成"
