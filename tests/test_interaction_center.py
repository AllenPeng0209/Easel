from __future__ import annotations

import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def center(tmp_path, **callbacks):
    app = FastAPI()
    if importlib.util.find_spec('web.interaction_routes'):
        from web.interaction_routes import create_router
        app.include_router(create_router(lambda: tmp_path, **callbacks))
    return TestClient(app)


def bootstrap(client, client_id=None):
    r = client.get('/api/interactions/bootstrap', params={'client_id': client_id} if client_id else {})
    assert r.status_code == 200, r.text
    return r.json()


def seeded(client):
    b = bootstrap(client)
    r = client.post('/api/interactions/simulation', json={'client_id': b['client_id']})
    assert r.status_code == 200, r.text
    return r.json()


def detail(client, b, identity):
    r = client.get('/api/interactions/conversations/' + identity, params={'client_id': b['client_id']})
    assert r.status_code == 200, r.text
    return r.json()


def mutate(client, b, conversation, suffix, **values):
    r = client.post('/api/interactions/conversations/' + conversation['id'] + suffix,
                    json={'client_id': b['client_id'], 'expected_revision': conversation['revision'], **values})
    assert r.status_code == 200, r.text
    return r.json()


def draft(client, b, conversation):
    return mutate(client, b, conversation, '/draft')


def approve(client, b, conversation):
    r = client.post('/api/interactions/drafts/' + conversation['drafts'][-1]['id'] + '/approve',
                    json={'client_id': b['client_id'], 'expected_revision': conversation['revision']})
    assert r.status_code == 200, r.text
    return r.json()


def send(client, b, conversation, op='acceptance-send-1'):
    return client.post('/api/interactions/drafts/' + conversation['drafts'][-1]['id'] + '/send',
                       json={'client_id': b['client_id'], 'expected_revision': conversation['revision'], 'operation_id': op})


def test_default_workspace_persists_without_seeding_fake_platform_messages(tmp_path):
    b = bootstrap(center(tmp_path))
    assert b['conversations'] == []
    assert b['rules']['mode'] == 'assist'
    assert not b['rules']['enabled']
    assert bootstrap(center(tmp_path))['client_id'] == b['client_id']


def test_simulation_is_idempotent_and_never_claims_live_platform_connection(tmp_path):
    client = center(tmp_path)
    b = seeded(client)
    again = seeded(client)
    assert len(b['conversations']) == len(again['conversations']) == 4
    assert {c['delivery_mode'] for c in b['channels']} == {'simulation'}
    assert {c['platform'] for c in b['channels']} == {'xiaohongshu', 'douyin'}
    assert {c['kind'] for c in b['channels']} == {'comment', 'dm'}
    assert all(c['status'] != 'connected' for c in b['channels'])


def test_client_scope_prevents_reading_or_mutating_another_clients_conversation(tmp_path):
    client = center(tmp_path)
    b = seeded(client)
    other = client.post('/api/interactions/clients', json={'name': '另一客户'}).json()
    other_id = other['client_id']
    identity = b['conversations'][0]['id']
    assert client.get('/api/interactions/conversations/' + identity, params={'client_id': other_id}).status_code == 404
    assert client.post('/api/interactions/conversations/' + identity + '/action',
                       json={'client_id': other_id, 'expected_revision': 1, 'action': 'resolve'}).status_code == 404
    assert bootstrap(client, other_id)['conversations'] == []


def test_faq_draft_records_knowledge_evidence_and_simulated_approval(tmp_path):
    client = center(tmp_path)
    b = seeded(client)
    candidates = [draft(client, b, c) for c in b['conversations']]
    c = next(c for c in candidates if c['drafts'][-1]['source'] == 'knowledge_base')
    d = c['drafts'][-1]
    assert d['text'] and d['knowledge_id']
    assert d['confidence'] > 0
    approved = approve(client, b, c)
    assert approved['drafts'][-1]['status'] == 'approved'
    assert any(e['type'] == 'simulated_review' for e in approved['events'])


def test_send_requires_approval_and_has_one_durable_simulation_receipt(tmp_path):
    def network(*args):
        pytest.fail('Simulation must not call a platform adapter')
    client = center(tmp_path, send_adapter=network)
    b = seeded(client)
    c = draft(client, b, b['conversations'][0])
    if not c['drafts'][-1]['text']:
        c = client.patch('/api/interactions/drafts/' + c['drafts'][-1]['id'], json={
            'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': '模拟验收回复。'}).json()
    assert send(client, b, c).status_code == 409
    c = approve(client, b, c)
    first = send(client, b, c)
    assert first.status_code == 200, first.text
    replay = send(client, b, c)
    assert replay.status_code == 200, replay.text
    sent = replay.json()
    assert sent['status'] == 'handled'
    assert len(sent['sends']) == 1
    assert sent['sends'][0]['mode'] == 'simulation'
    assert sent['sends'][0]['receipt_id']


def test_editing_approved_draft_invalidates_approval_and_stale_cas(tmp_path):
    client = center(tmp_path); b = seeded(client)
    c = draft(client, b, b['conversations'][0])
    d = c['drafts'][-1]
    c = client.patch('/api/interactions/drafts/' + d['id'], json={
        'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': '第一版草稿'}).json()
    approved = approve(client, b, c)
    edited = client.patch('/api/interactions/drafts/' + d['id'], json={
        'client_id': b['client_id'], 'expected_revision': approved['revision'], 'text': '人工修改后的草稿'}).json()
    assert edited['drafts'][-1]['status'] == 'draft'
    assert send(client, b, edited).status_code == 409
    assert client.patch('/api/interactions/drafts/' + d['id'], json={
        'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': '过时覆盖'}).status_code == 409


def test_ingest_deduplicates_by_external_id_and_new_message_invalidates_old_approval(tmp_path):
    from easel.interaction_center import InteractionStore
    store = InteractionStore(tmp_path); b = store.bootstrap()
    channel = store.create_channel(b['client_id'], {'name': '模拟评论', 'platform': 'xiaohongshu',
        'kind': 'comment', 'delivery_mode': 'simulation'})
    channel_id = channel['channels'][-1]['id']
    c = store.ingest(b['client_id'], channel_id, 'message-1', '想了解课程', conversation_external_id='thread-1')
    again = store.ingest(b['client_id'], channel_id, 'message-1', '想了解课程', conversation_external_id='thread-1')
    assert c['id'] == again['id'] and c['revision'] == again['revision']
    assert len(again['messages']) == 1
    newer = store.ingest(b['client_id'], channel_id, 'message-2', '还有一个问题', conversation_external_id='thread-1')
    assert newer['revision'] > again['revision']
    assert len(newer['messages']) == 2 and newer['status'] == 'pending'


def test_unknown_question_produces_an_editable_empty_draft_not_invented_answer(tmp_path):
    client = center(tmp_path); b = seeded(client)
    c = next(c for c in b['conversations'] if '投诉' in c['preview'])
    c = draft(client, b, c)
    assert c['drafts'][-1]['source'] == 'manual_required'
    assert c['drafts'][-1]['text'] == ''
    assert c['drafts'][-1]['confidence'] == 0


def test_automatic_faq_sends_only_approved_matching_safe_items_and_respects_takeover(tmp_path):
    client = center(tmp_path); b = seeded(client)
    safe = next(c for c in b['conversations'] if '课程介绍' in c['preview'])
    mutate(client, b, safe, '/action', action='takeover')
    r = client.put('/api/interactions/rules', json={'client_id': b['client_id'], 'mode': 'auto_faq', 'enabled': True})
    assert r.status_code == 200, r.text
    run = client.post('/api/interactions/automation/run', json={'client_id': b['client_id']})
    assert run.status_code == 200, run.text
    after = run.json()
    taken = detail(client, b, safe['id'])
    assert taken['human_takeover'] and not taken['sends']
    complaint = next(c for c in after['conversations'] if '投诉' in c['preview'])
    assert not detail(client, b, complaint['id'])['sends']
    assert any(c['status'] == 'handled' for c in after['conversations'])


def test_channel_metadata_cannot_forge_live_connection_or_accept_plaintext_credentials(tmp_path):
    client = center(tmp_path); b = bootstrap(client)
    values = {'client_id': b['client_id'], 'name': '未授权抖音', 'platform': 'douyin', 'kind': 'dm', 'delivery_mode': 'official'}
    r = client.post('/api/interactions/channels', json={**values, 'status': 'connected', 'access_token': 'do-not-store'})
    assert r.status_code == 422
    r = client.post('/api/interactions/channels', json=values)
    assert r.status_code == 200
    assert r.json()['channels'][-1]['status'] != 'connected'


def test_knowledge_disable_and_customer_rules_survive_restart(tmp_path):
    client = center(tmp_path); b = bootstrap(client)
    r = client.post('/api/interactions/knowledge', json={'client_id': b['client_id'], 'question': '怎么咨询',
        'keywords': ['咨询'], 'answer': '请说明你的需求。', 'auto_send': False})
    assert r.status_code == 200, r.text
    k = r.json()['knowledge'][-1]
    r = client.patch('/api/interactions/knowledge/' + k['id'], json={'client_id': b['client_id'], 'enabled': False})
    assert r.status_code == 200
    loaded = bootstrap(center(tmp_path), b['client_id'])
    assert loaded['knowledge'][-1]['enabled'] is False


def test_unknown_send_response_is_preserved_and_not_resent(tmp_path):
    calls = []
    def adapter(channel, conversation, draft, operation_id):
        calls.append(operation_id)
        raise TimeoutError('private body must not leak')
    client = center(tmp_path, send_adapter=adapter, capabilities=lambda channel: {'state': 'connected', 'can_send': True, 'can_sync': True})
    b = bootstrap(client)
    created = client.post('/api/interactions/channels', json={'client_id': b['client_id'], 'name': '测试接入',
        'platform': 'douyin', 'kind': 'comment', 'delivery_mode': 'official'}).json()
    from easel.interaction_center import InteractionStore
    c = InteractionStore(tmp_path).ingest(b['client_id'], created['channels'][-1]['id'], 'external-1', '您好')
    c = draft(client, b, c)
    c = client.patch('/api/interactions/drafts/' + c['drafts'][-1]['id'], json={
        'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': '你好，请问有什么可以帮助你？'}).json()
    c = approve(client, b, c)
    result = send(client, b, c)
    assert result.status_code == 200
    assert result.json()['sends'][-1]['status'] == 'unknown'
    assert 'private body' not in result.text
    assert send(client, b, c).status_code == 200
    assert len(calls) == 1


def test_manual_xhs_sync_can_read_but_never_gain_send_permission(tmp_path):
    calls = []
    def sync(channel, source_url=''):
        calls.append(source_url)
        return [{'external_id': 'comment-1', 'text': '想了解课程', 'author_name': '访客'}]
    client = center(tmp_path, sync_adapter=sync, capabilities=lambda channel: {'state': 'manual_only', 'can_sync': True, 'can_send': True})
    b = bootstrap(client)
    b = client.post('/api/interactions/channels', json={'client_id': b['client_id'], 'name': '小红书人工接待',
        'platform': 'xiaohongshu', 'kind': 'comment', 'delivery_mode': 'manual'}).json()
    channel = b['channels'][-1]
    assert channel['capabilities']['can_sync'] is True
    assert channel['capabilities']['can_send'] is False
    r = client.post('/api/interactions/channels/' + channel['id'] + '/sync', json={'client_id': b['client_id'], 'source_url': 'https://www.xiaohongshu.com/explore/example'})
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and len(r.json()['conversations']) == 1


def test_new_incoming_message_rejects_previously_approved_reply(tmp_path):
    from easel.interaction_center import InteractionStore
    client = center(tmp_path); b = seeded(client)
    c = draft(client, b, b['conversations'][0])
    c = client.patch('/api/interactions/drafts/' + c['drafts'][-1]['id'], json={
        'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': '审核过的旧回复'}).json()
    c = approve(client, b, c)
    newer = InteractionStore(tmp_path).ingest(b['client_id'], c['channel_id'], 'new-question', '补充：我想退款', conversation_external_id=c['external_id'])
    assert newer['drafts'][-1]['status'] == 'superseded'
    assert send(client, b, newer).status_code == 409


def test_real_channel_cannot_use_seeded_simulation_faq_for_automatic_sends(tmp_path):
    from easel.interaction_center import InteractionStore
    calls = []
    def sender(*args):
        calls.append(args)
        return {'status': 'sent', 'external_message_id': 'real-message', 'receipt_id': 'receipt'}
    client = center(tmp_path, send_adapter=sender, capabilities=lambda channel: {'can_send': True, 'state': 'connected'})
    b = seeded(client)
    b = client.post('/api/interactions/channels', json={'client_id': b['client_id'], 'name': '已配置通道',
        'platform': 'douyin', 'kind': 'comment', 'delivery_mode': 'official'}).json()
    c = InteractionStore(tmp_path).ingest(b['client_id'], b['channels'][-1]['id'], 'real-question', '想了解课程')
    client.put('/api/interactions/rules', json={'client_id': b['client_id'], 'mode': 'auto_faq', 'enabled': True})
    client.post('/api/interactions/automation/run', json={'client_id': b['client_id']})
    assert calls == []
    assert detail(client, b, c['id'])['sends'] == []


def test_changing_an_answer_does_not_silently_keep_its_old_auto_send_approval(tmp_path):
    client = center(tmp_path); b = seeded(client)
    k = b['knowledge'][0]
    updated = client.patch('/api/interactions/knowledge/' + k['id'], json={
        'client_id': b['client_id'], 'answer': '新编辑的课程答复'}).json()
    assert updated['knowledge'][0]['auto_send'] is False


def test_agent_authorship_is_recorded_but_cannot_impersonate_real_human_approval(tmp_path):
    from easel.interaction_center import InteractionStore
    client = center(tmp_path); b = bootstrap(client)
    b = client.post('/api/interactions/channels', json={'client_id': b['client_id'], 'name': '官方评论',
        'platform': 'douyin', 'kind': 'comment', 'delivery_mode': 'official'}).json()
    c = InteractionStore(tmp_path).ingest(b['client_id'], b['channels'][0]['id'], 'question-1', '您好')
    c = draft(client, b, c)
    d = c['drafts'][-1]
    c = client.patch('/api/interactions/drafts/' + d['id'], json={
        'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': 'Agent 拟定回复', 'source': 'agent'}).json()
    assert c['drafts'][-1]['source'] == 'agent'
    response = client.post('/api/interactions/drafts/' + d['id'] + '/approve', json={
        'client_id': b['client_id'], 'expected_revision': c['revision'], 'review_kind': 'agent'})
    assert response.status_code == 409


def test_unknown_delivery_blocks_new_draft_resubmission_for_same_incoming_message(tmp_path):
    from easel.interaction_center import InteractionStore
    calls = []
    def sender(*args):
        calls.append(args)
        raise TimeoutError()
    client = center(tmp_path, send_adapter=sender, capabilities=lambda channel: {'state': 'connected', 'can_send': True})
    b = bootstrap(client)
    b = client.post('/api/interactions/channels', json={'client_id': b['client_id'], 'name': '接入测试',
        'platform': 'douyin', 'kind': 'comment', 'delivery_mode': 'official'}).json()
    c = InteractionStore(tmp_path).ingest(b['client_id'], b['channels'][0]['id'], 'question-1', '您好')
    for attempt in range(2):
        c = draft(client, b, c)
        c = client.patch('/api/interactions/drafts/' + c['drafts'][-1]['id'], json={
            'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': '你好，请说明需求。'}).json()
        c = approve(client, b, c)
        r = send(client, b, c, 'send-' + str(attempt))
        if attempt == 0:
            assert r.status_code == 200
            c = r.json()
        else:
            assert r.status_code == 409
    assert len(calls) == 1


def test_concurrent_identical_sends_share_one_durable_external_intent(tmp_path):
    from easel.interaction_center import InteractionStore
    entered = threading.Event(); release = threading.Event(); calls = []
    def sender(channel, conversation, draft, operation_id):
        calls.append(operation_id); entered.set()
        assert release.wait(5)
        return {'status': 'sent', 'external_message_id': 'platform-message-1', 'receipt_id': 'receipt-1'}
    client = center(tmp_path, send_adapter=sender, capabilities=lambda channel: {'state': 'connected', 'can_send': True})
    b = bootstrap(client)
    b = client.post('/api/interactions/channels', json={'client_id': b['client_id'], 'name': '并发验收',
        'platform': 'douyin', 'kind': 'comment', 'delivery_mode': 'official'}).json()
    c = InteractionStore(tmp_path).ingest(b['client_id'], b['channels'][0]['id'], 'question-1', '你好')
    c = draft(client, b, c)
    c = client.patch('/api/interactions/drafts/' + c['drafts'][-1]['id'], json={
        'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': '你好，请说明需求。'}).json()
    c = approve(client, b, c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(send, client, b, c, 'same-operation')
        assert entered.wait(5)
        try:
            second = send(client, b, c, 'same-operation')
            assert second.status_code == 200
            assert second.json()['sends'][0]['status'] == 'sending'
        finally:
            release.set()
        result = first.result(timeout=5)
    assert result.status_code == 200
    assert len(calls) == 1 and len(result.json()['sends']) == 1


def test_new_incoming_during_send_does_not_get_marked_handled_by_old_reply(tmp_path):
    from easel.interaction_center import InteractionStore
    def sender(channel, conversation, draft, operation_id):
        InteractionStore(tmp_path).ingest(conversation['client_id'], channel['id'], 'question-2', '我还有问题', conversation_external_id=conversation['external_id'])
        return {'status': 'sent', 'external_message_id': 'reply-1', 'receipt_id': 'receipt-1'}
    client = center(tmp_path, send_adapter=sender, capabilities=lambda channel: {'state': 'connected', 'can_send': True})
    b = bootstrap(client)
    b = client.post('/api/interactions/channels', json={'client_id': b['client_id'], 'name': '新消息验收',
        'platform': 'douyin', 'kind': 'comment', 'delivery_mode': 'official'}).json()
    c = InteractionStore(tmp_path).ingest(b['client_id'], b['channels'][0]['id'], 'question-1', '你好')
    c = draft(client, b, c)
    c = client.patch('/api/interactions/drafts/' + c['drafts'][-1]['id'], json={
        'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': '你好，请说明需求。'}).json()
    c = approve(client, b, c)
    result = send(client, b, c)
    assert result.status_code == 200
    assert result.json()['status'] == 'pending'
    assert result.json()['preview'] == '我还有问题'


def test_sensitive_outbound_text_is_blocked_before_any_simulation_receipt(tmp_path):
    client = center(tmp_path); b = seeded(client)
    c = draft(client, b, b['conversations'][0])
    c = client.patch('/api/interactions/drafts/' + c['drafts'][-1]['id'], json={
        'client_id': b['client_id'], 'expected_revision': c['revision'], 'text': 'Bearer syntheticsecret1234567890'}).json()
    c = approve(client, b, c)
    response = send(client, b, c)
    assert response.status_code == 409
    assert 'syntheticsecret' not in response.text
    assert detail(client, b, c['id'])['sends'] == []


def test_automatic_hourly_limit_is_per_channel_and_persisted(tmp_path):
    from easel.interaction_center import InteractionStore
    client = center(tmp_path); b = bootstrap(client)
    b = client.post('/api/interactions/channels', json={'client_id': b['client_id'], 'name': '自动回复限额验收',
        'platform': 'xiaohongshu', 'kind': 'comment', 'delivery_mode': 'simulation'}).json()
    channel_id = b['channels'][0]['id']
    client.post('/api/interactions/knowledge', json={'client_id': b['client_id'], 'question': '咨询',
        'keywords': ['咨询'], 'answer': '你好，请说明咨询需求。', 'auto_send': True})
    store = InteractionStore(tmp_path)
    for n in range(3):
        store.ingest(b['client_id'], channel_id, 'question-' + str(n), '想咨询一下')
    r = client.put('/api/interactions/rules', json={'client_id': b['client_id'], 'mode': 'auto_faq', 'enabled': True,
        'max_auto_per_hour': 1, 'max_auto_per_run': 10})
    assert r.status_code == 200, r.text
    client.post('/api/interactions/automation/run', json={'client_id': b['client_id']})
    client.post('/api/interactions/automation/run', json={'client_id': b['client_id']})
    after = bootstrap(center(tmp_path), b['client_id'])
    assert after['rules']['max_auto_per_hour'] == 1
    assert sum(len(detail(client, b, c['id'])['sends']) for c in after['conversations']) == 1


def test_automatic_run_budget_does_not_silently_process_entire_backlog(tmp_path):
    client = center(tmp_path); b = seeded(client)
    r = client.put('/api/interactions/rules', json={'client_id': b['client_id'], 'mode': 'auto_faq', 'enabled': True,
        'max_auto_per_hour': 20, 'max_auto_per_run': 1})
    assert r.status_code == 200, r.text
    after = client.post('/api/interactions/automation/run', json={'client_id': b['client_id']}).json()
    assert sum(c['status'] == 'handled' for c in after['conversations']) == 1


def test_ambiguous_faq_matches_go_to_review_instead_of_choosing_an_arbitrary_answer(tmp_path):
    client = center(tmp_path); b = seeded(client)
    client.post('/api/interactions/knowledge', json={'client_id': b['client_id'], 'question': '另一个课程方向',
        'keywords': ['了解课程'], 'answer': '另一项需要人工选择的课程说明。', 'auto_send': True})
    c = next(c for c in b['conversations'] if '想了解课程，' in c['preview'])
    c = draft(client, b, c)
    assert c['drafts'][-1]['source'] == 'manual_required'
    assert c['drafts'][-1]['text'] == ''


def test_malformed_sync_payload_is_reported_safely_without_importing_untrusted_fields(tmp_path):
    client = center(tmp_path, sync_adapter=lambda *a, **k: [{'private': 'upstream-sensitive-detail'}],
                    capabilities=lambda channel: {'state': 'ready', 'can_sync': True, 'can_send': False})
    b = bootstrap(client)
    b = client.post('/api/interactions/channels', json={'client_id': b['client_id'], 'name': '读取验收',
        'platform': 'douyin', 'kind': 'comment', 'delivery_mode': 'official'}).json()
    response = client.post('/api/interactions/channels/' + b['channels'][0]['id'] + '/sync', json={'client_id': b['client_id']})
    assert response.status_code == 503
    assert 'upstream-sensitive-detail' not in response.text
