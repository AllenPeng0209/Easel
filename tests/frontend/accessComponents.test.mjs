import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));
const owner = {id:'owner',username:'owner',display_name:'主理人',is_admin:true,active:true};

function harness(file, component, context, api, initialMarker = 'owner') {
  const path = new URL(`../../web/frontend/src/components/${file}.tsx`, import.meta.url);
  const source = existsSync(path) ? readFileSync(path, 'utf8') : '';
  const compiled = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  let cursor = 0;
  let effectCursor = 0;
  let effects = [];
  const states = [], effectStates = [], listeners = new Map(), markers = new Map(initialMarker ? [['easel_authenticated_user',initialMarker]] : []), reloads = [];
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
  runInNewContext(compiled, {exports,console,Error,Promise,document:{body:{},activeElement:null},
    sessionStorage,
    window:{location:{pathname:'/',reload:()=>reloads.push(true)},addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)},
    setInterval:()=>1,clearInterval(){},
    require:name=>name==='react'?hooks:name==='react-dom'?{createPortal:child=>child}:name==='react/jsx-runtime'?{jsx,jsxs:jsx,Fragment:'fragment'}
      :name.includes('/access')?api:name.includes('AuthGate')?{useAuth:()=>context,useAccessDialog(){}}:{},
  });
  const flatten = tree => {const result=[];const walk=node=>{if(!node||typeof node!=='object')return;result.push(node);[node.props?.children].flat(Infinity).forEach(walk);};walk(tree);return result;};
  return {exports,listeners,markers,reloads,sessionStorage,render(props={}) {assert.equal(typeof exports[component],'function',`${component} must be implemented`);cursor=0;effectCursor=0;
    const tree=exports[component](props); const tasks=effects;effects=[];tasks.forEach(fn=>fn());return {tree,nodes:flatten(tree)};}};
}

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
  assert.ok(expired.nodes.some(node=>node.type==='form'));
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
