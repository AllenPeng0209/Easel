"""The default-mode Xiangya question tool uses Codex's real reverse-RPC shape."""
import asyncio
import json
import os
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.codex import CodexAdapter


WIRE = '''import json,os,sys
def send(message): print(json.dumps(message),flush=True)
audit=os.environ["QUESTION_AUDIT"]
thread=os.environ.get("QUESTION_NATIVE_ID") or "question-thread"
mode=os.environ.get("QUESTION_MODE", "normal")
for line in sys.stdin:
 m=json.loads(line)
 with open(audit,"a") as f: f.write(json.dumps(m)+"\\n")
 method=m.get("method")
 if method=="initialize": send({"id":m["id"],"result":{}})
 elif method in ("thread/start","thread/resume"):
  send({"id":m["id"],"result":{"thread":{"id":thread}}})
 elif method=="turn/start":
  send({"id":m["id"],"result":{"turn":{"id":"question-turn"}}})
  if mode!="no-call":
   send({"id":"question-call","method":"item/tool/call","params":{
    "threadId":"foreign-thread" if mode=="foreign" else thread,"turnId":"question-turn",
    "callId":"question-call-id","tool":"unknown_tool" if mode=="unknown" else "easel_ask_user",
    "arguments":json.loads(os.environ["QUESTION_ARGUMENTS"])}})
 elif m.get("id")=="question-call":
  send({"method":"item/completed","params":{"threadId":thread,"turnId":"question-turn",
   "item":{"id":"final","type":"agentMessage","text":"Continued after the client tool response"}}})
  send({"method":"turn/completed","params":{"threadId":thread,"turnId":"question-turn",
   "turn":{"id":"question-turn","status":"completed"}}})
 if method=="turn/start" and mode=="no-call":
  send({"method":"turn/completed","params":{"threadId":thread,"turnId":"question-turn",
   "turn":{"id":"question-turn","status":"completed"}}})
'''


def run_question(tmp_path, arguments, *, mode="normal", native_id=""):
    script = tmp_path / "question_wire.py"
    script.write_text(WIRE)
    audit = tmp_path / "question_audit.jsonl"
    requested = []
    emitted = []
    identifiers = []
    request = RunRequest("Ask a necessary question, then continue", tmp_path, {
        **os.environ, "QUESTION_AUDIT": str(audit), "QUESTION_MODE": mode,
        "QUESTION_ARGUMENTS": json.dumps(arguments), "QUESTION_NATIVE_ID": native_id,
    }, native_id=native_id, command=(sys.executable, str(script)), timeout=5)

    async def ask(questions):
        requested.append(questions)
        return {question["questionId"]: ["专业"] for question in questions}

    error = None
    try:
        asyncio.run(CodexAdapter().run(request, lambda *event: emitted.append(event), ask, identifiers.append))
    except HarnessError as caught:
        error = caught
    messages = [json.loads(line) for line in audit.read_text().splitlines()]
    return {"messages": messages, "questions": requested, "events": emitted, "identifiers": identifiers, "error": error}


QUESTION = {"questions": [{"id": "tone", "header": "语气", "question": "口播采用哪种语气？",
                          "options": [{"label": "专业", "description": "推荐", "recommended": True},
                                      {"label": "轻松", "description": "日常表达"}],
                          "defaultAnswers": ["专业"]}]}


def test_new_thread_registers_an_eager_default_mode_question_tool(tmp_path):
    result = run_question(tmp_path, QUESTION, mode="no-call")
    assert result["error"] is None
    options = next(message["params"] for message in result["messages"] if message.get("method") == "thread/start")
    specs = options.get("dynamicTools") or []
    assert len(specs) == 1, "New Codex threads need a client-owned question tool, independent of plan-mode tools"
    tool = specs[0]
    assert tool["name"] == "easel_ask_user"
    assert tool["type"] == "function"
    assert tool.get("deferLoading", False) is False
    assert tool["inputSchema"]["required"] == ["questions"]
    assert tool["inputSchema"]["additionalProperties"] is False
    assert set(tool["inputSchema"]["properties"]) == {"questions"}


def test_dynamic_question_round_trip_calls_host_ask_then_allows_the_turn_to_finish(tmp_path):
    result = run_question(tmp_path, QUESTION)
    assert result["error"] is None, str(result["error"])
    assert len(result["questions"]) == 1
    item = result["questions"][0][0]
    assert item["questionId"] == "tone"
    assert item["defaultAnswers"] == ["专业"]
    assert item["options"][0]["recommended"] is True
    response = next(message for message in result["messages"] if message.get("id") == "question-call")
    assert response["result"]["success"] is True
    assert response["result"]["contentItems"][0]["type"] == "inputText"
    assert json.loads(response["result"]["contentItems"][0]["text"])["answers"] == {"tone": ["专业"]}
    assert ("token", "Continued after the client tool response") in result["events"]


@pytest.mark.parametrize("arguments", [
    {}, {"questions": []}, {"questions": "wrong"}, {"questions": [{"id": "same", "question": "First?"}, {"id": "same", "question": "Second?"}]},
    {"questions": [{"id": "q", "question": "What?", "apiKey": "metadata-must-not-return"}]},
    {"questions": [{"id": "q", "question": "Provide the account password", "isSecret": False}]},
    {"questions": [{"id": "__proto__", "question": "Which style?"}]},
])
def test_invalid_or_secret_dynamic_questions_never_reach_host_input_or_echo_arguments(tmp_path, arguments):
    result = run_question(tmp_path, arguments)
    assert result["error"] is None, "Tool validation should return a tool failure so the model can correct it"
    assert result["questions"] == []
    response = next(message for message in result["messages"] if message.get("id") == "question-call")
    assert response["result"]["success"] is False
    assert "metadata-must-not-return" not in json.dumps(response)


@pytest.mark.parametrize("mode", ["foreign", "unknown"])
def test_foreign_or_unknown_dynamic_tool_requests_cannot_reach_host_ask(tmp_path, mode):
    result = run_question(tmp_path, QUESTION, mode=mode)
    assert result["questions"] == []
    assert result["error"] is not None


def test_resume_keeps_the_existing_native_thread_without_unsupported_tool_parameters(tmp_path):
    result = run_question(tmp_path, QUESTION, mode="no-call", native_id="saved-original-thread")
    assert result["error"] is None
    assert result["identifiers"] == ["saved-original-thread"]
    assert not any(message.get("method") == "thread/start" for message in result["messages"])
    resume = next(message["params"] for message in result["messages"] if message.get("method") == "thread/resume")
    assert resume["threadId"] == "saved-original-thread"
    assert "dynamicTools" not in resume
