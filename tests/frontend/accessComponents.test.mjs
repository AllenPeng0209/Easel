import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));
const owner = {id:'owner',username:'owner',display_name:'主理人',is_admin:true,active:true};

function harness(file, component, context, api, initialMarker = 'owner', pathname = '/', hash = '') {
  const path = new URL(`../../web/frontend/src/components/${file}.tsx`, import.meta.url);
  const source = existsSync(path) ? readFileSync(path, 'utf8') : '';
  const compiled = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  let cursor = 0;
  let effectCursor = 0;
  let effects = [];
  const states = [], effectStates = [], listeners = new Map(), markers = new Map(initialMarker ? [['easel_authenticated_user',initialMarker]] : []), reloads = [], navigations=[];
  const jsx = (type, props) => ({type,props});
  const hooks = {
    createContext: () => ({Provider:'provider'}), useContext: () => context,
    useCallback(fn,deps) { const index=cursor++;const old=states[index];if(old&&deps.every((dep,i)=>dep===old.deps[i]))return old.fn;states[index]={fn,deps};return fn; },
    useRef(initial) {const index=cursor++;if(!(index in states))states[index]={current:initial};return states[index];},
    useState(initial) { const index=cursor++; if (!(index in states)) states[index]=typeof initial==='function'?initial():initial;
      return [states[index],value=>{states[index]=typeof value==='function'?value(states[index]):value;}]; },
    useEffect(fn,deps) { const index=effectCursor++; const old=effectStates[index];
      if (!old || !deps || deps.some((dep,i)=>dep!==old.deps[i])) effects.push(()=>{old?.cleanup?.();effectStates[index]={deps,cleanup:fn()};}); },
  };
  const exports = {};
  const sessionStorage={getItem:key=>markers.get(key)??null,setItem:(key,value)=>markers.set(key,value),removeItem:key=>markers.delete(key)};
  const modules={};
  const environment={console,Error,Promise,document:{body:{},activeElement:null},
    sessionStorage,
    window:{location:{pathname,hash,reload:()=>reloads.push(true),assign:url=>navigations.push(url),replace:url=>navigations.push(url)},addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)},
    setInterval:()=>1,clearInterval(){},
  };
  const resolve=name=>{
    if(name==='react')return hooks;
    if(name==='react-dom')return {createPortal:child=>child};
    if(name==='react/jsx-runtime')return {jsx,jsxs:jsx,Fragment:'fragment'};
    if(/\.(png|jpe?g|webp|avif)$/.test(name))return {__esModule:true,default:name};
    if(name.includes('LandingPage')){
      if(!modules.landing){const url=new URL('../../web/frontend/src/components/LandingPage.tsx',import.meta.url);modules.landing={};
        if(existsSync(url))runInNewContext(ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,{...environment,exports:modules.landing,require:resolve});}
      return modules.landing;
    }
    return name.includes('/access')?api:name.includes('AuthGate')?{useAuth:()=>context,useAccessDialog(){}}:{};
  };
  runInNewContext(compiled,{...environment,exports,require:resolve});
  const flatten = tree => {const result=[];const walk=node=>{if(!node||typeof node!=='object')return;result.push(node);
    if(typeof node.type==='function'){walk(node.type(node.props));return;}[node.props?.children].flat(Infinity).forEach(walk);};walk(tree);return result;};
  return {exports,listeners,markers,reloads,navigations,sessionStorage,render(props={}) {assert.equal(typeof exports[component],'function',`${component} must be implemented`);cursor=0;effectCursor=0;
    const tree=exports[component](props); const tasks=effects;effects=[];tasks.forEach(fn=>fn());return {tree,nodes:flatten(tree)};}};
}

test('anonymous visitors see the website introduction with real separate login and registration links',async()=>{
  class ApiError extends Error {constructor(status){super('请先登录');this.status=status;}}
  const ui=harness('AuthGate','default',null,{AccessError:ApiError,fetchAuthBootstrap:async()=>({configured:true}),fetchAuthUser:async()=>{throw new ApiError(401);}});
  ui.render();await settle();let view=ui.render();
  assert.ok(!view.nodes.some(node=>node.type==='form'),'The brand homepage should not be an always-open sign-in form');
  assert.equal(view.nodes.find(node=>node.type==='a' && node.props['data-auth-mode']==='login')?.props.href,'/login/');
  assert.equal(view.nodes.find(node=>node.type==='a' && node.props['data-auth-mode']==='register')?.props.href,'/register/');
  assert.ok(!view.nodes.some(node=>node.props?.role==='dialog'));
});

test('registration remains directly reachable before session discovery finishes',async()=>{
  class ApiError extends Error {constructor(status){super('请先登录');this.status=status;}}
  let resolveBootstrap;
  const ui=harness('AuthGate','default',null,{AccessError:ApiError,fetchAuthBootstrap:()=>new Promise(resolve=>{resolveBootstrap=resolve;}),fetchAuthUser:async()=>{throw new ApiError(401);}});
  let view=ui.render();
  const register=view.nodes.find(node=>node.type==='a' && node.props['data-auth-mode']==='register');
  assert.equal(register?.props.href,'/register/');
  resolveBootstrap({configured:true});await settle();view=ui.render();
  assert.equal(view.nodes.find(node=>node.type==='a' && node.props['data-auth-mode']==='register')?.props.href,'/register/');
});

test('opening a workspace waits for identity without downloading homepage artwork', () => {
  const ui=harness('AuthGate','default',null,{fetchAuthBootstrap:()=>new Promise(()=>{})},'','/','#workspace');
  const view=ui.render();
  assert.ok(!view.nodes.some(node=>node.type==='img'),'Homepage artwork must not compete with authenticated workspace chunks');
  assert.ok(view.nodes.some(node=>node.type==='h2' && node.props.children==='正在打开你的空间…'));
});

test('interactive product preview switches content locally without executing a real creative task',()=>{
  const ui=harness('LandingPage','default',null,{});
  let view=ui.render();
  const publish=view.nodes.find(node=>node.type==='button' && node.props.role==='tab' && node.props['data-preview-tab']==='publish');
  assert.ok(publish,'Visitors need a usable product preview');publish.props.onClick();
  view=ui.render();
  assert.ok(view.nodes.some(node=>node.props?.role==='tabpanel' && node.props['data-preview-panel']==='publish'));
  assert.equal(view.nodes.find(node=>node.type==='a' && node.props['data-auth-mode']==='register')?.props.href,'/register/');
});

test('the direct registration page shows its own form without administrator setup', async () => {
  class ApiError extends Error { constructor(status) { super('请先登录'); this.status=status; } }
  const ui=harness('AuthGate','default',null,{AccessError:ApiError,fetchAuthBootstrap:async()=>({configured:true}),
    fetchAuthUser:async()=>{throw new ApiError(401);}},'','/register/');
  ui.render({children:'PRIVATE APP'}); await settle();
  let view=ui.render();
  assert.ok(view.nodes.some(node=>node.type==='input' && node.props.name==='display_name'));
  assert.ok(view.nodes.some(node=>node.type==='input' && node.props.name==='confirm_password'));
  assert.ok(!view.nodes.some(node=>node.type==='input' && node.props.type==='checkbox'));
});

test('registration checks password confirmation and never mounts an old users workspace', async () => {
  class ApiError extends Error { constructor(status) { super('请先登录'); this.status=status; } }
  const calls=[];
  const ui=harness('AuthGate','default',null,{AccessError:ApiError,fetchAuthBootstrap:async()=>({configured:true}),
    fetchAuthUser:async()=>{throw new ApiError(401);},registerAccount:async input=>{calls.push(input);return {user:{...owner,id:'new-user',is_admin:false}};}},'','/register/');
  ui.render({children:'PRIVATE APP'}); await settle();
  let view=ui.render();
  for(const [name,value] of Object.entries({username:'new-creator',display_name:'创作者',password:'fixture123',confirm_password:'different-password'})) {
    view.nodes.find(node=>node.type==='input' && node.props.name===name).props.onChange({target:{value}});
  }
  view=ui.render(); await view.nodes.find(node=>node.type==='form').props.onSubmit({preventDefault(){}});
  assert.equal(calls.length,0);
  view=ui.render(); assert.ok(view.nodes.some(node=>node.props?.role==='alert'));
  view.nodes.find(node=>node.type==='input' && node.props.name==='confirm_password').props.onChange({target:{value:'fixture123'}});
  view=ui.render(); await view.nodes.find(node=>node.type==='form').props.onSubmit({preventDefault(){}});
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[{username:'new-creator',display_name:'创作者',password:'fixture123'}]);
  assert.equal(ui.markers.get('easel_authenticated_user'),'new-user');
  assert.deepEqual(ui.navigations,['/#projects']);
  assert.ok(!ui.render({children:'PRIVATE APP'}).nodes.some(node=>node.props?.children==='PRIVATE APP'));
});

test('owner membership is a fixed badge and ordinary grants offer only collaborator roles', async () => {
  const api={fetchMembers:async()=>[{user_id:'owner',username:'owner',display_name:'主理人',role:'owner',active:true}],
    fetchUserDirectory:async()=>[{id:'operator',username:'operator',display_name:'运营'}]};
  const ui=harness('AccessMembersPanel','default',{user:owner},api);
  const props={projectId:'ip-one',projectName:'汽车观察',onClose(){}};
  ui.render(props);await settle();
  const view=ui.render(props);
  assert.ok(!view.nodes.some(node=>node.type==='option' && node.props.value==='owner'));
  assert.ok(!view.nodes.some(node=>node.type==='select' && node.props['aria-label']==='主理人 的角色'));
  assert.ok(!view.nodes.some(node=>node.props?.['data-action']==='remove-member'));
});

test('ownership transfer requires the current project name and the expected Owner', async () => {
  const calls=[];
  const repository={id:'ip-one',name:'城市观察',description:'城市生活',owner:{id:'owner',username:'owner',display_name:'主理人'},created_by:{id:'owner'},role:'owner'};
  const api={fetchProject:async()=>repository,transferOwnership:async(id,input)=>{calls.push({id,input});return {};} };
  const ui=harness('RepositorySettings','default',{user:owner},api);
  const props={projectId:'ip-one',canManage:true,members:[{user_id:'owner',role:'owner',active:true},
    {user_id:'next',username:'next',display_name:'新 Owner',role:'operator',active:true}],onChanged(){},onTransferred:async()=>{}};
  ui.render(props);await settle();let view=ui.render(props);
  const target=view.nodes.find(node=>node.type==='select' && node.props.name==='new_owner');
  assert.ok(target,'Owner needs a dedicated transfer form');target.props.onChange({target:{value:'next'}});
  view=ui.render(props);
  assert.ok(view.nodes.find(node=>node.type==='button' && node.props['data-action']==='transfer-ownership').props.disabled);
  view.nodes.find(node=>node.type==='input' && node.props.name==='confirmation').props.onChange({target:{value:'城市观察'}});
  view=ui.render(props);await view.nodes.find(node=>node.type==='form' && node.props['data-form']==='transfer').props.onSubmit({preventDefault(){}});
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[{id:'ip-one',input:{user_id:'next',expected_owner_id:'owner',confirmation:'城市观察'}}]);
});

test('viewers cannot see settings write controls and must confirm leaving their project',async()=>{
  const user={...owner,id:'viewer',is_admin:false};
  const api={fetchProject:async()=>({id:'ip-one',name:'城市观察',description:'城市生活',owner:{id:'owner',username:'owner'}})};
  const ui=harness('RepositorySettings','default',{user},api);
  const props={projectId:'ip-one',canManage:false,members:[],onChanged(){},onTransferred:async()=>{}};
  ui.render(props);await settle();const view=ui.render(props);
  assert.ok(!view.nodes.some(node=>node.type==='form'));
  assert.ok(!view.nodes.some(node=>node.props?.['data-action']==='transfer-ownership'));
  assert.ok(view.nodes.some(node=>node.type==='button' && node.props['data-action']==='request-leave'));
  assert.ok(!view.nodes.some(node=>node.type==='button' && node.props['data-action']==='confirm-leave'));
});

test('authenticated application is withheld until initialization and login succeed', async () => {
  let resolveBootstrap;
  const api={fetchAuthBootstrap:()=>new Promise(resolve=>{resolveBootstrap=resolve;}),fetchAuthUser:async()=>owner};
  const ui=harness('AuthGate','default',null,api);
  assert.ok(!ui.render({children:'PRIVATE APP'}).nodes.some(node=>node.props?.children==='PRIVATE APP'));
  resolveBootstrap({configured:true});await settle();
  const ready=ui.render({children:'PRIVATE APP'});
  assert.ok(ready.nodes.some(node=>node.type==='provider'));
  assert.equal(ready.tree.props.value.user.id,'owner');
});

test('a session-expired event immediately unmounts the authenticated application', async () => {
  const api={fetchAuthBootstrap:async()=>({configured:true}),fetchAuthUser:async()=>owner};
  const ui=harness('AuthGate','default',null,api);
  ui.render({children:'PRIVATE APP'});await settle();
  assert.ok(ui.render({children:'PRIVATE APP'}).nodes.some(node=>node.type==='provider'));
  ui.listeners.get('easel:auth-required')({});
  const expired=ui.render({children:'PRIVATE APP'});
  assert.ok(!expired.nodes.some(node=>node.type==='provider'));
  assert.deepEqual(ui.navigations,['/login/']);
});

test('an authenticated visit to a nested login page returns to the project homepage',async()=>{
  const ui=harness('AuthGate','default',null,{fetchAuthBootstrap:async()=>({configured:true}),fetchAuthUser:async()=>owner},'owner','/studio/login/');
  ui.render({children:'PRIVATE APP'});await settle();
  assert.deepEqual(ui.navigations,['/studio/#projects']);
  assert.ok(!ui.render({children:'PRIVATE APP'}).nodes.some(node=>node.props?.children==='PRIVATE APP'));
});

test('the first-owner form submits explicit credentials and does not authenticate until the server replies', async () => {
  const calls=[];let resolveSetup;
  const api={fetchAuthBootstrap:async()=>({configured:false}),setupAccount:payload=>{calls.push(payload);return new Promise(resolve=>{resolveSetup=resolve;});}};
  const ui=harness('AuthGate','default',null,api);
  ui.render({children:'PRIVATE APP'});await settle();
  let rendered=ui.render({children:'PRIVATE APP'});
  const inputs=rendered.nodes.filter(node=>node.type==='input');
  inputs.find(node=>node.props.name==='username').props.onChange({target:{value:'new-owner'}});
  inputs.find(node=>node.props.name==='display_name').props.onChange({target:{value:'新主理人'}});
  inputs.find(node=>node.props.name==='password').props.onChange({target:{value:'long-password-123'}});
  rendered=ui.render({children:'PRIVATE APP'});
  void rendered.nodes.find(node=>node.type==='form').props.onSubmit({preventDefault(){}});
  assert.equal(calls[0].username,'new-owner');assert.equal(calls[0].password,'long-password-123');
  assert.ok(!ui.render({children:'PRIVATE APP'}).nodes.some(node=>node.type==='provider'));
  resolveSetup({user:owner});await settle();
  assert.ok(ui.render({children:'PRIVATE APP'}).nodes.some(node=>node.type==='provider'));
});

test('viewer members can inspect membership but cannot render member-write controls', async () => {
  const user={...owner,id:'viewer',is_admin:false};
  const api={fetchMembers:async()=>[{user_id:'owner',username:'owner',display_name:'主理人',role:'owner',active:true},
    {user_id:'viewer',username:'viewer',display_name:'访客',role:'viewer',active:true}],fetchUserDirectory:async()=>[]};
  const ui=harness('AccessMembersPanel','default',{user},api);
  ui.render({projectId:'ip-one',projectName:'汽车观察',onClose(){}});await settle();
  const view=ui.render({projectId:'ip-one',projectName:'汽车观察',onClose(){}});
  assert.ok(view.nodes.some(node=>node.props?.role==='dialog'));
  assert.ok(!view.nodes.some(node=>node.type==='form' || node.type==='select'));
  assert.ok(!view.nodes.some(node=>node.type==='button' && node.props?.['data-action']==='remove-member'));
});

test('an owner submits a selected user and role to this IP and exposes last-owner errors', async () => {
  const added=[];
  const api={fetchMembers:async()=>[{user_id:'owner',username:'owner',display_name:'主理人',role:'owner',active:true}],
    fetchUserDirectory:async()=>[{id:'operator',username:'operator',display_name:'运营'}],
    addMember:async(id,payload)=>{added.push({id,payload});throw new Error('至少保留一个 owner');}};
  const ui=harness('AccessMembersPanel','default',{user:owner},api);
  ui.render({projectId:'ip-one',projectName:'汽车观察',onClose(){}});await settle();
  let view=ui.render({projectId:'ip-one',projectName:'汽车观察',onClose(){}});
  view.nodes.find(node=>node.type==='select' && node.props.name==='user_id').props.onChange({target:{value:'operator'}});
  view.nodes.find(node=>node.type==='select' && node.props.name==='role').props.onChange({target:{value:'operator'}});
  view=ui.render({projectId:'ip-one',projectName:'汽车观察',onClose(){}});
  await view.nodes.find(node=>node.type==='form').props.onSubmit({preventDefault(){}});
  assert.equal(added[0].id,'ip-one');assert.equal(added[0].payload.user_id,'operator');assert.equal(added[0].payload.role,'operator');
  assert.ok(ui.render({projectId:'ip-one',projectName:'汽车观察',onClose(){}}).nodes.some(node=>node.props?.role==='alert' && node.props.children==='至少保留一个 owner'));
});


test('a newly authenticated identity reloads before mounting the previous user workspace', async () => {
  const calls=[];
  const ui=harness('AuthGate','default',null,{fetchAuthBootstrap:async()=>({configured:true}),fetchAuthUser:async()=>owner},'previous-user');
  ui.render({children:'PRIVATE APP',onAuthenticated:user=>calls.push(user?.id)});await settle();
  const frame=ui.render({children:'PRIVATE APP'});
  assert.equal(ui.reloads.length,1);
  assert.equal(ui.markers.get('easel_authenticated_user'),'owner');
  assert.ok(!frame.nodes.some(node=>node.type==='provider'));
  assert.deepEqual(calls,['owner']);
});

for (const admin of [true,false]) test(`${admin ? 'administrator' : 'member'} footer offers logout without personal password controls`, async () => {
  const logouts=[];
  const ui=harness('AuthGate','AuthAccountMenu',{user:{...owner,is_admin:admin},logout:async()=>{logouts.push(true);}},{ });
  const rendered=ui.render();
  const actions=rendered.nodes.filter(node=>node.type==='button');
  assert.deepEqual(actions.map(node=>node.props['aria-label'] || node.props.children),admin ? ['用户管理','退出'] : ['退出']);
  await actions.find(node=>node.props.children==='退出').props.onClick();
  await settle();
  assert.equal(logouts.length,1);
});

test('logout clears the mounted space even when its session was already revoked', async () => {
  class ApiError extends Error {constructor(status,message){super(message);this.status=status;}}
  const ui=harness('AuthGate','default',null,{AccessError:ApiError,fetchAuthBootstrap:async()=>({configured:true}),fetchAuthUser:async()=>owner,
    logoutAccount:async()=>{throw new ApiError(401,'登录已失效');}});
  ui.render({children:'PRIVATE APP'});await settle();
  const current=ui.render({children:'PRIVATE APP'});
  await current.tree.props.value.logout();
  assert.equal(ui.reloads.length,1);
  assert.equal(ui.markers.get('easel_authenticated_user'),undefined);
  assert.ok(!ui.render({children:'PRIVATE APP'}).nodes.some(node=>node.type==='provider'));
});

test('a late unauthorized response belonging to an older user cannot sign out the new user', async () => {
  const ui=harness('AuthGate','default',null,{fetchAuthBootstrap:async()=>({configured:true}),fetchAuthUser:async()=>owner});
  ui.render({children:'PRIVATE APP'});await settle();
  ui.render({children:'PRIVATE APP'});
  ui.listeners.get('easel:auth-required')({detail:{userId:'previous-user'}});
  const current=ui.render({children:'PRIVATE APP'});
  assert.equal(current.tree.props.value.user.id,'owner');
  assert.equal(ui.markers.get('easel_authenticated_user'),'owner');
});

test('a failed identity-namespace switch never mounts the previous user workspace', async () => {
  const ui=harness('AuthGate','default',null,{fetchAuthBootstrap:async()=>({configured:true}),fetchAuthUser:async()=>owner},'previous-user');
  ui.sessionStorage.setItem=()=>{throw new Error('Storage blocked');};
  ui.render({children:'PRIVATE APP'});await settle();
  const current=ui.render({children:'PRIVATE APP'});
  assert.ok(!current.nodes.some(node=>node.type==='provider'));
  assert.ok(current.nodes.some(node=>node.props?.role==='alert'));
});
