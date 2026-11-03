"""Host-side task execution, adapted from Super Agent's Ticket/Assignment worker."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from easel.superagent_tasks import SuperAgentTaskError

LOG = logging.getLogger(__name__)
PLATFORMS = ("douyin", "xiaohongshu", "wechat")
DRAFT_STEPS = [{"kind": kind, "retrySafe": True} for kind in ("preference", "draft", "review")]
STYLES = ["专业", "亲切", "活泼"]
ACTIVE = {"queued", "waiting_input", "running", "stopping"}


def draft_receipt(text: str) -> dict:
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4].strip()
    result = json.loads(text)
    if not isinstance(result, dict) or set(result) != set(PLATFORMS) or any(
        not isinstance(value, str) or not value.strip() or len(value) > 4000 for value in result.values()
    ):
        raise ValueError("Missing platform draft")
    return {"kind": "drafts", "drafts": {platform: result[platform].strip() for platform in PLATFORMS}}


class SuperagentWorker:
    def __init__(self, store, candidates, authorized, generate, publish, reconcile=None, *,
                 interval=1, lease_ms=15_000, max_workers=2, preference_ms=25_000):
        self.store = store
        self.candidates = candidates
        self.authorized = authorized
        self.generate = generate
        self.publish = publish
        self.reconcile = reconcile
        self.interval = interval
        self.lease_ms = lease_ms
        self.max_workers = max_workers
        self.preference_ms = preference_ms
        self.worker_id = uuid.uuid4().hex
        self.running = {}
        self.loop = None

    def start(self):
        if self.loop is None:
            self.loop = asyncio.create_task(self._run())

    async def close(self):
        if self.loop is not None:
            self.loop.cancel()
            await asyncio.gather(self.loop, return_exceptions=True)
            self.loop = None
        for task in self.running.values():
            task.cancel()
        await asyncio.gather(*self.running.values(), return_exceptions=True)
        self.running.clear()

    async def _run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                LOG.exception("Super Agent scheduler pass failed")
            await asyncio.sleep(self.interval)

    async def tick(self):
        for task_id, worker in list(self.running.items()):
            if worker.done():
                self.running.pop(task_id)
                if not worker.cancelled() and worker.exception():
                    LOG.error("Super Agent assignment failed", exc_info=worker.exception())
        for task in await asyncio.to_thread(self.candidates):
            task_id = task["id"]
            if task_id in self.running:
                continue
            scope = {"actor_id": task["actorId"], "project_id": task["projectId"]}
            if not self.authorized(**scope):
                if task["status"] in {"queued", "waiting_input", "running", "stopping"}:
                    try:
                        self.store.stop(task_id, **scope)
                        self.store.settle_due(task_id, **scope)
                    except SuperAgentTaskError:
                        pass
                continue
            try:
                if task["status"] == "unconfirmed" and self.reconcile:
                    await self.reconcile(task, scope)
                    continue
                if task["status"] not in ACTIVE:
                    continue
                if len(self.running) >= self.max_workers:
                    if task["status"] == "waiting_input":
                        self.store.settle_due(task_id, **scope)
                    continue
                assignment = self.store.claim(task_id, self.worker_id, lease_ms=self.lease_ms, **scope)
                if assignment:
                    self.running[task_id] = asyncio.create_task(self._assignment(assignment, scope))
            except SuperAgentTaskError:
                continue
            except Exception:
                LOG.exception("Super Agent task dispatch or readback failed")

    async def _assignment(self, assignment, scope):
        task, step, token = (assignment[key] for key in ("task", "step", "leaseToken"))
        task_id = task["id"]
        work = None

        def check():
            if not self.authorized(**scope):
                raise SuperAgentTaskError("任务权限已撤销", 403)
            current = self.store.renew(task_id, token, lease_ms=self.lease_ms, **scope)
            if current["status"] == "stopping":
                raise _Stopping

        try:
            check()
            kind = step["kind"]
            answer = (step.get("question") or {}).get("answer")
            if kind == "preference" and answer is None:
                self.store.ask(task_id, token, {"prompt": "草稿希望采用哪种语气？", "options": STYLES},
                               default_answer=STYLES[0], deadline_ms=self.store.now_ms() + self.preference_ms, **scope)
                return
            if kind == "review" and answer is None:
                self.store.ask(task_id, token, {"prompt": "请核对三平台草稿后确认审核结果。",
                    "options": ["通过草稿", "退回草稿"], "requiresHuman": True},
                    deadline_ms=self.store.now_ms() + 7 * 86400_000, **scope)
                return
            if kind == "preference":
                receipt = {"kind": "preference", "answer": answer, "source": step["question"]["resolvedBy"]}
            elif kind == "review":
                if answer != "通过草稿":
                    self.store.fail_step(task_id, token, {"code": "review_returned", "message": "草稿已被退回"}, **scope)
                    return
                receipt = {"kind": "review", "status": "approved", "source": "human"}
            elif kind == "draft" and step.get("checkpoint"):
                receipt = step["checkpoint"]
            elif kind in ("draft", "publish"):
                callback = self.generate(task, scope) if kind == "draft" else self.publish(task, step, scope, check)
                work = asyncio.create_task(callback)
                while not work.done():
                    await asyncio.wait({work}, timeout=min(1, self.lease_ms / 4000))
                    check()
                result = work.result()
                if kind == "draft":
                    receipt = draft_receipt(result)
                    self.store.checkpoint(task_id, token, receipt, **scope)
                elif result["state"] == "confirmed":
                    receipt = result
                else:
                    self.store.fail_step(task_id, token, {"code": "publication_unconfirmed", "message": "请核对发布回执，系统不会重发"}, **scope)
                    return
            else:
                raise ValueError("Unknown task step")
            check()
            self.store.finish_step(task_id, token, receipt, **scope)
        except _Stopping:
            if work is not None:
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
            try:
                if step["retrySafe"]:
                    self.store.ack_stop(task_id, token, **scope)
                else:
                    self.store.fail_step(task_id, token, {"code": "stop_unconfirmed", "message": "外部动作结果待核对"}, **scope)
            except SuperAgentTaskError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                self.store.fail_step(task_id, token, {"code": "assignment_failed", "message": "本步骤未取得可验收的回执"}, **scope)
            except SuperAgentTaskError:
                pass
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)


class _Stopping(Exception):
    pass
