"""D25 uses real scoped services at the HTTP and streamed Host boundary."""
import json

import pytest

from tests.test_chat_artifacts import artifact_http_environment
from easel.projects import project_context
from easel.request_identity import actor_context
import web.app as web

pytestmark = pytest.mark.access_integration
ROUTE = '/api/chat/business-objects'


@pytest.fixture
def business_http(artifact_http_environment, monkeypatch):
    root, owner, members, client, _ = artifact_http_environment
    monkeypatch.setattr(web, 'IDEAS_FILE', root / 'outputs/_ideas.json')
    item = owner.post('/api/ideas', json={'title':'真实保存的选题','note':'选题笔记','source':'用户调研','status':'doing'})
    assert item.status_code == 200
    from easel.video_workshop import WorkshopStore
    project = WorkshopStore(root / 'outputs', strict_canvas_bindings=True).create(
        {'title':'真实工坊项目','brief':'明确的创作要求','reference_url':'','aspect_ratio':'9:16'})
    with actor_context(owner.get('/api/auth/me').json()), project_context('default', root):
        artifact = web._chat_artifact_store().present([{'path':'outputs/topic/report.txt','description':'固定稿件文件'}],
            session_id='d25-files',turn_id='one',call_id='one')[0]
    return root, owner, members, client, item.json(), project, artifact


def query(client, kind, identifier=None, **extra):
    params={'action':'inspect' if identifier else 'list','kind':kind,**extra}
    if identifier:params['id']=identifier
    return client.get(ROUTE,params=params)


def test_business_reads_real_ids_and_does_not_invent_idea_versions(business_http):
    _, owner, members, client, idea, *_ = business_http
    response=query(owner,'idea',idea['id'])
    assert response.status_code==200,response.text
    result=response.json();card=result['items'][0]
    assert card['id']==idea['id'] and card['title']==idea['title']
    assert card['actorId']==owner.get('/api/auth/me').json()['id']
    assert card['projectId']=='default'
    assert card['version']=={'revision':None,'sha256':None}
    assert len(card['snapshotHash'])==64
    assert query(members['operator'],'idea',idea['id']).status_code==200
    assert query(members['viewer'],'idea',idea['id']).status_code==403
    assert query(client(),'idea',idea['id']).status_code==401
    assert owner.post(ROUTE,json={'action':'inspect','kind':'idea','id':idea['id']}).status_code in (403,405)


def test_draft_snapshot_keeps_artifact_identity_and_actor_isolation(business_http):
    root, owner, members, _, _, _, artifact=business_http
    response=query(owner,'draft',artifact['artifactId'])
    assert response.status_code==200,response.text
    card=response.json()['items'][0]
    assert card['id']==artifact['artifactId']
    assert card['version']=={'revision':artifact['revision'],'sha256':artifact['sha256']}
    assert card['details']['artifact']['artifactId']==artifact['artifactId']
    (root/'outputs/topic/report.txt').write_text('unapproved later source')
    assert query(owner,'draft',artifact['artifactId']).json()['items'][0]['version']['sha256']==artifact['sha256']
    assert query(members['operator'],'draft',artifact['artifactId']).status_code==404
    other=owner.post('/api/projects',json={'name':'另一个IP'}).json()['id']
    response=owner.get(f'/api/projects/{other}/chat/business-objects',params={'action':'inspect','kind':'draft','id':artifact['artifactId']})
    assert response.status_code==404,response.text


def test_workshop_lookup_uses_real_project_and_query_never_renders_or_reviews(business_http,monkeypatch):
    _, owner, _, _, _, project, _=business_http
    def forbidden(*args,**kwargs):pytest.fail('Business inspection must not start/collect/render/review')
    from easel.video_workshop import WorkshopStore
    for method in ('start_render','collect_render','review','publication','attach_canvas'):
        monkeypatch.setattr(WorkshopStore,method,forbidden)
    response=query(owner,'workshop',project['id'])
    assert response.status_code==200,response.text
    card=response.json()['items'][0]
    assert card['id']==project['id'] and card['version']['revision']==project['revision']
    assert card['details']['project']['id']==project['id']
    assert card['actions']==[{'kind':'open','target':'workshop','entityId':project['id'],'label':card['actions'][0]['label']}]


@pytest.mark.parametrize('extra',[{'actorId':'forged'},{'projectId':'other'},{'href':'https://example.invalid'},{'status':'approved'}])
def test_business_query_cannot_supply_identity_or_status(business_http,extra):
    _,owner,*_=business_http
    assert query(owner,'idea',**extra).status_code in (400,422)


def test_business_native_callback_emits_typed_entity_and_survives_snapshot(business_http,monkeypatch):
    _,owner,_,_,idea,*_=business_http
    monkeypatch.setattr(web,'_chat_backend',lambda _: 'codex')
    monkeypatch.setattr(web,'_harness_settings',lambda:{'runtime':'codex','models':{},'sandbox':'read-only'})
    class Adapter:
        async def run(self,request,emit,ask,bind):
            bind('native-business-fixture')
            assert request.business_objects is not None
            value=await request.business_objects({'action':'inspect','kind':'idea','id':idea['id']},'actual-business-call')
            assert value['items'][0]['id']==idea['id']
            emit('token','已读取真实选题。')
    monkeypatch.setattr(web.harnesses,'adapter',lambda _:Adapter())
    response=owner.post('/api/chat/stream',json={'message':'查看真实业务对象','sessionId':'business-chat','turnId':'business-turn'})
    assert response.status_code==200,response.text
    current='';events=[]
    for line in response.text.splitlines():
        if line.startswith('event: '):current=line[7:]
        if line.startswith('data: ') and current=='agent_event':
            value=json.loads(line[6:])
            if value.get('kind')=='business':events.append(value)
    assert len(events)==1,response.text
    assert events[0]['business']['id']==idea['id']
    assert events[0]['callId']=='actual-business-call'
    assert events[0]['name']=='easel_business_objects'
    snapshot=owner.get('/api/chat/last/business-chat')
    assert snapshot.status_code==200,snapshot.text
    assert any(event.get('business',{}).get('id')==idea['id'] for event in snapshot.json().get('events',[]))


def test_publication_queries_keep_distinct_tool_receipts_after_snapshot(business_http, monkeypatch):
    from types import SimpleNamespace

    _, owner, *_ = business_http
    record = {'id': 'a' * 32, 'state': 'rejected'}
    monkeypatch.setattr(web, '_publication_workflow', lambda: SimpleNamespace(get=lambda _: record))
    monkeypatch.setattr(web, '_chat_backend', lambda _: 'codex')
    monkeypatch.setattr(web, '_harness_settings', lambda: {'runtime': 'codex', 'models': {}, 'sandbox': 'read-only'})

    class Adapter:
        async def run(self, request, emit, ask, bind):
            bind('native-publication-fixture')
            for call_id in ('first-lookup', 'second-lookup'):
                assert await request.publication_status_with_call(record['id'], call_id) == record
            emit('token', '已核对发布状态。')

    monkeypatch.setattr(web.harnesses, 'adapter', lambda _: Adapter())
    response = owner.post('/api/chat/stream', json={'message': '核对状态', 'sessionId': 'publication-chat',
                                                   'turnId': 'publication-turn'})
    assert response.status_code == 200, response.text
    snapshot = owner.get('/api/chat/last/publication-chat')
    assert snapshot.status_code == 200, snapshot.text
    receipts = [event for event in snapshot.json()['events'] if event['kind'] == 'publication']
    assert [event['callId'] for event in receipts] == ['first-lookup', 'second-lookup']
    assert {event['name'] for event in receipts} == {'easel_publication_status'}
    assert receipts[0]['runId'] and receipts[0]['runId'] == receipts[1]['runId']
    assert receipts[0]['id'] != receipts[1]['id']


@pytest.mark.parametrize('channel',['http','native'])
def test_business_projection_redacts_known_configuration_before_every_delivery(business_http,monkeypatch,channel):
    import secrets
    root,owner,_,_,idea,*_=business_http
    canary=secrets.token_urlsafe(24)
    (root/'.env').write_text('BUSINESS_TEST_TOKEN='+canary+'\n')
    updated=owner.put('/api/ideas/'+idea['id'],json={'title':idea['title'],'note':'普通笔记 '+canary,
        'source':'用户调研','status':'doing'})
    assert updated.status_code==200
    if channel=='http':
        response=query(owner,'idea',idea['id']);assert response.status_code==200
        dto=response.json()
    else:
        seen=[]
        monkeypatch.setattr(web,'_chat_backend',lambda _: 'codex')
        monkeypatch.setattr(web,'_harness_settings',lambda:{'runtime':'codex','models':{},'sandbox':'read-only'})
        class Adapter:
            async def run(self,request,emit,ask,bind):
                bind('native-business-redaction-fixture')
                seen.append(await request.business_objects({'action':'inspect','kind':'idea','id':idea['id']},'redaction-call'))
                emit('token','已读取。')
        monkeypatch.setattr(web.harnesses,'adapter',lambda _:Adapter())
        response=owner.post('/api/chat/stream',json={'message':'查看选题','sessionId':'redaction-chat','turnId':'redaction-turn'})
        assert response.status_code==200
        assert len(seen)==1
        dto=seen[0]
    if canary in json.dumps(dto,ensure_ascii=False):
        pytest.fail('The configured canary escaped the common business projection')
    card=dto['items'][0]
    assert '普通笔记' in card['summary'] and '••••' in card['summary']
    import hashlib
    value={key:value for key,value in card.items() if key not in {'snapshotHash','observedAt'}}
    assert card['snapshotHash']==hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
        allow_nan=False,separators=(',',':')).encode()).hexdigest()
