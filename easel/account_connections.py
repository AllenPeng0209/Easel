"""One owned login flow, with independent read and creator verification."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from urllib.parse import urlsplit
import uuid

from fastapi import HTTPException
from sqlmodel import select

from easel import account_sessions


class _Unverified(ValueError):
    pass


class _Mismatch(_Unverified):
    pass


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    value = str(value).strip()
    return (
        value
        if value
        and value != "0"
        and len(value) <= 256
        and all(ord(c) > 32 and ord(c) != 127 for c in value)
        else ""
    )


def _subjects(platform, profile):
    if platform == "xhs":
        value = _identifier(
            profile.get("user_id") or profile.get("userId") or profile.get("sec_uid")
        )
        return {"user_id": value} if value else {}
    return {
        name: value
        for name in ("uid", "sec_uid")
        if (value := _identifier(profile.get(name)))
    }


def _same(expected, observed):
    common = expected.keys() & observed.keys()
    return bool(common) and all(expected[key] == observed[key] for key in common)


def _capabilities(read=False, creator=False, dm=False):
    return {
        name: "verified" if enabled else "needs_verification"
        for name, enabled in (
            ("comments", read),
            ("dm", dm),
            ("creator", creator),
            ("publish", creator),
            ("analytics", creator),
        )
    }


def _state(value, platform):
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise _Unverified("平台登录快照未完成")
    suffix = "douyin.com" if platform == "douyin" else "xiaohongshu.com"

    def allowed(host):
        host = str(host or "").lower().lstrip(".")
        return host == suffix or host.endswith("." + suffix)

    return {
        "cookies": [
            deepcopy(item)
            for item in parsed.get("cookies", [])
            if isinstance(item, dict) and allowed(item.get("domain"))
        ],
        "origins": [
            deepcopy(item)
            for item in parsed.get("origins", [])
            if isinstance(item, dict)
            and allowed(urlsplit(str(item.get("origin", ""))).hostname)
        ],
    }


class AccountConnections:
    def __init__(self, runtime):
        self.runtime = runtime
        self._records = {}
        self._runners = {}
        self._remote_finalizers = set()
        self._lock = asyncio.Lock()
        self._closed = False

    @staticmethod
    def _platform(platform):
        platform = "xhs" if platform == "xiaohongshu" else platform
        if platform not in {"douyin", "xhs"}:
            raise HTTPException(400, "该平台请使用原有账号登录")
        return platform

    def _account(self, account_id, platform):
        module = self.runtime.module
        with module.get_session() as db:
            account = db.get(module.DouyinAccount, account_id)
            if account is None or account.platform != platform:
                raise HTTPException(404, "账号不存在或不属于当前平台")
            db.expunge(account)
            return account

    async def start(self, platform, account_id=None, *, new_account=False, actor_id=None, authorize=None):
        platform = self._platform(platform)
        actor_id = str(actor_id or "")
        async with self._lock:
            if authorize is not None:
                authorize()
            if self._closed or self.runtime.module.browser is None:
                raise HTTPException(503, "账号连接服务尚未就绪")
            if account_id is not None:
                self._account(account_id, platform)
            for identifier, runner in self._runners.items():
                if not runner.done():
                    record = self._records[identifier]
                    if record.get("_actor_id", "") != actor_id:
                        raise HTTPException(409, "另一位负责人正在连接账号，请等待其完成或取消")
                    if (
                        record["platform"] == platform
                        and record.get("_new_account") == bool(new_account)
                        and (
                            account_id is None or record.get("account_id") == account_id
                        )
                    ):
                        return self.poll(identifier, actor_id=actor_id)
                    raise HTTPException(409, "请先完成当前账号的登录验证")
            if self.runtime.module._active_login_task() is not None:
                raise HTTPException(409, "请先完成当前账号的登录验证")
            if account_id is None and not new_account:
                saved = account_sessions.load_session(self.runtime.workspace, platform)
                if saved and saved.get("account_id"):
                    try:
                        account_id = self._account(saved["account_id"], platform).id
                    except HTTPException:
                        pass
                if account_id is None:
                    module = self.runtime.module
                    with module.get_session() as db:
                        accounts = db.exec(
                            select(module.DouyinAccount).where(
                                module.DouyinAccount.platform == platform
                            )
                        ).all()
                        if len(accounts) == 1:
                            account_id = accounts[0].id
            identifier = uuid.uuid4().hex
            record = {
                "task_id": identifier,
                "platform": platform,
                "account_id": account_id,
                "status": "opening",
                "stage": "read",
                "hint": "正在连接账号并检查可用功能。",
                "capabilities": _capabilities(),
                "loginSaved": False,
                "can_send": False,
                "_leaves": [],
                "_new_account": bool(new_account),
                "_actor_id": actor_id,
                "_authorize": authorize,
            }
            if actor_id:
                from easel.login_remote import enabled, registry

                if enabled():
                    def active():
                        return (self._records.get(identifier) is record
                                and record["status"] not in {"confirmed", "error", "expired"}
                                and self._active(record))

                    def cancel():
                        self._stop_login(record)

                    async def closed():
                        runner = self._runners.get(identifier)
                        if runner is not None and runner is not asyncio.current_task():
                            await asyncio.gather(runner, return_exceptions=True)

                    record["_remote"] = registry.create(
                        self.runtime.project, actor_id, platform, identifier,
                        is_active=active, cancel=cancel, on_close=closed,
                    )
                    record["_remote_deadline"] = time.monotonic() + 600
            # Reserve browser capacity before invalidating a verified session.
            # No await separates this reservation from the generation and task.
            try:
                record["_generation"] = account_sessions.begin_generation(
                    self.runtime.workspace, platform
                )
                self._records[identifier] = record
                runner = asyncio.create_task(self._run(identifier))
                self._runners[identifier] = runner
            except BaseException:
                self._records.pop(identifier, None)
                if record.get("_remote"):
                    with suppress(Exception):
                        await record["_remote"].stop(cancel=False)
                raise
            if record.get("_remote"):
                def finish_remote(_runner):
                    task = asyncio.create_task(record["_remote"].stop(cancel=False))
                    self._remote_finalizers.add(task)

                    def finished(finalizer):
                        self._remote_finalizers.discard(finalizer)
                        if not finalizer.cancelled():
                            finalizer.exception()

                    task.add_done_callback(finished)

                # A session finalizer may wait for this runner. Start it only
                # after the runner's browser/profile cleanup has completed.
                runner.add_done_callback(finish_remote)
            return self.poll(identifier, actor_id=actor_id)

    def _stop_login(self, record):
        # Closing the interactive window ends optional verification. A main-site
        # identity already proved and saved is not a logout operation.
        active = self._active(record)
        if active and record.get("_mismatch"):
            checkpoint = bool(record.get("loginSaved"))
            removed = (not checkpoint or account_sessions.revoke_session(
                self.runtime.workspace, record["platform"], expected_generation=record["_generation"]))
            record.update(loginSaved=False, status="error", stage="identity_mismatch",
                          capabilities=_capabilities(),
                          _invalidate_mismatch_generation=(record["_generation"] + int(checkpoint)) if removed else None,
                          hint="主站与内容后台账号不一致，请使用同一账号完成验证。")
        saved = bool(record.get("loginSaved") and not record.get("_mismatch") and active)
        record["_revoked"] = True
        if record["status"] not in {"confirmed", "error", "expired"}:
            if saved:
                record.update(status="confirmed", stage="complete",
                              hint="主站登录已保存；未完成的功能可稍后继续验证。")
            else:
                record.update(status="error", stage="canceled", hint="本次账号连接已取消。")
        runner = self._runners.get(record["task_id"])
        if runner is not None and not runner.done() and not runner.cancelling():
            runner.cancel()

    def _save_read_checkpoint(self, record, expected, snapshot, *, creator=False):
        if not self._active(record):
            raise _Unverified("该连接已取消")
        name = ("user_id" if record["platform"] == "xhs"
                else "uid" if "uid" in expected else "sec_uid")
        identity = {"accountId": expected[name], "accountIdKind": name,
                    "accountIdentityStatus": "verified", **expected}
        capabilities = _capabilities(True, creator)
        module = self.runtime.module
        with module.get_session() as db:
            account = db.get(module.DouyinAccount, record["account_id"])
            if account is None or account.platform != record["platform"] or not self._active(record):
                raise _Unverified("该连接已取消")
            account.status = "active"
            account.storage_state = json.dumps(snapshot)
            if creator:
                account.creator_storage_state = json.dumps(snapshot)
            db.add(account)
            db.commit()
        account_sessions.save_session(self.runtime.workspace, record["platform"], record["account_id"],
            snapshot, identity=identity, capabilities=capabilities,
            expected_generation=record["_generation"])
        record.update(loginSaved=True, capabilities=capabilities,
                      completion="full" if creator else "partial")

    def poll(self, task_id, *, actor_id=None):
        record = self._records.get(task_id)
        if record is None or record.get("_actor_id", "") != str(actor_id or ""):
            raise HTTPException(404, "连接任务不存在或不属于当前 IP")
        result = {
            key: value for key, value in record.items() if not key.startswith("_")
        }
        if record["status"] not in {"confirmed", "error", "expired"} and record.get(
            "_leaf"
        ):
            state = self.runtime.module.login_tasks.get(record["_leaf"], {})
            if state.get("status") in {
                "opening",
                "waiting",
                "verification",
                "persisted",
            }:
                result["status"] = state["status"]
                if state["status"] == "verification":
                    result["hint"] = "请在当前平台窗口完成账号安全验证。"
        if record.get("_remote") and record["status"] not in {"confirmed", "error", "expired"}:
            result["remote"] = record["_remote"].public()
        return deepcopy(result)

    def _active(self, record):
        if self._closed or record.get("_revoked"):
            return False
        if record.get("_authorize") is not None:
            try:
                record["_authorize"]()
            except Exception:
                record.update(_revoked=True, status="error", stage="authorization",
                              hint="登录会话或账号管理权限已变化，请重新登录后连接账号。")
                runner = self._runners.get(record["task_id"])
                if runner is not None and not runner.done() and runner is not asyncio.current_task():
                    runner.cancel()
                return False
        return (
            account_sessions.session_generation(
                self.runtime.workspace, record["platform"]
            )
            == record["_generation"]
        )

    async def _read_proof(self, platform, identity):
        module = self.runtime.module
        function = (
            module.fetch_xhs_self_profile
            if platform == "xhs"
            else module.fetch_self_profile
        )
        profile, error = await function(module.browser, identity)
        if error or not isinstance(profile, dict) or not _subjects(platform, profile):
            raise _Unverified("主站身份尚未核实")
        normalized = (
            module.parse_xhs_self_user(profile)
            if platform == "xhs"
            else module.parse_self_user(profile)
        )
        if platform == "douyin" and _identifier(profile.get("uid")):
            normalized["uid"] = _identifier(profile["uid"])
        return normalized

    async def _creator_proof(self, platform, identity):
        context = await self.runtime.module.browser.context_for(identity)
        url = (
            "https://creator.xiaohongshu.com/api/galaxy/user/info"
            if platform == "xhs"
            else "https://creator.douyin.com/web/api/media/user/info/?aid=1128"
        )
        response = await context.request.get(
            url,
            headers={
                "Cache-Control": "no-cache",
                "Referer": urlsplit(url)._replace(path="/", query="").geturl(),
            },
            timeout=8000,
        )
        if response.status != 200:
            raise _Unverified("创作后台身份尚未核实")
        payload = await response.json()
        if platform == "xhs":
            profile = (
                payload.get("data")
                if isinstance(payload, dict) and payload.get("success") is True
                else None
            )
            if not isinstance(profile, dict) or not _identifier(profile.get("userId")):
                raise _Unverified("创作后台身份尚未核实")
            return {
                "sec_uid": _identifier(profile["userId"]),
                "nickname": profile.get("userName") or "",
            }
        profile = (
            (payload.get("user_info") or payload.get("user"))
            if isinstance(payload, dict)
            and type(payload.get("status_code")) is int
            and payload["status_code"] == 0
            else None
        )
        if not isinstance(profile, dict) or not _subjects(platform, profile):
            raise _Unverified("创作后台身份尚未核实")
        return {
            key: value
            for key in ("uid", "sec_uid")
            if (value := _identifier(profile.get(key)))
        }

    async def _install_candidate(self, platform, identity, snapshot):
        # Login adapters may close their Chromium context before returning.
        # Reopen from this candidate, then replace all authentication material
        # before asking either first-party identity endpoint for proof.
        identity.bridge_states = (json.dumps(snapshot),)
        context = await self.runtime.module.browser.context_for(identity)
        restore = getattr(context, "set_storage_state", None)
        if not callable(restore):
            raise _Unverified("浏览器暂不支持完整登录恢复，请更新后验证")
        await restore(snapshot)

    async def _phase(self, record, *, creator, expected, snapshots):
        module = self.runtime.module
        leaf = uuid.uuid4().hex
        record.update(
            stage="creator" if creator else "read",
            _leaf=leaf,
            hint="正在检查创作后台；如平台要求，请在同一窗口补充验证。"
            if creator
            else "请在平台窗口登录，完成后将自动检查可用功能。",
        )
        record["_leaves"].append(leaf)
        proof = {}

        async def before_persist(candidate):
            if not self._active(record):
                raise _Unverified("该连接已取消")
            snapshot = _state(candidate["state"], record["platform"])
            await self._install_candidate(
                record["platform"], candidate["identity"], snapshot
            )
            function = self._creator_proof if creator else self._read_proof
            profile = await function(record["platform"], candidate["identity"])
            subject = _subjects(record["platform"], profile)
            if expected and not _same(expected, subject):
                record["_mismatch"] = True
                raise _Mismatch("登录账号身份不匹配")
            if not self._active(record):
                raise _Unverified("该连接已取消")
            proof.update(profile=profile, subject=subject, state=snapshot)
            return profile

        remote_options = {}
        proxy_choice = ""
        if record.get("_remote"):
            remaining = record["_remote_deadline"] - time.monotonic()
            if remaining <= 0:
                raise _Unverified("本次账号连接已超时，请重新登录")
            remote_options = {
                "context_callback": record["_remote"].bind_context,
                "timeout_seconds": max(1, min(600, int(remaining))),
            }
            if record["platform"] == "xhs":
                # Server-owned opt-in only. The vendor preserves this network
                # on the new account identity for the creator-stage hand-off.
                proxy_choice = os.environ.get("EASEL_XHS_LOGIN_PROXY", "").strip()
        await module._run_login(
            leaf,
            platform=record["platform"],
            account_id=record.get("account_id"),
            creator=creator,
            proxy_choice=proxy_choice,
            browser_backend="local",
            force_reauth=False,
            before_persist=before_persist,
            **remote_options,
        )
        if not self._active(record):
            raise _Unverified("该连接已取消")
        state = module.login_tasks.get(leaf, {})
        if state.get("status") != "confirmed" or not proof:
            raise _Unverified("平台验证尚未完成")
        record["account_id"] = state["account_id"]
        snapshots.append(proof["state"])
        return proof["subject"]

    async def _dm_proof(self, record, identity):
        module = self.runtime.module
        if module.engine is None:
            return False

        async def operation():
            return await module.fetch_dm_conversations(
                module.browser, identity, record["platform"]
            )

        try:
            conversations, error = await asyncio.wait_for(
                module.engine.guarded_read_pair(
                    record["account_id"],
                    module.OperationKind.READ_HEAVY,
                    f"account-connect-dm:{record['account_id']}",
                    operation,
                    empty_result=[],
                ),
                timeout=20,
            )
            # The synchronization convenience API invokes XHS automation and
            # may enqueue writes. This probe only reads conversation metadata.
            return isinstance(conversations, list) and not error
        except Exception:
            return False

    async def _run(self, identifier):
        record = self._records[identifier]
        snapshots, expected = [], {}
        read_ok = creator_ok = dm_ok = False
        try:
            if record.get("account_id"):
                account = self._account(record["account_id"], record["platform"])
                expected = _subjects(
                    record["platform"], {"uid": account.uid, "sec_uid": account.sec_uid}
                )
                if account.storage_state:
                    identity = self.runtime.module.browser.identity_for(account)
                    try:
                        profile = await self._read_proof(record["platform"], identity)
                        observed = _subjects(record["platform"], profile)
                        if expected and not _same(expected, observed):
                            raise _Mismatch("登录账号身份不匹配")
                        expected = observed
                        context = await self.runtime.module.browser.context_for(
                            identity
                        )
                        snapshots.append(
                            _state(await context.storage_state(), record["platform"])
                        )
                        read_ok = True
                        try:
                            creator = _subjects(
                                record["platform"],
                                await self._creator_proof(record["platform"], identity),
                            )
                            if not _same(expected, creator):
                                raise _Mismatch("登录账号身份不匹配")
                            context = await self.runtime.module.browser.context_for(
                                identity
                            )
                            snapshots.append(
                                _state(
                                    await context.storage_state(), record["platform"]
                                )
                            )
                            creator_ok = True
                        except _Mismatch:
                            record["_mismatch"] = True
                        except Exception:
                            pass
                    except _Mismatch:
                        record["_mismatch"] = True
                    except Exception:
                        pass
            if not read_ok and not record.get("_mismatch"):
                expected = await self._phase(
                    record, creator=False, expected=expected, snapshots=snapshots
                )
                read_ok = True
            if read_ok and not record.get("_mismatch"):
                self._save_read_checkpoint(record, expected, snapshots[-1], creator=creator_ok)
            if read_ok and not creator_ok and not record.get("_mismatch"):
                try:
                    await self._phase(
                        record, creator=True, expected=expected, snapshots=snapshots
                    )
                    creator_ok = True
                    self._save_read_checkpoint(record, expected, snapshots[-1], creator=True)
                except _Unverified:
                    pass
            if not self._active(record):
                return
            if record.get("_mismatch"):
                raise _Mismatch("登录账号身份不匹配")
            if not read_ok:
                raise _Unverified("主站账号尚未验证")
            if record.get("_mismatch"):
                await self._invalidate(record["account_id"], clear=False)
                read_ok = creator_ok = False
            else:
                account = self._account(record["account_id"], record["platform"])
                identity = self.runtime.module.browser.identity_for(account)
                if creator_ok:
                    record.update(
                        stage="dm", hint="账号登录已核实，正在检查私信是否可读取。"
                    )
                    dm_ok = await self._dm_proof(record, identity)
                try:
                    final = _subjects(
                        record["platform"],
                        await self._read_proof(record["platform"], identity),
                    )
                    if not _same(expected, final):
                        raise _Mismatch("登录账号身份不匹配")
                    if creator_ok:
                        context = await self.runtime.module.browser.context_for(
                            identity
                        )
                        snapshots.append(
                            _state(await context.storage_state(), record["platform"])
                        )
                except _Mismatch:
                    record["_mismatch"] = True
                    read_ok = creator_ok = dm_ok = False
                    raise
                except Exception:
                    read_ok = False
                    if self._active(record):
                        await self._invalidate(record["account_id"], clear=False)
            if not self._active(record):
                return
            capabilities = _capabilities(read_ok, creator_ok, dm_ok)
            name = (
                "user_id"
                if record["platform"] == "xhs"
                else "uid"
                if "uid" in expected
                else "sec_uid"
            )
            identity = {
                "accountId": expected[name],
                "accountIdKind": name,
                "accountIdentityStatus": "verified",
                **expected,
            }
            # storage_state() is a complete, current browser snapshot. Unioning
            # prior snapshots would resurrect cookies the provider removed.
            merged = deepcopy(snapshots[-1])
            module = self.runtime.module
            with module.get_session() as db:
                account = db.get(module.DouyinAccount, record["account_id"])
                if (
                    account is None
                    or account.platform != record["platform"]
                    or not self._active(record)
                ):
                    raise _Unverified("该连接已取消")
                if read_ok:
                    account.storage_state = json.dumps(merged)
                if creator_ok:
                    account.creator_storage_state = json.dumps(merged)
                db.add(account)
                db.commit()
            account_sessions.save_session(
                self.runtime.workspace,
                record["platform"],
                record["account_id"],
                merged,
                identity=identity,
                capabilities=capabilities,
                expected_generation=record["_generation"],
            )
            record.update(
                status="confirmed",
                loginSaved=read_ok,
                stage="complete",
                completion="full" if read_ok and creator_ok else "partial",
                capabilities=capabilities,
                hint="账号已连接，可用功能已自动检查；平台发送仍按权限处理。"
                if read_ok and creator_ok
                else "账号已连接，部分功能仍需在同一账号下补充验证。",
            )
        except asyncio.CancelledError:
            invalidation_generation = record.get("_invalidate_mismatch_generation")
            if (invalidation_generation is not None and record.get("account_id")
                    and account_sessions.session_generation(self.runtime.workspace, record["platform"])
                    == invalidation_generation):
                await self._invalidate(record["account_id"], clear=False)
            if record["status"] not in {"confirmed", "error", "expired"}:
                record.update(
                    status="error", stage="canceled", hint="本次账号连接已取消。"
                )
            raise
        except _Mismatch:
            active = self._active(record)
            if active:
                checkpoint = bool(record.get("loginSaved"))
                removed = (not checkpoint or account_sessions.revoke_session(
                    self.runtime.workspace, record["platform"], expected_generation=record["_generation"]))
                record.update(loginSaved=False, status="error", stage="identity_mismatch",
                              capabilities=_capabilities(),
                              hint="主站与内容后台账号不一致，请使用同一账号完成验证。")
                if (removed and record.get("account_id")
                        and account_sessions.session_generation(self.runtime.workspace, record["platform"])
                        == record["_generation"] + int(checkpoint)):
                    await self._invalidate(record["account_id"], clear=False)
        except Exception:
            if self._active(record):
                record.update(
                    status="error",
                    stage="verification",
                    hint="未能确认账号登录，请在平台窗口完成验证后重试。",
                )
        finally:
            record.pop("_leaf", None)
            self._settle_leaves(record)
            with suppress(Exception):
                await self._cleanup_unbound(record)

    def _settle_leaves(self, record):
        for identifier in record["_leaves"]:
            state = self.runtime.module.login_tasks.get(identifier)
            if state and state.get("status") in {
                "opening",
                "waiting",
                "verification",
                "persisted",
            }:
                state.update(status="error", error="账号连接已结束")

    async def _cleanup_unbound(self, record):
        module = self.runtime.module
        with module.get_session() as db:
            bound = {
                str(Path(account.profile_dir).resolve())
                for account in db.exec(select(module.DouyinAccount)).all()
                if account.profile_dir
            }
        for identifier in record["_leaves"]:
            state = module.login_tasks.get(identifier, {})
            environment = state.get("environment") or {}
            path = self._owned_profile(environment.get("profile_dir", ""))
            if path is None or str(path.resolve()) in bound:
                continue
            digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
            await module.browser.close_context(f"_anon:{digest}")
            if path.exists():
                shutil.rmtree(path)

    def _owned_profile(self, value):
        owned = self.runtime.directory / "profiles"
        if owned.is_symlink():
            return None
        root = owned.resolve()
        path = Path(value)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.is_symlink()
            or path.resolve() == root
            or not path.resolve().is_relative_to(root)
        ):
            return None
        if any(
            parent.is_symlink()
            for parent in (path, *path.parents)
            if parent != root and parent.is_relative_to(root)
        ):
            return None
        return path

    async def _invalidate(self, account_id, *, clear):
        module = self.runtime.module
        with module.get_session() as db:
            account = db.get(module.DouyinAccount, account_id)
            if account is None:
                return
            profile = account.profile_dir
            account.status = "invalid"
            if clear:
                account.storage_state = account.creator_storage_state = (
                    account.cookie
                ) = ""
            db.add(account)
            db.commit()
        receiver = getattr(module, "im_receiver", None)
        if receiver is not None:
            await receiver.stop_account(account_id)
        await module._close_runtime_accounts([account_id])
        if (
            clear
            and (path := self._owned_profile(profile)) is not None
            and path.exists()
        ):
            shutil.rmtree(path)
            path.mkdir(mode=0o700)

    async def revoke_account(self, account_id, *, authorize=None):
        """Exit one saved account without revoking a different content account.

        A platform-wide generation is changed only for the selected shared
        session (or its own unfinished login with no shared session). Other
        account records and their browsers remain owned by their original IDs.
        """
        if type(account_id) is not int or account_id < 1:
            raise HTTPException(404, "账号不存在或不属于当前 IP")
        async with self._lock:
            if authorize is not None:
                authorize()
            module = self.runtime.module
            with module.get_session() as db:
                account = db.get(module.DouyinAccount, account_id)
                if account is None:
                    raise HTTPException(404, "账号不存在或不属于当前 IP")
                platform = "xhs" if account.platform == "xiaohongshu" else account.platform
                native_subject = {"uid": account.uid, "sec_uid": account.sec_uid}
            managed = platform in {"douyin", "xhs"}
            shared = account_sessions.load_session(self.runtime.workspace, platform) if managed else None
            shared_active = bool(shared and shared.get("account_id") == account_id)
            if shared_active:
                from easel.account_directory import matching_subjects, platform_key, subjects

                namespace = platform_key(platform)
                expected = subjects(namespace, shared.get("identity"), require_verified=True)
                actual = subjects(namespace, native_subject)
                if not matching_subjects(expected, actual):
                    raise HTTPException(409, "账号身份存在冲突或尚未核实，请先检查登录后再退出")
            pending = []
            for identifier, record in self._records.items():
                runner = self._runners.get(identifier)
                if record["platform"] != platform or runner is None or runner.done():
                    continue
                if record.get("account_id") != account_id:
                    # A fresh login can still reconcile into an existing XHS
                    # ID. Do not cancel it or clear an account underneath it.
                    raise HTTPException(409, "该平台有其他账号正在登录，请完成后再退出此账号")
                pending.append(runner)
            fence_session = managed and (shared_active or bool(pending and shared is None))
            if fence_session:
                account_sessions.begin_generation(self.runtime.workspace, platform)
            for identifier, record in self._records.items():
                if record["platform"] != platform or record.get("account_id") != account_id:
                    continue
                record.update(
                    _revoked=True, status="error", stage="canceled",
                    hint="该账号已退出。", capabilities=_capabilities(), loginSaved=False,
                )
                runner = self._runners.get(identifier)
                if runner is not None and not runner.done():
                    runner.cancel()
            # Persist the revocation before waiting for any browser boundary.
            with module.get_session() as db:
                account = db.get(module.DouyinAccount, account_id)
                if account is not None:
                    account.status = "invalid"
                    account.storage_state = account.creator_storage_state = account.cookie = ""
                    db.add(account)
                    db.commit()
            await asyncio.gather(*pending, return_exceptions=True)
            # begin_generation already removed the selected snapshot before
            # awaiting the browser. Do not revoke again here: another process
            # may have committed a newer, different account in the meantime.
            await self._invalidate(account_id, clear=True)
            return {"account_id": account_id, "platform": platform, "shared_active": shared_active}

    async def revoke(self, platform):
        platform = self._platform(platform)
        async with self._lock:
            account_sessions.begin_generation(self.runtime.workspace, platform)
            runners = []
            for identifier, record in self._records.items():
                if record["platform"] == platform:
                    record.update(
                        _revoked=True,
                        status="error",
                        stage="canceled",
                        hint="账号连接已退出。",
                        capabilities=_capabilities(),
                        loginSaved=False,
                    )
                    runner = self._runners[identifier]
                    if not runner.done():
                        runner.cancel()
                        runners.append(runner)
            module = self.runtime.module
            with module.get_session() as db:
                accounts = db.exec(
                    select(module.DouyinAccount).where(
                        module.DouyinAccount.platform == platform
                    )
                ).all()
                identifiers = [account.id for account in accounts]
                for account in accounts:
                    account.status = "invalid"
                    account.storage_state = account.creator_storage_state = (
                        account.cookie
                    ) = ""
                    db.add(account)
                db.commit()
            await asyncio.gather(*runners, return_exceptions=True)
            account_sessions.revoke_session(self.runtime.workspace, platform)
            for account_id in identifiers:
                await self._invalidate(account_id, clear=True)

    async def close(self):
        self._closed = True
        for runner in self._runners.values():
            if not runner.done():
                runner.cancel()
        await asyncio.gather(*self._runners.values(), return_exceptions=True)
        await asyncio.gather(*tuple(self._remote_finalizers), return_exceptions=True)
