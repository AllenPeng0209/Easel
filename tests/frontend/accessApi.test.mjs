import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { randomUUID } from 'node:crypto';

const TEST_PASSWORD = randomUUID();

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const file = new URL('../../web/frontend/src/lib/access.ts', import.meta.url);
const source = existsSync(file) ? readFileSync(file, 'utf8') : '';
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});

function setup(pathname = '/', response = { ok: true, status: 200, data: {} }, initialUser = 'current-user') {
  const calls = [], events = [], eventDetails = [], exports = {};
  const markers = new Map(initialUser ? [['easel_authenticated_user',initialUser]] : []);
  const sessionStorage = { getItem: key => markers.get(key) ?? null,
    setItem: (key, value) => markers.set(key, String(value)) };
  const scope = {};
  const scopeSource = readFileSync(new URL('../../web/frontend/src/lib/projectScope.ts', import.meta.url), 'utf8');
  runInNewContext(ts.transpileModule(scopeSource, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,
    {exports:scope,sessionStorage,localStorage:{getItem:()=>null}});
  runInNewContext(outputText, {
    exports, Error, Headers, encodeURIComponent, sessionStorage, require:name=>name==='./projectScope'?scope:{},
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
    window: { location: { pathname }, dispatchEvent: event => { events.push(event.type); eventDetails.push(event.detail); } },
    fetch: async (url, options) => {
      calls.push({ url, options });
      return { ...response, json: async () => response.data };
    },
  });
  return { api: exports, calls, events, eventDetails, sessionStorage };
}

test('public registration uses no previous identity header and sends no extra privileges', async () => {
  const {api,calls}=setup('/',{ok:true,status:200,data:{user:{id:'new-user'}}},null);
  assert.equal(typeof api.registerAccount,'function');
  await api.registerAccount({username:'creator',display_name:'创作者',password:TEST_PASSWORD,is_admin:true,role:'owner'});
  assert.equal(calls[0].url,'/api/auth/register');
  assert.equal(new Headers(calls[0].options.headers).get('X-Easel-User'),null);
  assert.deepEqual(JSON.parse(calls[0].options.body),{username:'creator',display_name:'创作者',password:TEST_PASSWORD});
});

test('ownership transfer pins the expected owner and confirms the repository before submitting', async () => {
  const {api,calls}=setup();
  assert.equal(typeof api.transferOwnership,'function');
  await api.transferOwnership('ip one',{user_id:'new/owner',expected_owner_id:'old-owner',confirmation:'城市观察',is_admin:true});
  assert.equal(calls[0].url,'/api/projects/ip%20one/transfer');
  assert.deepEqual(JSON.parse(calls[0].options.body),{user_id:'new/owner',expected_owner_id:'old-owner',confirmation:'城市观察'});
  await api.leaveProject('ip one');
  assert.equal(calls[1].url,'/api/projects/ip%20one/leave');
  assert.equal(calls[1].options.method,'POST');
});

test('authentication uses same-origin cookies and preserves proxy paths without IP scope', async () => {
  const { api, calls } = setup('/workspace/index.html', { ok: true, status: 200, data: { user: { id: 'user-1' } } });
  assert.equal(typeof api.loginAccount, 'function');
  assert.equal((await api.loginAccount({ username: 'operator', password: TEST_PASSWORD })).user.id, 'user-1');
  assert.equal(calls[0].url, '/workspace/api/auth/login');
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.deepEqual(JSON.parse(calls[0].options.body), { username: 'operator', password: TEST_PASSWORD });
  assert.equal(new Headers(calls[0].options.headers).get('Content-Type'), 'application/json');
  assert.equal(new Headers(calls[0].options.headers).get('Authorization'), null);
});

for(const pathname of ['/workspace/login/','/workspace/register/'])test(`authentication from ${pathname} uses the application API rather than nesting it under the auth page`,async()=>{
  const {api,calls}=setup(pathname);
  await api.loginAccount({username:'creator',password:TEST_PASSWORD});
  assert.equal(calls[0].url,'/workspace/api/auth/login');
});

test('member operations encode each IP and user id and send only the intended membership update', async () => {
  const { api, calls } = setup();
  assert.equal(typeof api.changeMemberRole, 'function');
  await api.addMember('ip one', { user_id: 'user/one', role: 'viewer', is_admin: true });
  await api.changeMemberRole('ip one', 'user/one', 'operator');
  await api.removeMember('ip one', 'user/one');
  assert.equal(calls[0].url, '/api/projects/ip%20one/members');
  assert.deepEqual(JSON.parse(calls[0].options.body), { user_id: 'user/one', role: 'viewer' });
  assert.equal(calls[1].url, '/api/projects/ip%20one/members/user%2Fone');
  assert.deepEqual(JSON.parse(calls[1].options.body), { role: 'operator' });
  assert.equal(calls[1].options.method, 'PATCH');
  assert.equal(calls[2].options.method, 'DELETE');
});

test('an expired authenticated request signals AuthGate before returning a status-aware error', async () => {
  const { api, events } = setup('/', { ok: false, status: 401, data: { detail: '请重新登录' } });
  assert.equal(typeof api.fetchMembers, 'function');
  await assert.rejects(api.fetchMembers('default'), error => error.status === 401 && error.message === '请重新登录');
  assert.deepEqual(events, ['easel:auth-required']);
});

test('an initial cookie discovery without a known user shows ordinary login without reporting expiry', async () => {
  const { api, events } = setup('/', { ok: false, status: 401, data: { detail: '请先登录' } }, null);
  await assert.rejects(api.fetchAuthUser(), error => error.status === 401);
  assert.deepEqual(events, []);
});

test('a failed login keeps its form active and presents the server error without expiring the app again', async () => {
  const { api, events } = setup('/', { ok: false, status: 401, data: { detail: '用户名或密码错误' } });
  assert.equal(typeof api.loginAccount, 'function');
  await assert.rejects(api.loginAccount({ username: 'viewer', password: randomUUID() }), error => error.status === 401);
  assert.deepEqual(events, []);
});

test('permission failures retain the backend explanation and never impersonate another user', async () => {
  const { api, calls, events } = setup('/', { ok: false, status: 409, data: { detail: '至少保留一个 owner' } });
  assert.equal(typeof api.changeMemberRole, 'function');
  await assert.rejects(api.changeMemberRole('default', 'owner-1', 'viewer'), error => error.status === 409 && error.message === '至少保留一个 owner');
  assert.deepEqual(events, []);
  assert.equal(new Headers(calls[0].options.headers).get('X-User-Id'), null);
});

test('non-text error bodies do not leak internal structures into the UI', async () => {
  const { api } = setup('/', { ok: false, status: 500, data: { detail: { database: '/private/server/secret.sqlite' } } });
  assert.equal(typeof api.fetchUsers, 'function');
  await assert.rejects(api.fetchUsers(), error => error.status === 500 && !error.message.includes('secret.sqlite') && !error.message.includes('[object Object]'));
});

test('user directory and administration use their distinct real API endpoints', async () => {
  const { api, calls } = setup();
  assert.equal(typeof api.fetchUserDirectory, 'function');
  await api.fetchUserDirectory();
  await api.createUser({ username: 'viewer', display_name: '只读用户', password: TEST_PASSWORD, account_owner: 'spoofed-user' });
  await api.patchUser('user two', { active: false });
  assert.equal(calls[0].url, '/api/users/directory');
  assert.equal(calls[1].url, '/api/users');
  assert.deepEqual(JSON.parse(calls[1].options.body), { username: 'viewer', display_name: '只读用户', password: TEST_PASSWORD });
  assert.equal(calls[2].url, '/api/users/user%20two');
  assert.deepEqual(JSON.parse(calls[2].options.body), { active: false });
});

test('private requests retain the page identity after another login and reject caller-supplied identity overrides', async () => {
  const { api, calls, sessionStorage } = setup('/', {ok:true,status:200,data:{}}, 'old-tab-user');
  sessionStorage.setItem('easel_authenticated_user', 'old-tab-user');
  const pending = api.accessRequest('/api/users', { headers: { 'X-Easel-User': 'forged-user' } });
  sessionStorage.setItem('easel_authenticated_user', 'new-tab-user');
  await pending;
  assert.equal(new Headers(calls[0].options.headers).get('X-Easel-User'), 'old-tab-user');
  await api.logoutAccount();
  assert.equal(new Headers(calls[1].options.headers).get('X-Easel-User'), 'old-tab-user');
});

test('cookie identity discovery and public authentication remove an untrusted caller identity header', async () => {
  const { api, calls, sessionStorage } = setup();
  sessionStorage.setItem('easel_authenticated_user', 'old-tab-user');
  for (const path of ['/api/auth/me', '/api/auth/login', '/api/auth/bootstrap', '/api/auth/setup']) {
    await api.accessRequest(path, { headers: { 'X-Easel-User': 'forged-user' } });
  }
  assert.ok(calls.every(call => new Headers(call.options.headers).get('X-Easel-User') === null));
});

test('a late unauthorized response identifies the request owner even after the tab marker changes', async () => {
  const { api, eventDetails, sessionStorage } = setup('/', { ok: false, status: 401, data: { detail: '登录已失效' } }, 'old-tab-user');
  sessionStorage.setItem('easel_authenticated_user', 'old-tab-user');
  const pending = api.fetchUsers();
  sessionStorage.setItem('easel_authenticated_user', 'new-tab-user');
  await assert.rejects(pending);
  assert.equal(eventDetails[0]?.userId, 'old-tab-user');
});


test('a private callback cannot lose its page identity when the tab marker is cleared', async () => {
  const {api,calls,sessionStorage}=setup('/',{ok:true,status:200,data:{}},'original-user');
  sessionStorage.setItem('easel_authenticated_user','');
  await api.changeMemberRole('ip-one','target-user','viewer');
  assert.equal(new Headers(calls[0].options.headers).get('X-Easel-User'),'original-user');
});

test('an anonymous module cannot send private operations before the authenticated page reload', async () => {
  const {api,calls,events,sessionStorage}=setup('/',{ok:true,status:200,data:{}},null);
  sessionStorage.setItem('easel_authenticated_user','new-user');
  await assert.rejects(api.createUser({username:'operator',password:TEST_PASSWORD}),error=>error.status===401);
  assert.equal(calls.length,0);
  assert.deepEqual(events,[]);
});
