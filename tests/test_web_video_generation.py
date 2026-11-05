"""Native dynamic tools dispatch video on the Host, preserving sandbox policy."""


import asyncio


import importlib


import json


import os


import sys


import httpx


import pytest


from easel.harnesses.base import RunRequest


from easel.harnesses.codex import CodexAdapter


from tests.test_native_business_objects import WIRE


from tests.test_chat_artifacts import artifact_http_environment


def test_native_video_generation_callback_is_available():
    assert "video_generation" in RunRequest.__dataclass_fields__, "Missing native Host video generation callback"


def test_web_generation_adapter_is_available():
    assert importlib.util.find_spec("web.video_generation"), "Missing current-IP Host video adapter"


def run_native(tmp_path, calls, callback, *, enabled=True):
    if "video_generation" not in RunRequest.__dataclass_fields__:
        pytest.skip("callback not implemented")
    script = tmp_path / "video_wire.py"
    script.write_text(WIRE.replace("easel_business_objects", "easel_video_generation"))
    async def forbidden(*args):
        pytest.fail("Video tools cannot approve or publish")
    request = RunRequest("Use the Host to inspect video generation", tmp_path,
        {**os.environ, "BUSINESS_AUDIT": str(tmp_path / "wire.jsonl"),
         "BUSINESS_REGISTRATION": str(tmp_path / "native.json"), "BUSINESS_CALLS": json.dumps(calls)},
        command=(sys.executable, str(script)), timeout=10,
        video_generation=callback if enabled else None, publish=forbidden)
    asyncio.run(CodexAdapter().run(request, lambda *args: None, forbidden, lambda native_id: None))
    return [json.loads(line) for line in (tmp_path / "wire.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("bad", [
    {"action": "catalog", "baseUrl": "http://attacker"},
    {"action": "submit", "workshopId": "a" * 32, "clipId": "one", "approved": True},
    {"action": "collect", "workshopId": "a" * 32, "clipId": "../foreign"},
    {"action": "submit", "workshopId": "other-ip", "clipId": "one"},
    {"action": "prepare", "workshopId": "a" * 32, "clipId": "one"},
])
def test_invalid_native_video_calls_are_rejected_before_host_dispatch(tmp_path, bad):
    seen = []
    async def dispatch(*args):
        seen.append(args)
        return {}
    wire = run_native(tmp_path, [{"arguments": bad}], dispatch)
    assert seen == []
    reply = next(item["result"] for item in wire if item.get("id") == "business-reverse-0")
    assert reply["success"] is False


def test_same_native_video_call_id_is_idempotent_and_cannot_change_payload(tmp_path):
    seen = []
    async def dispatch(payload, call_id):
        seen.append((payload, call_id))
        return {"status": "queued", "taskId": "a" * 32}
    payload = {"action": "submit", "workshopId": "b" * 32, "clipId": "cat-one"}
    wire = run_native(tmp_path, [
        {"arguments": payload, "overrides": {"callId": "same-call"}},
        {"arguments": payload, "overrides": {"callId": "same-call"}},
        {"arguments": {**payload, "clipId": "cat-two"}, "overrides": {"callId": "same-call"}},
    ], dispatch)
    replies = [item["result"] for item in wire if str(item.get("id", "")).startswith("business-reverse-")]
    assert seen == [(payload, "same-call")]
    assert replies[0] == replies[1] and replies[2]["success"] is False


def test_disabled_video_tool_cannot_be_invoked_by_a_native_request(tmp_path):
    async def forbidden(*args):
        pytest.fail("disabled callback executed")
    wire = run_native(tmp_path, [{"arguments": {"action": "catalog"}}], forbidden, enabled=False)
    start = next(item["params"] for item in wire if item.get("method") == "thread/start")
    assert "easel_video_generation" not in {item["name"] for item in start["dynamicTools"]}
    assert next(item["result"] for item in wire if item.get("id") == "business-reverse-0")["success"] is False


@pytest.mark.parametrize('payload', [
    {'action': 'catalog', 'workshopId': 'a' * 32},
    {'action': 'submit', 'workshopId': 'a' * 32, 'clipId': 'one', 'prompt': 'changed paid prompt'},
    {'action': 'collect', 'workshopId': 'a' * 32, 'clipId': 'one', 'modelId': 'arbitrary'},
])
def test_tool_json_schema_cannot_smuggle_prepare_fields_into_later_actions(payload):
    jsonschema = pytest.importorskip('jsonschema')
    from easel.harnesses.codex import EASEL_VIDEO_GENERATION_TOOL
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, EASEL_VIDEO_GENERATION_TOOL['inputSchema'])


@pytest.mark.parametrize("overrides", [{"threadId": "foreign"}, {"turnId": "foreign"}, {"turnId": None}])
def test_foreign_native_thread_or_turn_cannot_reach_video_host(tmp_path, overrides):
    seen = []
    async def dispatch(*args):
        seen.append(args)
        return {}
    with pytest.raises(Exception):
        run_native(tmp_path, [{"arguments": {"action": "catalog"}, "overrides": overrides}], dispatch)
    assert seen == []
