"""Authenticated transport adapter around the original Huobao frontend/backend."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import re
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from easel.huobao_runtime import HuobaoRuntime, SOURCE, _json
from easel.projects import project_id
from easel.request_identity import current_user
from easel.video_workshop import WorkshopError, _file_lock
from web.workshop_actions import WorkshopActions

_RUNTIME = {}


async def proxy_client(host, engine):
    clients = getattr(getattr(getattr(host, 'app', None), 'state', None), 'huobao_proxy_clients', None)
    if clients is None:
        return httpx.AsyncClient(trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(1800, connect=3)), True
    key = str(engine.directory)
    identity = (engine.state['base'], engine.state['token'])
    current = clients.get(key)
    if current and current[0] == identity:
        return current[1], False
    if current:
        await current[1].aclose()
    client = httpx.AsyncClient(trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(1800, connect=3), limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))
    clients[key] = (identity, client)
    return client, False


async def close_runtimes():
    await asyncio.gather(*(asyncio.to_thread(engine.stop) for engine in list(_RUNTIME.values())), return_exceptions=True)
    _RUNTIME.clear()


def runtime(host):
    workspace = host._workspace_root()
    key = (str(host.PROJECT_ROOT), str(workspace), project_id())
    if key not in _RUNTIME:
        _RUNTIME[key] = HuobaoRuntime(host.PROJECT_ROOT, workspace, project_id())
    return _RUNTIME[key]


def reconcile(host, identifier):
    from easel.host_state import generation_state_directory
    from easel.video_workshop import WorkshopStore
    path = generation_state_directory(host.PROJECT_ROOT, project_id()).parent / 'huobao-drama' / 'bindings.json'
    if not path.is_file():
        return WorkshopStore(host._outputs_dir()).get(identifier)
    engine = runtime(host)
    if engine.bindings_path.is_file() and identifier in engine.bindings():
        return engine.pull_if_changed(identifier)
    return engine.store.get(identifier)


def _redact(value):
    if isinstance(value, list):return [_redact(row) for row in value]
    if isinstance(value, dict):
        result={}
        for key,row in value.items():
            normalized=key.replace('_','').lower()
            if normalized=='apikey':result[key]='__configured__' if row else ''
            elif normalized in {'secret','password','token','accesstoken','refreshtoken','authorization'}:result[key]=''
            else:result[key]=_redact(row)
        return result
    return value


def _native_agent_operation(engine, binding, path, body):
    if not body or not isinstance(body, dict):return None
    config_id=body.get('config_id') or body.get('text_config_id')
    with engine.database() as db:
        row=db.execute("SELECT base_url FROM ai_service_configs WHERE service_type='text' " + ('AND id=?' if config_id else 'ORDER BY priority DESC LIMIT 1'), (config_id,) if config_id else ()).fetchone()
    if row and 'xiangya-agent' not in row[0]:return None
    action=None;shots=[];assets=[];key=None
    if path.startswith('api/v1/agent/'):
        name=path.split('/')[3]
        action={'script_rewriter':'rewrite','extractor':'extract_assets','storyboard_breaker':'breakdown','prompt_generator':'video_prompts'}.get(name)
        if name=='prompt_generator':
            message=body.get('message','')
            match=re.search(r'(?:character_id|scene_id|prop_id)[:：\s]+(\d+)',message)
            if match:
                assets=[native for native,row in binding['asset_map'].items() if row['id']==int(match[1])]
                action='image_prompts'
            else:
                match=re.search(r'(?:分镜\s*(?:ID|id)[:：\s]*|ID[:：\s]*)(\d+)',message)
                if match:shots=[native for native,row in binding['shot_map'].items() if row==int(match[1])]
    elif re.fullmatch(r'api/v1/episodes/\d+/extract',path):
        action='extract_assets';key='extract:'+body.get('target','characters')
    elif re.fullmatch(r'api/v1/episodes/\d+/generate-video-prompts',path):
        action='video_prompts';key='video-prompts'
        shots=[native for native,row in binding['shot_map'].items() if row in body.get('storyboard_ids',[])]
    elif re.fullmatch(r'api/v1/(characters|scenes|props)/\d+/generate-prompt',path):
        kind={'characters':'character','scenes':'scene','props':'prop'}[path.split('/')[2]]
        assets=[native for native,row in binding['asset_map'].items() if row['kind']==kind and row['id']==int(path.split('/')[3])]
        action='image_prompts'
    if not action:return None
    return {'action':action,'shot_ids':shots,'asset_ids':assets,'status_key':key,
            'notes':(body.get('message','') + ('\n本次仅提取 '+body['target']+'。' if body.get('target') else '') + '\n原版引擎编号映射：' + json.dumps({'assets':binding['asset_map'],'shots':binding['shot_map']},ensure_ascii=False) + '\n更新制作清单必须保留映射左侧的稳定 ID，不能用引擎数字编号替换。')[-4000:]}


def _media_inputs(value):
    if isinstance(value,list):
        for row in value:_media_inputs(row)
    elif isinstance(value,dict):
        for key,row in value.items():
            if isinstance(row,str) and row and key in {'video_url','image_url','composed_video_url','local_path','first_frame_url','last_frame_url'}:
                if not row.startswith(('https://','http://','data:')) and (not row.lstrip('/').startswith('static/') or '..' in row.split('/') or '\\' in row):
                    raise HTTPException(422,'素材路径必须属于当前制作项目')
            _media_inputs(row)


def create_router(get_host):
    router=APIRouter()

    @router.get('/huobao-studio/{path:path}', include_in_schema=False)
    def studio_files(path: str):
        public=(SOURCE/'frontend'/'.output'/'public').resolve()
        candidate=(public/path).resolve()
        if public not in candidate.parents and candidate!=public:raise HTTPException(404)
        if candidate.is_file():
            hashed = (path.startswith('_nuxt/') and '/builds/' not in path
                      and re.search(r'(?:^|[/.])[A-Za-z0-9_-]{6,}\.(?:js|css|woff2?)$', path))
            headers = {'Cache-Control': 'public, max-age=31536000, immutable'} if hashed else {}
            return FileResponse(candidate, headers=headers)
        if path.startswith(('_nuxt/','icons/')):raise HTTPException(404)
        return FileResponse(public/'index.html',headers={'Cache-Control':'no-cache'})

    @router.post('/api/workshop/projects/{identifier}/engine/open')
    async def open_engine(identifier: str, request: Request):
        host=get_host();actor=current_user()
        host._require_operation(roles=('owner','operator','viewer'))
        body=await request.json()
        engine=runtime(host)
        try:
            project=engine.store.get(identifier)
            engine.store._expect(project,body.get('expected_revision'))
            binding=await asyncio.to_thread(engine.open,identifier)
        except WorkshopError as error:
            raise HTTPException(error.status,str(error)) from None
        stage='videos' if project['shots'] else 'assets' if project['script'] else 'script'
        query=urlencode({'xiangyaProject':identifier,'xiangyaIp':project_id(),'xiangyaUser':actor['id'],'stage':stage,
                         'theme':body.get('theme','dark') if body.get('theme') in {'dark','light'} else 'dark'})
        return {'url':f"/huobao-studio/drama/{binding['drama_id']}/episode/1?{query}",
                'engine':'huobao-drama','upstream_commit':json.loads((SOURCE.parent/'UPSTREAM.json').read_text())['commit']}

    @router.get('/api/workshop/projects/{identifier}/actions')
    async def jobs(identifier: str):
        return {'jobs':await WorkshopActions(get_host()).list(identifier)}

    @router.api_route('/api/workshop/projects/{identifier}/engine/{path:path}',methods=['GET','HEAD','POST','PUT','DELETE'])
    async def proxy(identifier: str,path: str,request: Request):
        host=get_host();engine=runtime(host)
        try:
            if not path.startswith(('api/v1/','static/')) or '..' in path.split('/'):
                raise HTTPException(404)
            if request.method in {'GET', 'HEAD'} and path.startswith('static/'):
                # The immutable media tree remains readable when the editor is
                # idle or offline. Authentication/role checks run in middleware.
                from easel.media_delivery import MediaDelivery
                def media():
                    engine.store.get(identifier)
                    if identifier not in engine.bindings():
                        raise HTTPException(404, '此视频项目尚未连接制作引擎')
                    delivery = MediaDelivery(engine.directory / 'media-cache', engine.data_root / 'static')
                    user = current_user()
                    try:
                        return delivery.response(request, path[len('static/'):],
                                                 actor_scope=f"{project_id()}:{user['id']}")
                    except (ValueError, OSError):
                        raise HTTPException(404, '视频素材暂不可用') from None
                return await asyncio.to_thread(media)
            binding=await asyncio.to_thread(engine.read_binding if request.method in {'GET', 'HEAD'} else engine.open,identifier)
            if path.startswith(('api/v1/server-update','api/v1/storage')):
                raise HTTPException(404, '此管理功能由响芽宿主提供')
            body=await request.body()
            payload=None
            if 'application/json' in request.headers.get('content-type','') and body:
                payload=json.loads(body)
                _media_inputs(payload)
                if 'api_key' in payload and payload['api_key']=='__configured__':
                    payload.pop('api_key');body=json.dumps(payload).encode()
            if request.method=='POST':
                operation=_native_agent_operation(engine,binding,path,payload)
                if operation:
                    current=engine.store.get(identifier)
                    job=await WorkshopActions(host).submit(identifier,{'expected_revision':current['revision'],
                        'request_id':'huobao-'+__import__('uuid').uuid4().hex,
                        **{key:operation[key] for key in ('action','shot_ids','asset_ids','notes')}}, queued=True)
                    if operation['status_key']:
                        with _file_lock(engine.directory/'sync.lock'):
                            data=engine.bindings();data[identifier].setdefault('agent_jobs',{})[operation['status_key']]=job['id'];_json(engine.bindings_path,data)
                    return JSONResponse({'code':200,'data':{'started':True,'total':len(job.get('shot_ids', operation['shot_ids']))},'xiangyaJob':{'id':job['id']}}, headers={'X-Chirpiva-Job-Id': job['id']})
            if request.method=='GET' and path.endswith(('extract-status','video-prompts-status')) and binding.get('agent_jobs'):
                rows={row['id']:row for row in await WorkshopActions(host).list(identifier)}
                def state(key):
                    row=rows.get(binding['agent_jobs'].get(key,''))
                    if not row:return None
                    return {'status':'done' if row['status']=='completed' else 'error' if row['status'] in {'failed','cancelled','unconfirmed'} else 'running','error':row.get('message')}
                value=({key:state('extract:'+key) for key in ('characters','scenes','props')} if path.endswith('extract-status') else state('video-prompts'))
                return {'code':200,'data':value}
            client, owns_client = await proxy_client(host, engine)
            headers={'X-Xiangya-Runtime':engine.state['token']}
            for key in ('content-type','range'):
                if value:=request.headers.get(key):headers[key]=value
            url=engine.state['base']+'/'+path
            if request.url.query:url+='?'+request.url.query
            timeout = httpx.Timeout(15, connect=3) if request.method in {'GET', 'HEAD'} else httpx.Timeout(1800, connect=3)
            try:
                response=await client.send(client.build_request(request.method,url,content=body,headers=headers,timeout=timeout),stream=True)
            except BaseException:
                if owns_client: await client.aclose()
                raise
            if 'application/json' in response.headers.get('content-type',''):
                raw=await response.aread();status=response.status_code
                await response.aclose()
                if owns_client: await client.aclose()
                data=json.loads(raw)
                if status<400 and request.method not in {'GET', 'HEAD'}:
                    await asyncio.to_thread(engine.pull,identifier)
                return JSONResponse(_redact(data),status_code=status)
            async def content():
                try:
                    async for chunk in response.aiter_bytes():yield chunk
                finally:
                    await response.aclose()
                    if owns_client: await client.aclose()
            return StreamingResponse(content(),status_code=response.status_code,headers={key:value for key,value in response.headers.items()
                if key in {'content-type','content-range','accept-ranges'}})
        except WorkshopError as error:
            return JSONResponse({'code':error.status,'message':str(error)},status_code=error.status)
        except httpx.HTTPError:
            return JSONResponse({'code':503,'message':'火宝原任务连接暂时中断，请刷新后查询原任务'},status_code=503)
    return router
