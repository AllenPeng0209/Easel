"""A05 HTTP steer uses the authorized live Codex turn and durable receipts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import threading
import uuid

import pytest

from tests.test_chat_artifacts import artifact_http_environment
import web.app as web
from web.chat_history import ChatHistoryStore

pytestmark = pytest.mark.access_integration


def payload(message='Adjust the next step', **updates):
    return {'sessionId': 'steer-chat', 'turnId': 'steer-turn', 'requestId': 'steer-request',
            'message': message, **updates}


@contextmanager
def active_turn(owner, monkeypatch, *, runtime='codex', bind_capability=True, steer_failure=None,
                capability_closed=None):
    ready = threading.Event()
    finish = threading.Event()
    sent = []

    async def steer(message):
        sent.append(message)
        if steer_failure:
            await steer_failure()

    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('native-steer-fixture')
            if bind_capability and request.bind_steer:
                request.bind_steer(steer)
            ready.set()
            try:
                assert await asyncio.to_thread(finish.wait, 8)
                emit('token', 'completed')
            finally:
                if bind_capability and request.bind_steer:
                    request.bind_steer(None)
                if capability_closed:
                    capability_closed.set()

    monkeypatch.setattr(web, '_chat_backend', lambda _: runtime)
    monkeypatch.setattr(web, '_harness_settings', lambda: {'models': {}, 'sandbox': 'read-only'})
    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    with owner, ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(owner.post, '/api/chat/stream', json={
            'message': 'Start', 'sessionId': 'steer-chat', 'turnId': 'steer-turn',
        })
        try:
            assert ready.wait(4), response.result(timeout=1).text if response.done() else 'adapter not ready'
            yield sent, finish, response
        finally:
            finish.set()


def business_events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith('data: {') and any(
                f'"kind": "{kind}"' in line for kind in ('steer', 'steer_capability'))]


def test_accepted_steer_is_once_scoped_and_recovered(artifact_http_environment, monkeypatch):
    _, owner, members, _, _ = artifact_http_environment
    foreign = owner.post('/api/projects', json={'name': 'Other steer IP'}).json()['id']
    secret = uuid.uuid4().hex
    monkeypatch.setattr(web, '_secret_masks', lambda: [(secret, 'masked')])
    value = payload('Adjust ' + secret)
    with active_turn(owner, monkeypatch) as (sent, finish, streaming):
        assert owner.post('/api/chat/steer', json=payload(turnId='wrong-turn')).status_code == 409
        assert owner.post('/api/chat/steer', json=payload(sessionId='other-chat')).status_code == 409
        assert owner.post(f'/api/projects/{foreign}/chat/steer', json=value).status_code == 409
        assert members['operator'].post('/api/chat/steer', json=value).status_code == 403
        assert owner.post('/api/chat/steer', json=value).json() == {'status': 'accepted'}
        assert owner.post('/api/chat/steer', json=value).json() == {'status': 'accepted'}
        assert owner.post('/api/chat/steer', json=payload('Different')).status_code == 409
        assert sent == [value['message']]
        finish.set()
        stream = streaming.result(timeout=4)
        events = business_events(stream)
        assert [event['status'] for event in events] == ['ready', 'accepted', 'closed']
        accepted = events[1]
        assert accepted['id'] == 'steer:steer-request'
        assert accepted['requestId'] == value['requestId'] and accepted['turnId'] == value['turnId']
        assert accepted['text'] == 'Adjust masked'
        assert secret not in stream.text
        replay = owner.get('/api/chat/jobs/steer-turn/stream')
        assert business_events(replay) == events
        snapshot = owner.get('/api/chat/last/steer-chat', params={'turn_id': 'steer-turn'}).json()
        assert snapshot['events'] == [accepted]
        assert owner.post('/api/chat/steer', json=value).json() == {'status': 'accepted'}
        assert owner.post('/api/chat/steer', json=payload(requestId='not-claimed')).status_code == 409
        assert sent == [value['message']]


@pytest.mark.parametrize('runtime,bind_capability', [('codex', False), ('opencode', True)])
def test_no_registered_codex_steer_rejects_before_rpc(artifact_http_environment, monkeypatch, runtime, bind_capability):
    _, owner, _, _, _ = artifact_http_environment
    with active_turn(owner, monkeypatch, runtime=runtime, bind_capability=bind_capability) as (sent, _, _):
        assert owner.post('/api/chat/steer', json=payload()).status_code == 409
        assert sent == []


@pytest.mark.parametrize('failure,expected', [('transport', 'unconfirmed'), ('timeout', 'unconfirmed'), ('rejected', 'rejected')])
def test_uncertainty_and_explicit_rejection_never_retry_rpc(artifact_http_environment, monkeypatch, failure, expected):
    _, owner, _, _, _ = artifact_http_environment

    async def fail():
        if failure == 'rejected':
            from easel.harnesses.base import SteerRejected
            raise SteerRejected('rejected')
        if failure == 'timeout':
            await asyncio.Event().wait()
        raise web.HarnessError('transport lost')

    monkeypatch.setattr(web, '_CHAT_STEER_TIMEOUT_SECONDS', .02, raising=False)
    with active_turn(owner, monkeypatch, steer_failure=fail) as (sent, finish, streaming):
        for _ in range(2):
            response = owner.post('/api/chat/steer', json=payload())
            if expected == 'rejected':
                assert response.status_code == 409
            else:
                assert response.status_code == 200 and response.json() == {'status': 'unconfirmed'}
        assert sent == [payload()['message']]
        finish.set()
        assert all(event['kind'] != 'steer' for event in business_events(streaming.result(timeout=4)))


def test_inflight_duplicate_returns_uncertain_and_late_ack_precedes_final_snapshot(
        artifact_http_environment, monkeypatch):
    _, owner, _, _, _ = artifact_http_environment
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    async def wait_ack():
        assert ChatHistoryStore(web._sessions_dir()).steer_receipt('steer-request')['status'] == 'unconfirmed'
        entered.set()
        assert await asyncio.to_thread(release.wait, 4)

    with active_turn(owner, monkeypatch, steer_failure=wait_ack, capability_closed=closed) as (sent, finish, streaming):
        with ThreadPoolExecutor(max_workers=1) as pool:
            attempt = pool.submit(owner.post, '/api/chat/steer', json=payload())
            try:
                assert entered.wait(4)
                duplicate = owner.post('/api/chat/steer', json=payload())
                assert duplicate.status_code == 200 and duplicate.json() == {'status': 'unconfirmed'}
                assert owner.post('/api/chat/steer', json=payload(requestId='busy')).status_code == 409
                finish.set()
                assert closed.wait(4)
                assert not streaming.done()
            finally:
                release.set()
            assert attempt.result(timeout=4).json() == {'status': 'accepted'}
        events = business_events(streaming.result(timeout=4))
        assert [event['status'] for event in events] == ['ready', 'closed', 'accepted']
        snapshot = owner.get('/api/chat/last/steer-chat', params={'turn_id': 'steer-turn'}).json()
        assert snapshot['events'] == events[-1:] and sent == [payload()['message']]


def test_steer_receipt_claim_is_atomic_across_catalog_connections(tmp_path):
    store = ChatHistoryStore(tmp_path)

    def claim(_):
        return ChatHistoryStore(tmp_path).claim_steer('same-request', 'same-digest')

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(claim, range(8))).count(True) == 1
    assert store.steer_receipt('same-request') == {'payloadHash': 'same-digest', 'status': 'unconfirmed'}
    store.finish_steer('same-request', 'same-digest', 'accepted')
    assert ChatHistoryStore(tmp_path).steer_receipt('same-request')['status'] == 'accepted'
