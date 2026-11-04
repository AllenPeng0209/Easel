"""Import current-IP platform messages into Easel's reviewed conversation store."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import select

from easel.interaction_center import InteractionError
from easel.projects import project_id


def native_capability(channel):
    configured = bool(channel.get("config", {}).get("account_id"))
    return {
        "state": "configured" if configured else "disconnected",
        "can_sync": configured,
        "can_send": False,
        "message": "同步该账号的评论与私信，回复可复制到平台处理。",
    }


async def sync_native_inbox(manager, store, client_id, channel_id, *, refresh=True):
    bootstrap = store.bootstrap(client_id)
    channel = next(
        (item for item in bootstrap["channels"] if item["id"] == channel_id), None
    )
    if channel is None or channel["delivery_mode"] != "native":
        raise InteractionError("内置通道不存在或不属于当前客户", 404)
    config = channel["config"]
    try:
        account_id = int(config["account_id"])
    except (KeyError, ValueError):
        raise InteractionError("请选择当前 IP 的平台账号") from None
    runtime = await manager.get(project_id(), start=refresh)
    module = runtime.module
    platform = {"xiaohongshu": "xhs", "douyin": "douyin"}[channel["platform"]]
    with module.get_session() as session:
        account = session.get(module.DouyinAccount, account_id)
        if account is None or account.platform != platform:
            raise InteractionError("账号不存在或与当前平台不匹配", 404)
        if channel["kind"] == "comment":
            work = session.exec(
                select(module.AccountWork).where(
                    module.AccountWork.account_id == account_id,
                    module.AccountWork.platform == platform,
                    module.AccountWork.item_id == config.get("video_id", ""),
                )
            ).first()
            if work is None:
                raise InteractionError("请选择该账号已同步的自有作品", 404)
            work_id, item_id, media_type, subject = (
                work.id,
                work.item_id,
                work.media_type,
                work.desc,
            )
        else:
            conversation = session.exec(
                select(module.DmConversation).where(
                    module.DmConversation.account_id == account_id,
                    module.DmConversation.platform == platform,
                    module.DmConversation.conv_id == config.get("conversation_id", ""),
                )
            ).first()
            if conversation is None:
                raise InteractionError("该账号没有所选平台会话", 404)
            conversation_id, author = conversation.conv_id, conversation.peer_nickname
    if refresh:
        result = (
            await module.sync_work_comments(work_id)
            if channel["kind"] == "comment"
            else await module.sync_dm(account_id)
        )
        if result.get("skipped") or not result.get("ok", True):
            raise InteractionError(
                "平台同步已暂缓，请查看账号状态和任务记录后重试", 503
            )
    items = []
    with module.get_session() as session:
        if channel["kind"] == "comment":
            rows = session.exec(
                select(module.CommentRecord)
                .where(
                    module.CommentRecord.watch_id == 0,
                    module.CommentRecord.platform == platform,
                    module.CommentRecord.aweme_id == item_id,
                )
                .order_by(module.CommentRecord.id.desc())
                .limit(200)
            ).all()
            source = (
                "https://www.xiaohongshu.com/explore/" + item_id
                if platform == "xhs"
                else f"https://www.douyin.com/{'note' if media_type == 'images' else 'video'}/{item_id}"
            )
            for row in reversed(rows):
                if row.text.strip():
                    items.append(
                        {
                            "external_id": f"native:{platform}:{account_id}:{item_id}:{row.comment_id}",
                            "text": row.text,
                            "author_name": row.user_nickname,
                            "source_url": source,
                            "subject": subject[:300],
                            "created_at": datetime.fromtimestamp(
                                row.create_time, timezone.utc
                            ).isoformat()
                            if row.create_time
                            else "",
                        }
                    )
        else:
            rows = session.exec(
                select(module.DmMessage)
                .where(
                    module.DmMessage.account_id == account_id,
                    module.DmMessage.platform == platform,
                    module.DmMessage.conv_id == conversation_id,
                )
                .order_by(
                    module.DmMessage.create_time.desc(), module.DmMessage.id.desc()
                )
                .limit(200)
            ).all()
            for row in reversed(rows):
                if row.text.strip() and row.msg_id and row.direction in {"in", "out"}:
                    items.append(
                        {
                            "external_id": f"native:{platform}:{account_id}:{conversation_id}:{row.msg_id}",
                            "conversation_external_id": conversation_id,
                            "text": row.text,
                            "author_name": author
                            if row.direction == "in"
                            else "本账号",
                            "direction": "inbound"
                            if row.direction == "in"
                            else "outbound",
                            "created_at": datetime.fromtimestamp(
                                row.create_time, timezone.utc
                            ).isoformat()
                            if row.create_time
                            else "",
                        }
                    )
    return store.sync_channel(
        client_id,
        channel_id,
        sync_adapter=lambda *_args, **_kwargs: items,
        capabilities=native_capability,
    )
