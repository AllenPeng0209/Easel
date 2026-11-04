"""D01: Codex declares files through the host, never by trusting model prose."""
import asyncio
import json
import os
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.codex import CodexAdapter


WIRE = '''import json,os,sys
def send(value): print(json.dumps(value),flush=True)
thread=os.environ.get("PRESENT_NATIVE_ID") or "present-thread"
for line in sys.stdin:
 m=json.loads(line)
 with open(os.environ["PRESENT_AUDIT"],"a") as f: f.write(json.dumps(m)+"\\n")
 method=m.get("method")
 if method=="initialize": send({"id":m["id"],"result":{}})
 elif method in ("thread/start","thread/resume"):
  send({"id":m["id"],"result":{"thread":{"id":thread}}})
 elif method=="turn/start":
  send({"id":m["id"],"result":{"turn":{"id":"present-turn"}}})
  if os.environ.get("PRESENT_NO_CALL"):
   send({"method":"turn/completed","params":{"threadId":thread,"turnId":"present-turn",
    "turn":{"id":"present-turn","status":"completed"}}})
  else:
   send({"id":"present-reverse","method":"item/tool/call","params":{
    "threadId":"foreign-thread" if os.environ.get("PRESENT_FOREIGN") else thread,
    "turnId":"present-turn","callId":"present-call-1","tool":"present",
    "arguments":json.loads(os.environ["PRESENT_ARGS"])}})
 elif m.get("id")=="present-reverse":
  send({"method":"item/completed","params":{"threadId":thread,"turnId":"present-turn",
   "item":{"id":"final","type":"agentMessage","text":"All finished"}}})
  send({"method":"turn/completed","params":{"threadId":thread,"turnId":"present-turn",
   "turn":{"id":"present-turn","status":"completed"}}})
 elif method=="turn/interrupt": send({"id":m["id"],"result":{}})
'''


METADATA = {"artifactId": "artifact-1", "path": "demo/report.pdf", "name": "report.pdf",
            "mime": "application/pdf", "size": 12, "status": "available",
            "url": "/api/media/demo/report.pdf", "description": "最终报告"}


def request_for(tmp_path, arguments, present, *, no_call=False, native_id="", foreign=False):
    assert "present" in RunRequest.__dataclass_fields__, "The host must supply a validated file-declaration callback"
    script = tmp_path / "present_wire.py"
    script.write_text(WIRE)
    return RunRequest("Create and present report", tmp_path, {
        **os.environ, "PRESENT_AUDIT": str(tmp_path / "present_audit.jsonl"),
        "PRESENT_ARGS": json.dumps(arguments), "PRESENT_NATIVE_ID": native_id,
        "PRESENT_NO_CALL": "1" if no_call else "", "PRESENT_FOREIGN": "1" if foreign else "",
    }, native_id=native_id, command=(sys.executable, str(script)), timeout=5, present=present)


def run_present(tmp_path, arguments, present, **options):
    request = request_for(tmp_path, arguments, present, **options)
    emitted, identifiers = [], []

    async def no_questions(questions):
        raise AssertionError("Present must not invent a permission answer or ask for credentials")

    error = None
    try:
        asyncio.run(CodexAdapter().run(request, lambda *event: emitted.append(event), no_questions, identifiers.append))
    except HarnessError as caught:
        error = caught
    messages = [json.loads(line) for line in (tmp_path / "present_audit.jsonl").read_text().splitlines()]
    return {"messages": messages, "events": emitted, "identifiers": identifiers, "error": error}


def response(result):
    return next(item["result"] for item in result["messages"] if item.get("id") == "present-reverse")


@pytest.mark.parametrize("available", [True, False])
def test_present_is_registered_only_when_the_host_supplies_a_callback(tmp_path, available):
    async def present(files, call_id):
        return [METADATA]

    result = run_present(tmp_path, {"files": [{"path": "outputs/report.pdf"}]}, present if available else None, no_call=True)
    assert result["error"] is None
    options = next(item["params"] for item in result["messages"] if item.get("method") == "thread/start")
    tools = {tool["name"]: tool for tool in options["dynamicTools"]}
    assert "easel_ask_user" in tools
    assert ("present" in tools) is available
    if available:
        tool = tools["present"]
        schema = tool["inputSchema"]
        assert tool["type"] == "function"
        assert schema["required"] == ["files"]
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == {"files"}
        assert schema["properties"]["files"]["minItems"] == 1
        assert schema["properties"]["files"]["maxItems"] == 8
        file = schema["properties"]["files"]["items"]
        assert file["required"] == ["path"]
        assert set(file["properties"]) == {"path", "description"}


def test_present_returns_only_the_host_canonical_metadata_and_does_not_emit_success_itself(tmp_path):
    seen = []

    async def present(files, call_id):
        seen.append((files, call_id))
        return [METADATA]

    args = {"files": [{"path": "outputs/report.pdf", "description": "最终报告"}]}
    result = run_present(tmp_path, args, present)
    assert result["error"] is None, str(result["error"])
    assert seen == [(args["files"], "present-call-1")]
    reply = response(result)
    assert reply["success"] is True
    assert reply["contentItems"][0]["type"] == "inputText"
    assert json.loads(reply["contentItems"][0]["text"]) == {"files": [METADATA]}
    assert not any(kind == "agent_event" and payload.get("kind") == "artifact" for kind, payload in result["events"])
    assert ("token", "All finished") in result["events"]


@pytest.mark.parametrize("arguments", [
    {}, {"files": []}, {"files": "outputs/a.pdf"}, {"files": [{"path": ""}]},
    {"files": [{"path": "   "}]}, {"files": [{"path": 1}]},
    {"files": [{"path": "outputs/a.pdf", "description": 1}]},
    {"files": [{"path": "outputs/a.pdf", "url": "https://untrusted.invalid"}]},
    {"files": [{"path": "outputs/a.pdf"}] * 9},
    {"files": [{"path": "outputs/a.pdf"}], "status": "available"},
])
def test_invalid_present_arguments_never_reach_the_host_or_create_a_delivery(tmp_path, arguments):
    seen = []

    async def present(files, call_id):
        seen.append(files)
        return [METADATA]

    result = run_present(tmp_path, arguments, present)
    assert result["error"] is None
    assert seen == []
    assert response(result)["success"] is False


@pytest.mark.parametrize("outcome", [None, [], ValueError("do-not-echo-private-path"), HarnessError("do-not-echo-private-path"),
                                   PermissionError("do-not-echo-private-path")])
def test_host_validation_failure_is_a_failed_tool_result_not_a_fabricated_delivery(tmp_path, outcome):
    async def present(files, call_id):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    result = run_present(tmp_path, {"files": [{"path": "outputs/missing.pdf"}]}, present)
    assert result["error"] is None
    reply = response(result)
    assert reply["success"] is False
    assert "do-not-echo-private-path" not in json.dumps(reply)


def test_a_dynamic_present_call_without_a_host_callback_fails_closed(tmp_path):
    result = run_present(tmp_path, {"files": [{"path": "outputs/a.pdf"}]}, None)
    assert result["error"] is None
    assert response(result)["success"] is False


def test_present_keeps_existing_native_thread_history_and_resume_parameters(tmp_path):
    async def present(files, call_id):
        return [METADATA]

    result = run_present(tmp_path, {"files": [{"path": "outputs/a.pdf"}]}, present,
                         native_id="original-thread", no_call=True)
    assert result["error"] is None
    assert result["identifiers"] == ["original-thread"]
    assert not any(item.get("method") == "thread/start" for item in result["messages"])
    resumed = next(item["params"] for item in result["messages"] if item.get("method") == "thread/resume")
    assert "dynamicTools" not in resumed


def test_foreign_thread_present_does_not_reach_the_host(tmp_path):
    seen = []

    async def present(files, call_id):
        seen.append(files)
        return [METADATA]

    result = run_present(tmp_path, {"files": [{"path": "outputs/a.pdf"}]}, present, foreign=True)
    assert result["error"] is not None
    assert seen == []


def test_cancelling_the_turn_cancels_pending_present_instead_of_reporting_success(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        cleaned = asyncio.Event()

        async def present(files, call_id):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

        async def reject(questions):
            raise AssertionError("No questions expected")

        request = request_for(tmp_path, {"files": [{"path": "outputs/a.pdf"}]}, present)
        task = asyncio.create_task(CodexAdapter().run(request, lambda *args: None, reject, lambda identifier: None))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
        audit = (tmp_path / "present_audit.jsonl").read_text()
        assert '"success": true' not in audit

    asyncio.run(scenario())
