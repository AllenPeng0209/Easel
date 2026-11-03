"""Fault injection for the append-only chat replay log (no model or network)."""
import asyncio
import json

import pytest
from fastapi import HTTPException

import web.app as web
from web.chat_history import ChatHistoryStore


def encoded(event):
    return json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"


@pytest.fixture
def event_log(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "SESSIONS_DIR", tmp_path)
    path = web._job_event_file("recovery-turn")
    path.parent.mkdir()
    return path


@pytest.mark.parametrize("bad_record", [
    b'{"id":2,"event":',
    b'{"id":2,"data":"\xe4\xb8',  # interrupted UTF-8 character
    b"not-json",
    b"null",
    b"[]",
    b'{"id":null,"event":"token"}',
    b'{"id":"invalid","event":"token"}',
    b'{"id":2}',
    b'{"id":true,"event":"token"}',
    b'{"id":2.5,"event":"token"}',
    b'{"id":2,"event":null}',
])
def test_damaged_record_preserves_surrounding_events(event_log, bad_record):
    first = {"id": 1, "event": "token", "data": "已完成第一步"}
    done = {"id": 3, "event": "done", "data": {"sessionKey": "test"}}
    event_log.write_bytes(encoded(first) + bad_record + b"\n" + encoded(done))
    original = event_log.read_bytes()

    assert web._read_job_events("recovery-turn") == [first, done]
    assert web._read_job_events("recovery-turn", after=1) == [done]
    assert event_log.read_bytes() == original


def test_incomplete_tail_is_retried_after_append(event_log):
    first = {"id": 1, "event": "token", "data": "开始"}
    second = {"id": 2, "event": "token", "data": "中"}
    record = encoded(second)
    split = record.index("中".encode("utf-8")) + 1
    event_log.write_bytes(encoded(first) + record[:split])
    assert web._read_job_events("recovery-turn") == [first]

    with event_log.open("ab") as stream:
        stream.write(record[split:])
    assert web._read_job_events("recovery-turn", after=1) == [second]


def test_replay_reaches_terminal_event_after_corrupt_record(event_log):
    done = {"id": 2, "event": "done", "data": {"sessionKey": "test"}}
    event_log.write_bytes(b"broken\n" + encoded(done))

    async def replay():
        response = await web.api_chat_job_stream("recovery-turn")
        event = await asyncio.wait_for(
            anext(response.body_iterator), timeout=1,
        )
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)
        return event

    assert asyncio.run(replay()) == {
        "id": "2", "event": "done", "data": json.dumps(done["data"]),
    }


def test_missing_log_returns_empty(event_log):
    assert web._read_job_events("recovery-turn") == []


def claim_recovery_turn(event_log, *events):
    store = ChatHistoryStore(web._sessions_dir())
    store.claim_turn("recovery-turn", "session", "hash", event_log)
    event_log.write_bytes(b"".join(encoded(event) for event in events))
    return store.turn_claim("recovery-turn")


def test_dead_nonterminal_claim_rejects_get_and_same_request_post_replay(event_log, monkeypatch):
    monkeypatch.setattr(web, "_ACTIVE_TURN_IDS", set())
    claim = claim_recovery_turn(event_log, {"id": 1, "event": "activity", "data": "received"})

    async def scenario():
        with pytest.raises(HTTPException) as error:
            await web.api_chat_job_stream("recovery-turn")
        assert error.value.status_code == 404
        with pytest.raises(HTTPException) as error:
            await web._replay_claimed_turn(claim, "session", "hash", "recovery-turn")
        assert error.value.status_code == 409

    asyncio.run(scenario())


def test_admission_can_be_replayed_after_owner_loss_before_terminal(event_log, monkeypatch):
    monkeypatch.setattr(web, "_ACTIVE_TURN_IDS", set())
    admitted = {"turnId": "recovery-turn", "sessionId": "session"}
    claim = claim_recovery_turn(event_log, {"id": 1, "event": "admitted", "data": admitted})

    async def scenario():
        for response in (await web.api_chat_job_stream("recovery-turn"),
                         await web._replay_claimed_turn(claim, "session", "hash", "recovery-turn")):
            events = [event async for event in response.body_iterator]
            assert [event["event"] for event in events] == ["admitted", "error"]
            assert events[0]["id"] == "1" and json.loads(events[0]["data"]) == admitted
            error = json.loads(events[1]["data"])
            assert error['code'] == 'unknown' and error['phase'] == 'finalization'
            assert error['retryable'] is False and error['unconfirmed'] is True

    asyncio.run(scenario())


@pytest.mark.parametrize('has_claim', [False, True])
def test_replay_filters_an_admission_without_matching_claim_identity(event_log, has_claim):
    events = [{"id": 1, "event": "admitted", "data": {"turnId": "recovery-turn", "sessionId": "other"}},
              {"id": 2, "event": "done", "data": {"sessionKey": "session"}}]
    if has_claim:
        claim_recovery_turn(event_log, *events)
    else:
        event_log.write_bytes(b"".join(encoded(event) for event in events))

    async def scenario():
        response = await web.api_chat_job_stream("recovery-turn")
        assert [event["event"] async for event in response.body_iterator] == ["done"]

    asyncio.run(scenario())


def test_dead_terminal_claim_replays_and_already_consumed_cursor_closes(event_log, monkeypatch):
    monkeypatch.setattr(web, "_ACTIVE_TURN_IDS", set())
    claim_recovery_turn(event_log, {"id": 1, "event": "done", "data": {"sessionKey": "session"}})

    async def scenario():
        response = await web.api_chat_job_stream("recovery-turn")
        assert [event["event"] async for event in response.body_iterator] == ["done"]
        exhausted = await web.api_chat_job_stream("recovery-turn", after=1)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(exhausted.body_iterator), timeout=1)

    asyncio.run(scenario())


def test_active_claim_replays_prefix_then_owner_loss_reports_unconfirmed(event_log, monkeypatch):
    active = {"recovery-turn"}
    monkeypatch.setattr(web, "_ACTIVE_TURN_IDS", active)
    claim_recovery_turn(event_log, {"id": 1, "event": "activity", "data": "received"})

    async def scenario():
        response = await web.api_chat_job_stream("recovery-turn")
        assert (await anext(response.body_iterator))["event"] == "activity"
        active.clear()
        last = await asyncio.wait_for(anext(response.body_iterator), timeout=1)
        assert last["event"] == "error"
        error = json.loads(last["data"])
        assert error['code'] == 'unknown' and error['phase'] == 'finalization'
        assert error['retryable'] is False and error['unconfirmed'] is True
        with pytest.raises(StopAsyncIteration):
            await anext(response.body_iterator)

    asyncio.run(scenario())


def test_replay_keeps_receipt_written_after_error_before_done(event_log, monkeypatch):
    monkeypatch.setattr(web, "_ACTIVE_TURN_IDS", set())
    admitted = {"turnId": "recovery-turn", "sessionId": "session"}
    receipt = {"id": "saved-publication", "kind": "publication"}
    claim_recovery_turn(event_log,
        {"id": 1, "event": "admitted", "data": admitted},
        {"id": 2, "event": "error", "data": {"code": "network", "phase": "execution", "retryable": False}},
        {"id": 3, "event": "agent_event", "data": receipt},
        {"id": 4, "event": "done", "data": {"outcome": "failed"}})

    async def scenario():
        response = await web.api_chat_job_stream("recovery-turn", after=1)
        events = [event async for event in response.body_iterator]
        assert [event["event"] for event in events] == ["error", "agent_event", "done"]
        assert json.loads(events[1]["data"]) == receipt

    asyncio.run(scenario())


def test_exact_legacy_post_replay_has_admission_and_resumable_cursor(event_log):
    token = {"id": 1, "event": "token", "data": "saved answer"}
    done = {"id": 2, "event": "done", "data": {"sessionKey": "session"}}
    claim = claim_recovery_turn(event_log, token, done)
    original = event_log.read_bytes()

    async def scenario():
        response = await web._replay_claimed_turn(claim, "session", "hash", "recovery-turn")
        assert response.headers["X-Easel-Legacy-Admission"] == "1"
        events = [event async for event in response.body_iterator]
        assert [(event["id"], event["event"]) for event in events] == [
            ("1", "admitted"), ("2", "token"), ("3", "done"),
        ]
        assert json.loads(events[0]["data"]) == {"turnId": "recovery-turn", "sessionId": "session"}
        assert json.loads(events[1]["data"]) == token["data"]
        resumed = await web.api_chat_job_stream("recovery-turn", after=2, legacyAdmission=True)
        assert [event async for event in resumed.body_iterator] == events[2:]
        legacy = await web.api_chat_job_stream("recovery-turn")
        assert [(event["id"], event["event"]) async for event in legacy.body_iterator] == [
            ("1", "token"), ("2", "done"),
        ]

    asyncio.run(scenario())
    assert event_log.read_bytes() == original


@pytest.mark.parametrize("events", [
    [{"id": 1, "event": "done", "data": {"sessionKey": "other"}}],
    [{"id": 1, "event": "admitted", "data": {"turnId": "recovery-turn", "sessionId": "other"}},
     {"id": 2, "event": "done", "data": {"sessionKey": "session"}}],
    [{"id": 2, "event": "done", "data": {"sessionKey": "session"}}],
])
def test_legacy_recovery_cannot_admit_inconsistent_journal(event_log, events):
    claim = claim_recovery_turn(event_log, *events)

    async def scenario():
        response = await web._replay_claimed_turn(claim, "session", "hash", "recovery-turn")
        assert response.headers.get("X-Easel-Legacy-Admission") is None
        assert "admitted" not in [event["event"] async for event in response.body_iterator]

    asyncio.run(scenario())
