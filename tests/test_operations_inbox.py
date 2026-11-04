import importlib
from pathlib import Path

import pytest

from easel.interaction_center import InteractionStore, InteractionError
from easel.operations import OperationsManager


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_native_dm_import_retains_direction_time_and_remote_ids(tmp_path):
    store = InteractionStore(tmp_path / "outputs")
    customer = store.bootstrap()["client_id"]
    created = store.create_channel(
        customer,
        {
            "name": "native dm",
            "platform": "douyin",
            "kind": "dm",
            "delivery_mode": "native",
            "config": {"account_id": "1", "conversation_id": "conversation-one"},
        },
    )
    channel = created["channels"][0]
    manager = OperationsManager(tmp_path)
    runtime = await manager.get("default", start=False)
    with runtime.module.get_session() as db:
        db.add(runtime.module.DouyinAccount(id=1, platform="douyin"))
        db.add(
            runtime.module.DmConversation(
                account_id=1,
                platform="douyin",
                conv_id="conversation-one",
                peer_nickname="visitor",
            )
        )
        db.add(
            runtime.module.DmMessage(
                account_id=1,
                platform="douyin",
                conv_id="conversation-one",
                msg_id="remote-1",
                text="question",
                direction="in",
                create_time=1793750400,
            )
        )
        db.add(
            runtime.module.DmMessage(
                account_id=1,
                platform="douyin",
                conv_id="conversation-one",
                msg_id="remote-2",
                text="answer",
                direction="out",
                create_time=1793750401,
            )
        )
        db.commit()
    assert (Path(__file__).parents[1] / "easel/operations_inbox.py").exists(), (
        "Native messages are not connected to the reviewed inbox"
    )
    sync = importlib.import_module("easel.operations_inbox").sync_native_inbox
    for _ in range(2):
        result = await sync(manager, store, customer, channel["id"], refresh=False)
    conversation = store.conversation(customer, result["conversations"][0]["id"])
    assert [(m["text"], m["direction"]) for m in conversation["messages"]] == [
        ("question", "inbound"),
        ("answer", "outbound"),
    ]
    assert conversation["messages"][0]["external_id"].endswith(":remote-1")
    assert conversation["messages"][0]["created_at"] == "2026-11-04T00:00:00+00:00"
    assert conversation["revision"] == 2
    assert conversation["status"] == "handled"
    assert result["channels"][0]["last_sync_at"]
    other = store.create_client("other")["client_id"]
    with pytest.raises(InteractionError):
        await sync(manager, store, other, channel["id"], refresh=False)
    await manager.close()
