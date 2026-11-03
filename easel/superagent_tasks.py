"""Durable supervised task steps and one-shot decisions.

The fenced lease and exact decision ticket follow Harness's owner-scoped
job-controller and permission-ticket patterns. SQLite is the Host authority;
worker liveness and external effects are never inferred from a stale lease.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from easel.agent_policy import question_policy, requests_secret
from easel.publication_approvals import _external_id

_MAX_STEPS = 64
_MAX_JSON = 128 * 1024
_MAX_TASK_BYTES = 2 * 1024 * 1024
_MAX_AUTO_LEASE_ATTEMPTS = 3
_ID = re.compile(r"[a-zA-Z0-9_.:-]{1,160}")


class SuperAgentTaskError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _identifier(value, label="标识") -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SuperAgentTaskError(f"无效的{label}", 422)
    return value


def _text(value, maximum: int, label: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]", value)):
        raise SuperAgentTaskError(f"无效的{label}", 422)
    return value


def _encoded(value, maximum: int = _MAX_TASK_BYTES) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise SuperAgentTaskError("无效的任务记录", 422) from None
    if len(encoded) > maximum:
        raise SuperAgentTaskError("任务记录超出大小限制", 422)
    return encoded


def _object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise SuperAgentTaskError(f"无效的{label}", 422)
    return json.loads(_encoded(value, _MAX_JSON))


def _public(task: dict) -> dict:
    record = {key: value for key, value in task.items() if key != "_lease"}
    if lease := task.get("_lease"):
        record["leaseExpiresAt"] = lease["expiresAt"]
    return record


def _lease_duration(value) -> int:
    if type(value) is not int or not 1 <= value <= 300_000:
        raise SuperAgentTaskError("无效的任务租约时长", 422)
    return value


class SuperAgentTaskStore:
    def __init__(self, directory: Path, *, now_ms=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "superagent_tasks.sqlite3"
        self.now_ms = now_ms or _now_ms
        with self._database() as database:
            if database.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN (?,?)",
                                ("superagent_tasks", "superagent_scope")).fetchone()[0] == 2:
                return
        with self._database(write=True) as database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS superagent_tasks (
                    id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    request_id TEXT, request_hash TEXT NOT NULL, updated_at INTEGER NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(actor_id, project_id, request_id)
                )
            """)
            database.execute("CREATE INDEX IF NOT EXISTS superagent_scope "
                             "ON superagent_tasks(actor_id,project_id,updated_at DESC,id)")

    @contextmanager
    def _database(self, *, write=False):
        database = sqlite3.connect(self.path, timeout=10)
        try:
            if write:
                database.execute("BEGIN IMMEDIATE")
            with database:
                yield database
        finally:
            database.close()

    @staticmethod
    def _scope(actor_id: str, project_id: str) -> tuple[str, str]:
        return _identifier(actor_id, "用户"), _identifier(project_id, "IP")

    @staticmethod
    def _read(database, task_id: str, scope) -> dict | None:
        row = database.execute("SELECT payload FROM superagent_tasks WHERE id=? AND actor_id=? AND project_id=?",
                               (task_id, *scope)).fetchone()
        return json.loads(row[0]) if row else None

    @contextmanager
    def _edit(self, task_id: str, actor_id: str, project_id: str):
        task_id = _identifier(task_id, "任务标识")
        scope = self._scope(actor_id, project_id)
        with self._database(write=True) as database:
            task = self._read(database, task_id, scope)
            if task is None:
                raise SuperAgentTaskError("任务不存在", 404)
            original = _encoded(task)
            now = self.now_ms()
            yield task, now
            if _encoded(task) != original:
                task["updatedAt"] = max(now, task["updatedAt"])
                database.execute("UPDATE superagent_tasks SET updated_at=?,payload=? WHERE id=? AND actor_id=? AND project_id=?",
                                 (task["updatedAt"], _encoded(task), task_id, *scope))

    def create(self, *, steps: list[dict], title: str, brief: str = "", request_id: str | None = None,
               actor_id: str, project_id: str) -> dict:
        scope = self._scope(actor_id, project_id)
        title = _text(title, 200, "任务标题")
        brief = _text(brief, 32000, "任务说明") if brief else ""
        if request_id is not None:
            _identifier(request_id, "请求标识")
        if not isinstance(steps, list) or not 1 <= len(steps) <= _MAX_STEPS:
            raise SuperAgentTaskError("任务步骤数量无效", 422)
        specification = []
        for step in steps:
            if (not isinstance(step, dict) or set(step) - {"kind", "payload", "retrySafe"}
                    or type(step.get("retrySafe", False)) is not bool):
                raise SuperAgentTaskError("无效的任务步骤", 422)
            kind = _identifier(step.get("kind"), "步骤类型")
            if kind == "publish" and step.get("retrySafe"):
                raise SuperAgentTaskError("公开发布步骤不能自动重试", 422)
            specification.append({"kind": kind,
                                  "payload": _object(step.get("payload", {}), "步骤参数"),
                                  "retrySafe": step.get("retrySafe", False)})
        fingerprint = hashlib.sha256(_encoded([title, brief, specification]).encode("ascii")).hexdigest()
        with self._database(write=True) as database:
            if request_id is not None:
                row = database.execute("SELECT request_hash,payload FROM superagent_tasks "
                                       "WHERE actor_id=? AND project_id=? AND request_id=?", (*scope, request_id)).fetchone()
                if row is not None:
                    if row[0] != fingerprint:
                        raise SuperAgentTaskError("同一任务请求已绑定其他内容")
                    return _public(json.loads(row[1]))
            now = self.now_ms()
            task = {"id": uuid.uuid4().hex, "actorId": actor_id, "projectId": project_id,
                    "title": title, "brief": brief, "status": "queued", "createdAt": now, "updatedAt": now,
                    "steps": [{**step, "id": uuid.uuid4().hex, "status": "queued", "attempts": 0}
                              for step in specification]}
            database.execute("INSERT INTO superagent_tasks VALUES(?,?,?,?,?,?,?)",
                             (task["id"], *scope, request_id, fingerprint, now, _encoded(task)))
        return task

    def get(self, task_id: str, *, actor_id: str, project_id: str) -> dict | None:
        task_id = _identifier(task_id, "任务标识")
        scope = self._scope(actor_id, project_id)
        with self._database() as database:
            task = self._read(database, task_id, scope)
        return _public(task) if task is not None else None

    def list(self, *, actor_id: str, project_id: str) -> list[dict]:
        scope = self._scope(actor_id, project_id)
        with self._database() as database:
            return [_public(json.loads(row[0])) for row in database.execute(
                "SELECT payload FROM superagent_tasks WHERE actor_id=? AND project_id=? ORDER BY updated_at DESC,id", scope)]

    def scheduler_candidates(self, limit: int = 128) -> list[dict]:
        """Host-only scope discovery; the scheduler must still authorize work."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise SuperAgentTaskError("无效的任务扫描上限", 422)
        now = self.now_ms()
        with self._database() as database:
            rows = database.execute("""
                WITH candidates AS (
                    SELECT id, actor_id, project_id, updated_at, payload, json_extract(payload,'$.status') AS state
                    FROM superagent_tasks
                )
                SELECT id, actor_id, project_id, state FROM candidates
                WHERE state='queued'
                   OR (state IN ('running','stopping') AND json_extract(payload,'$._lease.expiresAt') <= ?)
                   OR (state='waiting_input' AND json_extract(payload,'$.question.deadlineAt') <= ?)
                   OR (state='unconfirmed' AND EXISTS (
                       SELECT 1 FROM json_each(candidates.payload,'$.steps')
                       WHERE json_extract(value,'$.kind')='publish' AND json_extract(value,'$.status')='unconfirmed'
                   ))
                ORDER BY CASE WHEN state='unconfirmed' THEN 1 ELSE 0 END, updated_at, id LIMIT ?
            """, (now, now, limit))
            return [{"id": identifier, "actorId": actor, "projectId": project, "status": status}
                    for identifier, actor, project, status in rows]

    @staticmethod
    def _active(task, lease_token, now):
        lease = task.get("_lease")
        if (not lease or lease["token"] != lease_token or now >= lease["expiresAt"]
                or task["status"] not in ("running", "stopping")):
            raise SuperAgentTaskError("任务租约已失效")
        return task["steps"][lease["stepIndex"]]

    @staticmethod
    def _recover(task, now):
        lease = task.get("_lease")
        if lease is None or now < lease["expiresAt"]:
            return
        step = task["steps"][lease["stepIndex"]]
        del task["_lease"]
        if task["status"] == "running" and step["retrySafe"]:
            if step["attempts"] < _MAX_AUTO_LEASE_ATTEMPTS:
                task["status"] = step["status"] = "queued"
            else:
                task["status"] = step["status"] = "failed"
                step["error"] = {"code": "recovery_limit", "message": "自动恢复次数已达限制，可手动重试"}
        else:
            task["status"] = step["status"] = "unconfirmed"
            step["error"] = {"code": "lease_expired", "message": "原执行结果未确认，未重复运行"}

    @staticmethod
    def _settle_question(task, now, state, *, answer=None, source=None):
        question = task["question"]
        question.update(status=state, resolvedAt=now)
        if answer is not None:
            question.update(answer=answer, resolvedBy=source)
        step = next(step for step in task["steps"] if step["status"] == "waiting_input")
        step["question"] = dict(question)
        if state in ("answered", "defaulted"):
            task["status"] = step["status"] = "queued"
        elif state == "cancelled":
            task["status"] = step["status"] = "stopped"
        else:
            task["status"] = step["status"] = "failed"
            step["error"] = {"code": "input_expired", "message": "等待输入已过期，未执行需确认的操作"}

    @classmethod
    def _settle_due(cls, task, now):
        if task["status"] != "waiting_input" or now < task["question"]["deadlineAt"]:
            return
        question = task["question"]
        if "defaultAnswer" in question and not question["requiresHuman"]:
            cls._settle_question(task, now, "defaulted", answer=question["defaultAnswer"], source="default")
        else:
            cls._settle_question(task, now, "expired")

    def claim(self, task_id: str, worker_id: str, *, lease_ms: int = 30_000,
              actor_id: str, project_id: str) -> dict | None:
        worker = _identifier(worker_id, "执行者")
        duration = _lease_duration(lease_ms)
        with self._edit(task_id, actor_id, project_id) as (task, now):
            self._recover(task, now)
            self._settle_due(task, now)
            if task["status"] != "queued":
                return None
            index = next(index for index, step in enumerate(task["steps"]) if step["status"] == "queued")
            step = task["steps"][index]
            token = uuid.uuid4().hex
            task["_lease"] = {"token": token, "workerId": worker, "stepIndex": index, "expiresAt": now + duration}
            task["status"] = step["status"] = "running"
            step["attempts"] += 1
            step.pop("error", None)
        return {"task": _public(task), "step": step, "leaseToken": token}

    def renew(self, task_id: str, lease_token: str, *, lease_ms: int = 30_000,
              actor_id: str, project_id: str) -> dict:
        duration = _lease_duration(lease_ms)
        with self._edit(task_id, actor_id, project_id) as (task, now):
            self._active(task, lease_token, now)
            if task["status"] == "running":
                task["_lease"]["expiresAt"] = max(task["_lease"]["expiresAt"], now + duration)
        return _public(task)

    def checkpoint(self, task_id: str, lease_token: str, receipt: dict, *, actor_id: str, project_id: str) -> dict:
        receipt = _object(receipt, "步骤检查点")
        with self._edit(task_id, actor_id, project_id) as (task, now):
            step = self._active(task, lease_token, now)
            if task["status"] != "running":
                raise SuperAgentTaskError("任务正在停止")
            step["checkpoint"] = receipt
        return _public(task)

    def finish_step(self, task_id: str, lease_token: str, receipt: dict, *, actor_id: str, project_id: str) -> dict:
        receipt = _object(receipt, "步骤回执")
        with self._edit(task_id, actor_id, project_id) as (task, now):
            step = self._active(task, lease_token, now)
            step.update(status="completed", receipt=receipt, completedAt=now)
            del task["_lease"]
            task["status"] = ("stopped" if task["status"] == "stopping" else
                              "completed" if all(row["status"] == "completed" for row in task["steps"]) else "queued")
        return _public(task)

    def fail_step(self, task_id: str, lease_token: str, error: str | dict, *, actor_id: str, project_id: str) -> dict:
        detail = _object(error, "步骤错误") if isinstance(error, dict) else _text(error, 2000, "步骤错误")
        with self._edit(task_id, actor_id, project_id) as (task, now):
            step = self._active(task, lease_token, now)
            status = "failed" if step["retrySafe"] and task["status"] == "running" else "unconfirmed"
            task["status"] = step["status"] = status
            step["error"] = detail
            del task["_lease"]
        return _public(task)

    def ask(self, task_id: str, lease_token: str, question: dict, *, default_answer: str | None = None,
            deadline_ms: int, actor_id: str, project_id: str) -> dict:
        if not isinstance(question, dict):
            raise SuperAgentTaskError("无效的任务问题", 422)
        prompt = _text(question.get("prompt"), 4000, "问题内容")
        raw_options = question.get("options", [])
        if not isinstance(raw_options, list) or len(raw_options) > 20:
            raise SuperAgentTaskError("无效的问题选项", 422)
        options = [_text(option, 500, "问题选项") for option in raw_options]
        requires_human = question.get("requiresHuman", False)
        if type(requires_human) is not bool or len(set(options)) != len(options):
            raise SuperAgentTaskError("无效的问题选项", 422)
        policy_question = [{"questionId": "task-input", "question": prompt, "options": options,
                            "isSecret": question.get("isSecret") is True, "requiresHuman": requires_human}]
        if requests_secret(policy_question):
            raise SuperAgentTaskError("请在原生安全界面完成敏感凭证输入", 422)
        requires_human = question_policy(policy_question).requires_human
        if requires_human and default_answer is not None:
            raise SuperAgentTaskError("需要人工确认的问题不能使用默认答案", 422)
        if default_answer is not None:
            _text(default_answer, 2000, "默认答案")
            if options and default_answer not in options:
                raise SuperAgentTaskError("默认答案与选项不匹配", 422)
        with self._edit(task_id, actor_id, project_id) as (task, now):
            step = self._active(task, lease_token, now)
            if task["status"] != "running":
                raise SuperAgentTaskError("任务正在停止")
            if type(deadline_ms) is not int or not now < deadline_ms < 2**53:
                raise SuperAgentTaskError("无效的输入截止时间", 422)
            if old := step.get("question"):
                step.setdefault("previousQuestions", []).append(old)
            record = {"id": uuid.uuid4().hex, "prompt": prompt, "options": options,
                      "requiresHuman": requires_human, "deadlineAt": deadline_ms, "status": "pending", "createdAt": now}
            if default_answer is not None:
                record["defaultAnswer"] = default_answer
            step["question"] = task["question"] = record
            task["status"] = step["status"] = "waiting_input"
            del task["_lease"]
        return _public(task)

    def answer(self, task_id: str, question_id: str, answer: str, *, actor_id: str, project_id: str) -> dict:
        answer = _text(answer, 2000, "答案")
        with self._edit(task_id, actor_id, project_id) as (task, now):
            question = task.get("question")
            if not question or question["id"] != question_id:
                raise SuperAgentTaskError("问题已替换或不存在")
            self._settle_due(task, now)
            if question["status"] == "pending":
                if question["options"] and answer not in question["options"]:
                    raise SuperAgentTaskError("答案与问题选项不匹配", 422)
                self._settle_question(task, now, "answered", answer=answer, source="human")
            elif question["status"] == "answered" and question["answer"] != answer:
                raise SuperAgentTaskError("问题已由另一个答案确认")
        return _public(task)

    def settle_due(self, task_id: str, *, actor_id: str, project_id: str) -> dict:
        with self._edit(task_id, actor_id, project_id) as (task, now):
            self._recover(task, now)
            self._settle_due(task, now)
        return _public(task)

    def stop(self, task_id: str, *, actor_id: str, project_id: str) -> dict:
        with self._edit(task_id, actor_id, project_id) as (task, now):
            if task["status"] == "running":
                task["status"] = "stopping"
            self._recover(task, now)
            if task["status"] == "waiting_input":
                self._settle_question(task, now, "cancelled")
            elif task["status"] == "queued":
                task["status"] = "stopped"
        return _public(task)

    def ack_stop(self, task_id: str, lease_token: str, receipt: dict | None = None, *,
                 actor_id: str, project_id: str) -> dict:
        receipt = _object(receipt, "停止回执") if receipt is not None else None
        with self._edit(task_id, actor_id, project_id) as (task, now):
            step = self._active(task, lease_token, now)
            if task["status"] != "stopping":
                raise SuperAgentTaskError("任务未请求停止")
            task["status"] = step["status"] = "stopped"
            if receipt is not None:
                step["receipt"] = receipt
            del task["_lease"]
        return _public(task)

    def retry(self, task_id: str, *, actor_id: str, project_id: str) -> dict:
        with self._edit(task_id, actor_id, project_id) as (task, now):
            step = next((step for step in task["steps"] if step["status"] == "failed"), None)
            if task["status"] != "failed" or step is None or not step["retrySafe"]:
                raise SuperAgentTaskError("当前任务不能安全重试")
            task["status"] = step["status"] = "queued"
            error = step.get("error")
            returned_review = step["kind"] == "review" and isinstance(error, dict) and error.get("code") == "review_returned"
            if "question" in step and (step["question"]["status"] == "expired" or returned_review):
                step.setdefault("previousQuestions", []).append(step.pop("question"))
        return _public(task)

    def reconcile_step(self, task_id: str, step_id: str, receipt: dict, *, actor_id: str, project_id: str) -> dict:
        """Observe a durable approved publication receipt without sending anything."""
        if not isinstance(receipt, dict):
            raise SuperAgentTaskError("发布核对回执无效", 422)
        result, subject = receipt.get("result"), receipt.get("subject")
        if not isinstance(result, dict) or not isinstance(subject, dict):
            raise SuperAgentTaskError("发布结果未确认")
        platform, account = subject.get("platform"), subject.get("account")
        operation, digest = receipt.get("operationId"), receipt.get("payloadHash")
        external = _external_id(result.get("externalId"))
        mode = subject.get("mode")
        if (receipt.get("actorId") != actor_id or receipt.get("projectId") != project_id
                or not isinstance(platform, dict) or not isinstance(platform.get("id"), str) or not platform["id"]
                or not isinstance(account, dict) or not isinstance(account.get("id"), str) or not account["id"]
                or not isinstance(operation, str) or not re.fullmatch(r"[a-f0-9]{32}", operation)
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or mode not in ("publish_now", "scheduled") or external is None
                or any(result.get(key) != expected for key, expected in {
                    "operationId": operation, "payloadHash": digest, "mode": mode,
                    "platform": platform["id"], "accountId": account["id"],
                    "status": "confirmed", "phase": "readback", "publicationState": "public",
                    "evidenceMethod": "authenticated_readback",
                }.items())):
            raise SuperAgentTaskError("发布核对回执与原操作不匹配或未确认")
        with self._edit(task_id, actor_id, project_id) as (task, now):
            step = next((row for row in task["steps"] if row["id"] == step_id), None)
            if (step is None or step["kind"] != "publish" or not step["payload"].get("approvalId")
                    or step["payload"]["approvalId"] != receipt.get("id")
                    or any(value is not None and value != operation for value in (
                        step["payload"].get("operationId"), step.get("checkpoint", {}).get("operationId"),
                    ))):
                raise SuperAgentTaskError("发布核对回执不属于原任务步骤")
            proof = {"approvalId": receipt["id"], "operationId": operation, "payloadHash": digest,
                     "status": "confirmed", "externalId": external, "publicationState": "public",
                     "evidenceMethod": "authenticated_readback"}
            if step["status"] == "completed" and step.get("receipt") == proof:
                return _public(task)
            if task["status"] != "unconfirmed" or step["status"] != "unconfirmed":
                raise SuperAgentTaskError("当前任务不在待核对状态")
            step.update(status="completed", receipt=proof, completedAt=now)
            task["status"] = "completed" if all(row["status"] == "completed" for row in task["steps"]) else "queued"
        return _public(task)
