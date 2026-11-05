"""Current-IP workshop delivery authorizes the host, not a model credential."""
import asyncio
import hashlib
import json
import os
import sys

import pytest

from easel.harnesses.base import RunRequest
from easel.harnesses.codex import CodexAdapter
from easel.video_workshop import WorkshopStore
from easel.workshop_delivery import load_episodes
from tests.test_native_business_objects import WIRE
from web import workshop_delivery


def make_plan(outputs):
    manifest = outputs / "family" / "series.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "series": "家庭教育", "episodes": [
            {"id": "01", "title": "親子晚飯", "brief": "真实情景及主持总结", "path": "family/01.mp4"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return {"action": "plan", "manifestPath": "family/series.json"}


@pytest.mark.parametrize("bad_path", [
    "/private/series.json", "../other/series.json", "_inbox/series.json",
    "family\\series.json", "family/.secret/series.json",
])
def test_host_manifest_is_bound_to_public_current_ip_paths(tmp_path, bad_path):
    payload = make_plan(tmp_path)
    payload["manifestPath"] = bad_path
    with pytest.raises(ValueError):
        workshop_delivery._manifest_request(payload, tmp_path)


def test_host_manifest_cannot_follow_a_symlink_or_supply_identity(tmp_path):
    outputs = tmp_path / "outputs"
    payload = make_plan(outputs)
    external = tmp_path / "different.json"
    external.write_text("{}", encoding="utf-8")
    target = outputs / payload["manifestPath"]
    target.unlink()
    target.symlink_to(external)
    with pytest.raises(ValueError):
        workshop_delivery._manifest_request(payload, outputs)
    with pytest.raises(ValueError):
        workshop_delivery._manifest_request({**payload, "projectId": "foreign"}, outputs)


def test_planning_writes_real_scoped_objects_and_deduplicates_without_media(tmp_path, monkeypatch):
    payload = make_plan(tmp_path)
    checks = []

    def authorize():
        checks.append(True)

    arguments = dict(outputs=tmp_path, ip="approved-ip", authorize=authorize)
    first = workshop_delivery.run_workshop_delivery(payload, **arguments)
    assert workshop_delivery.run_workshop_delivery(payload, **arguments) == first
    assert len(checks) >= 5
    assert len(WorkshopStore(tmp_path).list()) == 1
    record = WorkshopStore(tmp_path).get(first[0]["workshopId"])
    assert record["stage"] == "draft"
    assert record["deliverables"] == []
    assert "/approved-ip/workshop/" in first[0]["workshopUrl"]


def test_revoked_authority_prevents_writing_workshop(tmp_path):
    payload = make_plan(tmp_path)
    checks = 0

    def authorize():
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise PermissionError("revoked before create")

    with pytest.raises(PermissionError):
        workshop_delivery.run_workshop_delivery(payload, outputs=tmp_path, ip="approved-ip",
            authorize=authorize)
    assert list((tmp_path / "_workshop" / "projects").glob("*.json")) == []


def test_manifest_directory_is_part_of_new_batch_identity_and_legacy_resume_is_exact(tmp_path, monkeypatch):
    payload = make_plan(tmp_path)
    first = tmp_path / payload["manifestPath"]
    second = tmp_path / "other" / first.name
    second.parent.mkdir()
    second.write_bytes(first.read_bytes())
    assert load_episodes(first, tmp_path, set(), True)[0]["marker"] != load_episodes(second, tmp_path, set(), True)[0]["marker"]

    data = json.loads(first.read_text())
    series = data["series"]
    old_digest = hashlib.sha256(f"{first.stem}\n{series}".encode()).hexdigest()[:16]
    legacy = WorkshopStore(tmp_path).create({
        "title": f"{series}｜{data['episodes'][0]['title']}",
        "brief": f"现有项目\n\nEasel 制作批次 {old_digest}/01",
    })
    data["episodes"][0]["workshopId"] = legacy["id"]
    first.write_text(json.dumps(data))
    result = workshop_delivery.run_workshop_delivery(payload, outputs=tmp_path, ip="approved-ip",
        authorize=lambda: None)
    assert result[0]["workshopId"] == legacy["id"]
    assert len(WorkshopStore(tmp_path).list()) == 1
    data["episodes"][0]["workshopId"] = "f" * 32
    first.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="不属于当前制作批次"):
        workshop_delivery.run_workshop_delivery(payload, outputs=tmp_path, ip="approved-ip",
            authorize=lambda: None)


def test_codex_typed_delivery_registers_and_replays_same_call_once(tmp_path):
    script = tmp_path / "workshop_wire.py"
    script.write_text(WIRE.replace("easel_business_objects", "easel_workshop_delivery"), encoding="utf-8")
    payload = {"action": "plan", "manifestPath": "family/series.json"}
    calls = [
        {"arguments": payload, "overrides": {"callId": "same-workshop-call"}},
        {"arguments": payload, "overrides": {"callId": "same-workshop-call"}},
        {"arguments": {**payload, "action": "deliver"}, "overrides": {"callId": "same-workshop-call"}},
    ]
    log = tmp_path / "wire.jsonl"
    seen = []

    async def write(arguments):
        seen.append(arguments)
        await asyncio.sleep(0.01)
        return [{"workshopId": "a" * 32}]

    async def no_input(*args):
        pytest.fail("typed production called interactive approval")

    request = RunRequest("Plan the current IP", tmp_path,
        {**os.environ, "BUSINESS_AUDIT": str(log), "BUSINESS_REGISTRATION": str(tmp_path / "registered.json"),
         "BUSINESS_CALLS": json.dumps(calls), "BUSINESS_CONCURRENT": "1"},
        command=(sys.executable, str(script)), timeout=10, workshop_delivery=write)
    asyncio.run(CodexAdapter().run(request, lambda *args: None, no_input, lambda identifier: None))
    wire = [json.loads(line) for line in log.read_text().splitlines()]
    tools = next(message for message in wire if message.get("method") == "thread/start")["params"]["dynamicTools"]
    schema = next(tool for tool in tools if tool["name"] == "easel_workshop_delivery")["inputSchema"]
    assert set(schema["properties"]) == {"action", "manifestPath", "episodes"}
    replies = {message["id"]: message["result"] for message in wire
               if str(message.get("id", "")).startswith("business-reverse-")}
    assert seen == [payload]
    assert replies["business-reverse-0"] == replies["business-reverse-1"]
    assert replies["business-reverse-0"]["success"] is True
    assert replies["business-reverse-2"]["success"] is False
