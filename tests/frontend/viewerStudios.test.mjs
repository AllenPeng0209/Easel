import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const compile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
  fileName: path, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const ownerPermissions = { role: 'owner', canOperate: true, canManageAccounts: true, isPlatformAdmin: true };
const project = (delivered = false) => ({ id: 'video-one', title: '汽车视频', brief: '新能源主题', reference_url: '',
  aspect_ratio: '9:16', revision: 2, stage: delivered ? 'ready_for_review' : 'draft', beeftv_canvas_id: 'canvas-one',
  engine_current_revision: 4, engine_dirty: false, updated_at: '2026-11-02T02:00:00Z',
  deliverables: delivered ? [{ id: 'delivery-one', path: 'topic/final.mp4', sha256: 'one', revision: 2, engine_revision: 4, created_at: '2026-11-02T02:00:00Z' }] : [],
  reviews: [], comments: [{ id: 'comment-one', at_seconds: 1, kind: 'human', revision: 2, notes: '已有修改意见' }], renders: [], publications: [] });
const bootstrap = () => ({ client_id: 'client-one', clients: [{ id: 'client-one', name: '汽车客户' }],
  channels: [{ id: 'channel-one', client_id: 'client-one', name: '小红书评论', platform: 'xiaohongshu', kind: 'comment', delivery_mode: 'simulation', enabled: true,
    capabilities: { can_sync: true, can_send: true } }],
  conversations: [{ id: 'conversation-one', client_id: 'client-one', channel_id: 'channel-one', platform: 'xiaohongshu', kind: 'comment', delivery_mode: 'simulation',
    subject: '了解汽车', author_name: '访客', status: 'pending', revision: 1, preview: '什么车型', updated_at: '2026-11-02T02:00:00Z', human_takeover: false }],
  knowledge: [{ id: 'knowledge-one', question: '车辆介绍', keywords: ['车型'], answer: '已核实的介绍', auto_send: true, enabled: true }],
  rules: { mode: 'assist', enabled: true, blocked_keywords: ['退款'] }, counts: { pending: 1, review: 0, handled: 0, failed: 0 }, capabilities: [] });
const detail = () => ({ ...bootstrap().conversations[0], messages: [{ id: 'message-one', direction: 'inbound', text: '什么车型', author_name: '访客', created_at: '2026-11-02T02:00:00Z' }],
  drafts: [{ id: 'draft-one', text: '已有草稿', source: 'knowledge_base', status: 'draft', confidence: 1 }], sends: [], events: [] });

function ui(file, permissions, { delivered = false } = {}) {
  let currentFrame, dirty = true, tree;
  const frames = new Map(), calls = [], pendingEffects = [], timers = new Map();
  const same = (a,b) => a && b && a.length === b.length && a.every((value,index) => Object.is(value,b[index]));
  const hook = () => { const index = currentFrame.cursor++; return [currentFrame.slots,index]; };
  const hooks = {
    useState(initial) { const [slots,index]=hook(); if (!slots[index]) slots[index]={value:typeof initial==='function'?initial():initial};
      return [slots[index].value,next=>{const value=typeof next==='function'?next(slots[index].value):next;if(!Object.is(value,slots[index].value)){slots[index].value=value;dirty=true;}}]; },
    useRef(initial) { const [slots,index]=hook(); if(!slots[index])slots[index]={current:initial};return slots[index]; },
    useCallback(fn,deps) {const [slots,index]=hook();if(!slots[index]||!same(slots[index].deps,deps))slots[index]={value:fn,deps};return slots[index].value;},
    useEffect(fn,deps) {const [slots,index]=hook();if(!slots[index]||!same(slots[index].deps,deps)){const previous=slots[index];slots[index]={deps};pendingEffects.push(()=>{previous?.cleanup?.();slots[index].cleanup=fn();});}},
  };
  const fetch = async (url,options={}) => {
    calls.push({url,method:options.method||'GET',options});
    const value = url.endsWith('/api/workshop/projects/video-one/renders') && options.method === 'POST'
      ? {id:'render-one',status:'queued',content_revision:2,engine_revision:4,created_at:'2026-11-02T02:00:00Z',updated_at:'2026-11-02T02:00:00Z'}
      : url.includes('/api/workshop/status') ? {state:'running',ui_url:'http://127.0.0.1:3100',version:'test'}
      : url.includes('/api/workshop/projects/video-one') ? project(delivered)
        : url.includes('/api/workshop/projects') ? {projects:[project(delivered)]}
          : url.includes('/conversations/') ? detail() : bootstrap();
    return {ok:true,status:200,json:async()=>value};
  };
  const environment = {URL,Headers,AbortController,Error,fetch,crypto:{randomUUID:()=> 'fixed'},
    sessionStorage:{getItem:()=>null},localStorage:{getItem:()=>null},
    window:{location:{pathname:'/'},setInterval(fn){const id=timers.size+1;timers.set(id,fn);return id;},clearInterval(id){timers.delete(id);},
      setTimeout(fn){const id=timers.size+1;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);},addEventListener(){},removeEventListener(){}},
    document:{visibilityState:'visible',getElementById:()=>null}};
  environment.setInterval = environment.window.setInterval;
  environment.clearInterval = environment.window.clearInterval;
  const scope={};runInNewContext(compile('../../web/frontend/src/lib/projectScope.ts'),{...environment,exports:scope});
  const library={};const libraryName=file==='VideoWorkshopPage'?'workshop':'interactions';
  runInNewContext(compile(`../../web/frontend/src/lib/${libraryName}.ts`),{...environment,exports:library,require:()=>scope});
  const permissionSource = readFileSync(new URL('../../web/frontend/src/components/ProjectSpace.tsx',import.meta.url),'utf8');
  const parsedPermissions = ts.createSourceFile('ProjectSpace.tsx',permissionSource,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  const permissionHook = parsedPermissions.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='useIpPermissions');
  const permissionModule={};
  runInNewContext(ts.transpileModule(permissionHook.getText(parsedPermissions),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,
    {exports:permissionModule,SpaceContext:{},useContext:()=>({current:{role:permissions.role}}),useAuth:()=>({user:{is_admin:permissions.isPlatformAdmin}})});
  const module={};const jsx=(type,props,key)=>({type,props,key});
  runInNewContext(compile(`../../web/frontend/src/components/${file}.tsx`),{...environment,exports:module,
    require:name=>name==='react'?hooks:name==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'fragment'}
      :name.endsWith('/ProjectSpace')?permissionModule:name.endsWith(`/${libraryName}`)?library
        :name.endsWith('/icons')?new Proxy({},{get:(_,key)=>key}):{}});
  const renderComponent=(type,props,path)=>{const frame=frames.get(path)||{cursor:0,slots:[]};frames.set(path,frame);frame.cursor=0;const previous=currentFrame;currentFrame=frame;
    const result=type(props);currentFrame=previous;return resolve(result,path);};
  const resolve=(node,path)=>{if(!node||typeof node!=='object')return node;if(typeof node.type==='function')return renderComponent(node.type,node.props,`${path}/${node.type.name}:${node.key||''}`);
    const children=node.props?.children;return {...node,props:{...node.props,children:Array.isArray(children)?children.flat(Infinity).map((child,index)=>resolve(child,`${path}/${index}`)):resolve(children,`${path}/child`)}};};
  const all=()=>{const result=[];const visit=node=>{if(!node||typeof node!=='object')return;result.push(node);[node.props?.children].flat(Infinity).forEach(visit);};visit(tree);return result;};
  const text=node=>typeof node==='string'||typeof node==='number'?String(node):[node?.props?.children].flat(Infinity).map(child=>child?text(child):'').join('');
  return {calls,permissions,all,text,
    button(name){return all().find(node=>node.type==='button'&&(node.props['aria-label']===name||text(node)===name));},
    async flush(){for(let count=0;count<35;count++){if(dirty){dirty=false;tree=renderComponent(module.default,{},'root');}pendingEffects.splice(0).forEach(fn=>fn());await new Promise(resolve=>setImmediate(resolve));if(!dirty&&!pendingEffects.length)return;}throw new Error('UI did not settle');},
    rerender(){dirty=true;},dispose(){for(const frame of frames.values())frame.slots.forEach(slot=>slot?.cleanup?.());timers.clear();}};
}

test('viewer interaction view retains records but has no sending, automation, or channel-write actions', async () => {
  const view=ui('InteractionCenterPage',{role:'viewer',canOperate:false,canManageAccounts:false,isPlatformAdmin:false});
  await view.flush();assert.ok(!view.button('载入模拟消息'));assert.ok(!view.button('＋ 新增客户'));
  view.button('打开会话：访客 · 了解汽车').props.onClick();await view.flush();
  assert.ok(!view.button('生成回复建议'));assert.ok(!view.button('模拟发送'));assert.ok(!view.button('人工接管'));
  assert.ok(view.all().some(node=>node.type==='textarea'&&node.props['aria-label']==='回复内容'&&(node.props.disabled||node.props.readOnly)));
  view.button('回复规则').props.onClick();await view.flush();
  assert.ok(!view.button('运行一轮规则'));assert.ok(!view.button('保存规则'));assert.ok(!view.button('＋ 新增知识'));
  assert.ok(view.all().filter(node=>['接待模式','转人工关键词'].includes(node.props?.['aria-label'])||node.props?.type==='checkbox').every(node=>node.props.disabled||node.props.readOnly));
  view.button('账号连接').props.onClick();await view.flush();assert.ok(!view.button('＋ 新增渠道'));assert.ok(!view.button('同步评论'));
  assert.ok(view.calls.every(call=>call.method==='GET'));view.dispose();
});

test('operators can work on interaction replies while account channel configuration remains owner-only', async () => {
  const view=ui('InteractionCenterPage',{role:'operator',canOperate:true,canManageAccounts:false,isPlatformAdmin:false});
  await view.flush();assert.ok(view.button('载入模拟消息'));
  view.button('打开会话：访客 · 了解汽车').props.onClick();await view.flush();assert.ok(view.button('生成回复建议'));
  view.button('生成回复建议').props.onClick();await view.flush();assert.ok(view.calls.some(call=>call.method==='POST'&&call.url.endsWith('/draft')));
  view.button('账号连接').props.onClick();await view.flush();assert.ok(!view.button('＋ 新增渠道'));view.dispose();
});


test('platform administrators assigned viewer on this IP still get a read-only studio', async () => {
  const view=ui('VideoWorkshopPage',{role:'viewer',canOperate:false,canManageAccounts:false,isPlatformAdmin:true});
  await view.flush();assert.equal(view.calls.some(call=>call.url.includes('/workshop/status')),false);
  assert.ok(!view.button('+ 新建视频项目'));
  view.button('进入项目：汽车视频').props.onClick();await view.flush();
  assert.equal(view.all().filter(node=>node.type==='iframe').length,0);
  assert.ok(!view.button('导出当前成片'));view.dispose();
});
