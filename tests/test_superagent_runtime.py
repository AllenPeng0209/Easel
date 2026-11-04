"""Real task HTTP authorization and scheduler behavior with a controlled generator."""
import asyncio

from easel.superagent_runtime import draft_receipt


def test_draft_receipt_requires_all_three_distinct_platform_outputs():
    assert draft_receipt('{"douyin":"短视频脚本","xiaohongshu":"小红书草稿","wechat":"公众号草稿"}')['kind'] == 'drafts'
    for invalid in ('OK', '{"douyin":"draft"}', '{"douyin":"","xiaohongshu":"x","wechat":"w"}'):
        try:
            draft_receipt(invalid)
        except ValueError:
            continue
        raise AssertionError('Incomplete generation was accepted')


def test_worker_restarts_from_due_question_and_finishes_only_after_human_review(tmp_path):
    from easel.superagent_runtime import DRAFT_STEPS, SuperagentWorker
    from easel.superagent_tasks import SuperAgentTaskStore

    async def run():
        clock = [1000]
        scope = {"actor_id": "author", "project_id": "default"}
        calls = []
        async def generate(task, _scope):
            calls.append(task["id"])
            return '{"douyin":"口播","xiaohongshu":"笔记","wechat":"文章"}'
        async def publish(*_):
            raise AssertionError("Draft flow never publishes")
        def worker():
            db = SuperAgentTaskStore(tmp_path, now_ms=lambda: clock[0])
            return db, SuperagentWorker(db, db.scheduler_candidates, lambda **_: True,
                generate, publish, interval=.01, preference_ms=10)
        db, first = worker()
        task_id = db.create(title="选题", brief="家居", steps=DRAFT_STEPS, **scope)["id"]
        async def until(predicate):
            async with asyncio.timeout(3):
                while True:
                    row = db.get(task_id, **scope)
                    if predicate(row):
                        return row
                    await asyncio.sleep(.01)
        first.start()
        await until(lambda row: row["status"] == "waiting_input")
        await first.close()
        clock[0] += 100
        db, second = worker()
        second.start()
        try:
            pending = await until(lambda row: row["status"] == "waiting_input" and row["question"]["requiresHuman"])
            assert pending["steps"][0]["receipt"]["source"] == "default"
            assert pending["steps"][1]["receipt"]["kind"] == "drafts"
            assert calls == [task_id]
            db.answer(task_id, pending["question"]["id"], "通过草稿", **scope)
            done = await until(lambda row: row["status"] == "completed")
            assert done["steps"][2]["receipt"]["source"] == "human"
        finally:
            await second.close()

    asyncio.run(run())


import pytest
from tests.test_chat_artifacts import artifact_http_environment


@pytest.mark.access_integration
def test_task_intake_deduplicates_and_enforces_ip_and_actor(artifact_http_environment):
    _, owner, members, anonymous, _ = artifact_http_environment
    route = "/api/superagent/tasks"
    body = {"projectId": "default", "title": "选题", "brief": "家居", "requestId": "same-intake"}
    assert anonymous().get(route).status_code == 401
    assert members["viewer"].post(route, json=body).status_code == 403
    first = owner.post(route, json=body)
    assert first.status_code == 200, first.text
    identifier = first.json()["id"]
    assert owner.post(route, json=body).json()["id"] == identifier
    assert owner.post(route, json={**body, "brief": "changed"}).status_code == 409
    assert members["operator"].get(route).json()["tasks"] == []
    assert members["operator"].post(f"{route}/{identifier}/stop", json={"projectId": "default"}).status_code == 404
    assert owner.post(f"{route}/publish", json={"projectId": "default", "approvalId": "a"*32}).status_code == 404


@pytest.mark.access_integration
def test_chat_task_source_is_durable_and_bound_to_request(artifact_http_environment):
    _, owner, _, _, _ = artifact_http_environment
    route = "/api/superagent/tasks"
    body = {"projectId": "default", "title": "选题", "brief": "家居", "requestId": "chat-intake", "sessionId": "chat-one"}
    first = owner.post(route, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["steps"][0]["payload"] == {"sessionId": "chat-one"}
    assert owner.post(route, json={**body, "sessionId": "chat-two"}).status_code == 409
    assert owner.get(route).json()["tasks"][0]["steps"][0]["payload"]["sessionId"] == "chat-one"
