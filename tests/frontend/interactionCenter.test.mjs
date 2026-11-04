import { runWithProjectScope } from './projectScopeHarness.mjs';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const lib = new URL('../../web/frontend/src/lib/interactions.ts', import.meta.url);
const api = {};
const calls = [];
let response = () => ({ ok: true, status: 200, data: fixture() });
runWithProjectScope(ts.transpileModule(existsSync(lib) ? readFileSync(lib, 'utf8') : '', {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports: api, URL, Headers, Error, sessionStorage: { getItem: () => null }, localStorage: { getItem: () => null }, fetch: async (url, options = {}) => {
  calls.push({ url, options }); const result = await response(url, options);
  return { ok: result.ok, status: result.status, json: async () => result.data };
} });

function fixture(patch = {}) {
  return { client_id: 'client-1', clients: [{ id: 'client-1', name: '家庭教育工作区' }, { id: 'client-2', name: '另一客户' }],
    channels: [{ id: 'channel-1', client_id: 'client-1', name: '小红书评论验收', platform: 'xiaohongshu', kind: 'comment', delivery_mode: 'simulation', status: 'simulation', enabled: true }],
    conversations: [{ id: 'conv-1', client_id: 'client-1', channel_id: 'channel-1', platform: 'xiaohongshu', kind: 'comment', delivery_mode: 'simulation',
      subject: '了解亲子沟通课程', author_name: '模拟访客', status: 'pending', revision: 1, preview: '想了解课程', updated_at: '2026-11-02T02:00:00Z', human_takeover: false }],
    knowledge: [], rules: { mode: 'assist', enabled: false, blocked_keywords: ['退款'] }, counts: { pending: 1, review: 0, handled: 0, failed: 0 }, capabilities: [], ...patch };
}
function detail(patch = {}) {
  return { ...fixture().conversations[0], messages: [{ id: 'message-1', external_id: 'incoming-1', direction: 'inbound', text: '想了解课程', author_name: '模拟访客', created_at: '2026-11-02T02:00:00Z', simulated: true }], drafts: [], events: [], sends: [], ...patch };
}

function view() {
  const file = new URL('../../web/frontend/src/components/InteractionCenterPage.tsx', import.meta.url);
  const compiled = ts.transpileModule(existsSync(file) ? readFileSync(file, 'utf8') : '', { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const slots = []; let cursor = 0; let dirty = true; let effects = []; let tree;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const hooks = {
    useState(initial) { const i = cursor++; if (!slots[i]) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, next => { const value = typeof next === 'function' ? next(slots[i].value) : next;
        if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; } }]; },
    useRef(initial) { const i = cursor++; if (!slots[i]) slots[i] = { current: initial }; return slots[i]; },
    useCallback(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { value: fn, deps }; return slots[i].value; },
    useEffect(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) { const old = slots[i]; slots[i] = { deps };
      effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); } },
  };
  const module = {}; const jsx = (type, props, key) => ({ type, props, key });
  runInNewContext(compiled, { exports: module, URL, Error, AbortController, crypto: { randomUUID: () => 'fixed-op' },
    window: { setInterval: () => 1, clearInterval() {}, addEventListener() {}, removeEventListener() {} },
    document: { visibilityState: 'visible', getElementById: () => null },
    require: name => name === 'react' ? hooks : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment: 'fragment' }
      : name.endsWith('/interactions') ? api : name.endsWith('/ProjectSpace') ? { useIpPermissions: () => ({ role: 'owner', canOperate: true, canManageAccounts: true, isPlatformAdmin: true }) }
      : name.endsWith('/icons') ? new Proxy({}, { get: (_, name) => name }) : {},
  });
  const findAll = predicate => { const found = []; const visit = node => { if (!node || typeof node !== 'object') return;
    if (predicate(node)) found.push(node); const children = node.props?.children;
    (Array.isArray(children) ? children.flat(Infinity) : [children]).forEach(visit); }; visit(tree); return found; };
  const text = node => !node ? '' : typeof node === 'string' || typeof node === 'number' ? String(node)
    : (Array.isArray(node.props?.children) ? node.props.children.flat(Infinity) : [node.props?.children]).map(text).join('');
  return { findAll, text, button(name) { const item = findAll(n => n.type === 'button' && (n.props['aria-label'] === name || text(n) === name))[0]; assert.ok(item, `Missing button ${name}`); return item; },
    async flush() { for (let count = 0; count < 30; count++) { if (dirty) { cursor = 0; dirty = false; tree = module.default(); }
      const pending = effects; effects = []; pending.forEach(fn => fn()); await new Promise(resolve => setImmediate(resolve));
      if (!dirty && !effects.length) return; } throw new Error('UI did not settle'); },
    dispose() { slots.forEach(slot => slot?.cleanup?.()); } };
}

test('bootstrap scopes the workspace and never creates simulated messages on read', async () => {
  const before = calls.length;
  await api.fetchInteractionBootstrap('client-2');
  assert.equal(calls.at(-1).url, '/api/interactions/bootstrap?client_id=client-2');
  assert.equal(calls.at(-1).options.method || 'GET', 'GET');
  assert.equal(calls.length, before + 1);
});

test('nondefault project requests remain inside the original project API scope', async () => {
  const scoped = {}; let called = '';
  runWithProjectScope(ts.transpileModule(readFileSync(lib, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText,
    { exports: scoped, URL, Headers, sessionStorage: { getItem: key => key === 'easel_project' ? 'customer-project' : null }, localStorage: { getItem: () => null }, fetch: async url => { called = url; return { ok: true, json: async () => fixture() }; } });
  await scoped.fetchInteractionBootstrap('client-2');
  assert.equal(called, '/api/projects/customer-project/interactions/bootstrap?client_id=client-2');
});

test('saving full server rules serializes only writable fields and preserves the caller workspace', async () => {
  response = () => ({ ok: true, status: 200, data: fixture() });
  await api.updateInteractionRules('client-1', { id: 'server-rule-id', client_id: 'other-client',
    mode: 'auto_faq', enabled: true, blocked_keywords: ['退款'], max_auto_per_hour: 20,
    max_auto_per_run: 10, updated_at: '2026-11-02T02:00:00Z' });
  const call = calls.at(-1);
  assert.equal(call.url, '/api/interactions/rules');
  assert.equal(call.options.method, 'PUT');
  assert.deepEqual(JSON.parse(call.options.body), { client_id: 'client-1', mode: 'auto_faq',
    enabled: true, blocked_keywords: ['退款'], max_auto_per_hour: 20, max_auto_per_run: 10 });
});

test('conversation filters combine platform, kind, status and search', () => {
  const rows = [...fixture().conversations, { ...fixture().conversations[0], id: 'other', platform: 'douyin', status: 'review' }];
  assert.deepEqual(Array.from(api.filterInteractionConversations(rows, { platform: 'xiaohongshu', kind: 'comment', status: 'pending', query: '课程' })).map(x => x.id), ['conv-1']);
});

test('failed cases stay in the pending queue instead of disappearing from the three-state inbox', () => {
  const rows = [{ ...fixture().conversations[0], status: 'failed' }];
  assert.equal(api.filterInteractionConversations(rows, { status: 'pending' }).length, 1);
});

test('landing shows explicit simulation entry and does not silently seed or open a conversation', async () => {
  response = () => ({ ok: true, status: 200, data: fixture() }); const before = calls.length; const ui = view(); await ui.flush();
  ui.button('载入模拟消息'); assert.ok(ui.findAll(n => n.props?.['aria-label'] === '会话列表').length);
  assert.equal(calls.slice(before).some(x => x.url.includes('/simulation')), false);
  assert.equal(calls.slice(before).some(x => x.url.includes('/conversations/')), false); ui.dispose();
});

test('opening a conversation loads only that workspace detail and reveals the simulation label', async () => {
  response = url => ({ ok: true, status: 200, data: url.includes('/conversations/') ? detail() : fixture() });
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  assert.equal(calls.at(-1).url, '/api/interactions/conversations/conv-1?client_id=client-1');
  assert.ok(ui.findAll(n => n.props?.['aria-label'] === '模拟验收会话').length); ui.dispose();
});

test('workspace switching drops old conversation data before the next response', async () => {
  response = url => ({ ok: true, status: 200, data: url.includes('/conversations/') ? detail() : fixture() });
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  const selector = ui.findAll(n => n.type === 'select' && n.props['aria-label'] === '客户工作区')[0];
  response = () => new Promise(() => {}); selector.props.onChange({ target: { value: 'client-2' } }); await ui.flush();
  assert.equal(ui.findAll(n => n.props?.['aria-label'] === '模拟验收会话').length, 0); ui.dispose();
});

test('an unapproved draft cannot be sent', async () => {
  response = url => ({ ok: true, status: 200, data: url.includes('/conversations/') ? detail({ drafts: [{ id: 'draft-1', text: '课程介绍', source: 'knowledge_base', status: 'draft', confidence: 1 }] }) : fixture() });
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  assert.equal(ui.button('模拟发送').props.disabled, true); ui.dispose();
});

test('unknown delivery blocks sending another message while retaining the receipt', async () => {
  response = url => ({ ok: true, status: 200, data: url.includes('/conversations/') ? detail({ drafts: [{ id: 'draft-1', text: '课程介绍', source: 'human', status: 'approved', confidence: 1 }],
    sends: [{ operation_id: 'op-old', status: 'unknown', mode: 'simulation', message: '待核对', created_at: '' }] }) : fixture() });
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  assert.equal(ui.button('模拟发送').props.disabled, true); assert.ok(ui.findAll(n => n.props?.role === 'status').some(n => ui.text(n).includes('待核对'))); ui.dispose();
});

test('reply generation, edit, approval and simulated send remain separate explicit actions', async () => {
  let current = detail();
  response = (url, options) => {
    if (url.includes('/conversations/') && url.endsWith('/draft')) current = detail({ revision: 2, status: 'review', drafts: [{ id: 'draft-1', text: '知识库建议', source: 'knowledge_base', status: 'draft', confidence: 1 }] });
    else if (url.endsWith('/drafts/draft-1') && options.method === 'PATCH') current = { ...current, revision: 3,
      drafts: [{ ...current.drafts[0], text: JSON.parse(options.body).text, source: 'human' }] };
    else if (url.endsWith('/approve')) current = { ...current, revision: 4, drafts: [{ ...current.drafts[0], status: 'approved' }] };
    else if (url.endsWith('/send')) current = { ...current, revision: 5, status: 'handled', drafts: [{ ...current.drafts[0], status: 'sent' }],
      messages: [...current.messages, { id: 'out-1', direction: 'outbound', text: current.drafts[0].text, author_name: '', created_at: '', simulated: true }],
      sends: [{ operation_id: JSON.parse(options.body).operation_id, mode: 'simulation', status: 'sent', receipt_id: 'sim-receipt-1', created_at: '' }] };
    return { ok: true, status: 200, data: url.includes('/conversations/') || url.includes('/drafts/') ? current : fixture() };
  };
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  await ui.button('生成回复建议').props.onClick(); await ui.flush();
  assert.equal(ui.button('模拟发送').props.disabled, true);
  const textarea = ui.findAll(n => n.type === 'textarea' && n.props['aria-label'] === '回复内容')[0];
  textarea.props.onChange({ target: { value: '我加工后的回复' } }); await ui.flush();
  assert.equal(ui.button('模拟审核').props.disabled, true);
  await ui.button('保存草稿').props.onClick(); await ui.flush();
  assert.equal(ui.button('模拟发送').props.disabled, true);
  await ui.button('模拟审核').props.onClick(); await ui.flush();
  assert.equal(ui.button('模拟发送').props.disabled, false);
  await ui.button('模拟发送').props.onClick(); await ui.flush();
  assert.equal(ui.button('模拟发送').props.disabled, true);
  assert.ok(ui.findAll(n => n.type === 'code').some(n => ui.text(n) === 'sim-receipt-1'));
  assert.ok(ui.findAll(n => n.type === 'article').some(n => ui.text(n).includes('我加工后的回复') && ui.text(n).includes('模拟消息')));
  ui.dispose();
});

test('a delayed old-workspace detail cannot reopen after switching customers', async () => {
  let resolveOld;
  response = url => url.includes('/conversations/') ? new Promise(resolve => { resolveOld = resolve; })
    : ({ ok: true, status: 200, data: fixture(url.includes('client-2') ? { client_id: 'client-2', conversations: [], channels: [] } : {}) });
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  ui.findAll(n => n.type === 'select' && n.props['aria-label'] === '客户工作区')[0].props.onChange({ target: { value: 'client-2' } }); await ui.flush();
  resolveOld({ ok: true, status: 200, data: detail() }); await ui.flush();
  assert.equal(ui.findAll(n => n.props?.['aria-label'] === '模拟验收会话').length, 0);
  assert.equal(ui.findAll(n => n.type === 'select' && n.props['aria-label'] === '客户工作区')[0].props.value, 'client-2');
  ui.dispose();
});

test('refresh preserves unsaved reply text and saving retains the original version guard', async () => {
  let revision = 1;
  response = (url, options) => url.includes('/drafts/') && options.method === 'PATCH'
    ? { ok: false, status: 409, data: { detail: '有新消息，请核对' } }
    : { ok: true, status: 200, data: url.includes('/conversations/') ? detail({ revision, drafts: [{ id: 'draft-1', text: '已保存草稿', source: 'human', status: 'draft', confidence: 1 }] }) : fixture() };
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  ui.findAll(n => n.type === 'textarea' && n.props['aria-label'] === '回复内容')[0].props.onChange({ target: { value: '未保存的人工修改' } }); await ui.flush();
  revision = 2; await ui.button('刷新互动中心').props.onClick(); await ui.flush();
  assert.equal(ui.findAll(n => n.type === 'textarea' && n.props['aria-label'] === '回复内容')[0].props.value, '未保存的人工修改');
  await ui.button('保存草稿').props.onClick(); await ui.flush();
  const save = calls.findLast(x => x.url.endsWith('/drafts/draft-1') && x.options.method === 'PATCH');
  assert.equal(JSON.parse(save.options.body).expected_revision, 1);
  assert.equal(ui.findAll(n => n.type === 'textarea' && n.props['aria-label'] === '回复内容')[0].props.value, '未保存的人工修改');
  assert.ok(ui.findAll(n => n.props?.role === 'alert').some(n => ui.text(n).includes('有新消息'))); ui.dispose();
});

test('an approved draft on an unauthorized real channel remains unsendable', async () => {
  response = url => ({ ok: true, status: 200, data: url.includes('/conversations/') ? detail({ delivery_mode: 'official', drafts: [{ id: 'draft-1', text: '已审核回复', source: 'human', status: 'approved', confidence: 1 }] })
    : fixture({ channels: [{ ...fixture().channels[0], delivery_mode: 'official', status: 'disconnected', capabilities: { can_send: false } }], conversations: [{...fixture().conversations[0],delivery_mode:'official'}] }) });
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  assert.equal(ui.button('发送回复').props.disabled, true); ui.dispose();
});

test('discarding local edits explicitly reloads the current server draft', async () => {
  let saved = '原草稿';
  response = url => ({ ok: true, status: 200, data: url.includes('/conversations/') ? detail({ drafts: [{ id: 'draft-1', text: saved, source: 'human', status: 'draft', confidence: 1 }] }) : fixture() });
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  ui.findAll(n => n.type === 'textarea' && n.props['aria-label'] === '回复内容')[0].props.onChange({ target: { value: '本地未保存修改' } }); await ui.flush();
  saved = '服务端最新草稿';
  await ui.button('放弃本地修改并刷新').props.onClick(); await ui.flush();
  assert.equal(ui.findAll(n => n.type === 'textarea' && n.props['aria-label'] === '回复内容')[0].props.value, '服务端最新草稿');
  assert.equal(ui.button('保存草稿').props.disabled, true); ui.dispose();
});

test('channel setup accepts metadata references without asking for plaintext credentials', async () => {
  response = url => ({ ok: true, status: 200, data: url.includes('/operations/inbox/options') ? {accounts:[],works:[],conversations:[]} : fixture() });
  const ui = view(); await ui.flush(); ui.button('账号连接').props.onClick(); await ui.flush();
  ui.button('添加来源').props.onClick(); await ui.flush();
  ui.findAll(n => n.type === 'select' && n.props.value === 'native')[0].props.onChange({target:{value:'official'}}); await ui.flush();
  const input = name => { const found = ui.findAll(n => n.type === 'input' && n.props['aria-label'] === name)[0]; assert.ok(found, `Missing field ${name}`); return found; };
  for (const [name, value] of [['渠道来源链接', 'https://www.xiaohongshu.com/explore/note-one'], ['环境变量前缀', 'EASEL_INTERACTION_CUSTOMER_A'], ['平台账号ID', 'account-1'], ['视频作品ID', 'video-1']]) {
    input(name).props.onChange({ target: { value } }); await ui.flush();
  }
  await ui.findAll(n => n.type === 'form' && n.props.role === 'dialog')[0].props.onSubmit({ preventDefault() {} }); await ui.flush();
  const payload = JSON.parse(calls.findLast(x => x.url.endsWith('/channels')).options.body);
  assert.equal(payload.source_url, 'https://www.xiaohongshu.com/explore/note-one');
  assert.deepEqual(payload.config, { env_prefix: 'EASEL_INTERACTION_CUSTOMER_A', account_id: 'account-1', video_id: 'video-1' });
  assert.equal(ui.findAll(n => n.type === 'input' && n.props.type === 'password').length, 0);
  ui.dispose();
});

test('an older refresh cannot undo newly saved automation rules', async () => {
  let deferRefresh = false; let resolveRefresh;
  const savedRules = { mode: 'auto_faq', enabled: true, blocked_keywords: ['退款'] };
  response = (url, options) => {
    if (options.method === 'PUT') return { ok: true, status: 200, data: fixture({ rules: savedRules }) };
    if (deferRefresh) { deferRefresh = false; return new Promise(resolve => { resolveRefresh = resolve; }); }
    return { ok: true, status: 200, data: fixture() };
  };
  const ui = view(); await ui.flush(); ui.button('回复规则').props.onClick(); await ui.flush();
  deferRefresh = true; ui.button('刷新互动中心').props.onClick(); await ui.flush();
  ui.findAll(n => n.type === 'select' && n.props['aria-label'] === '接待模式')[0].props.onChange({ target: { value: 'auto_faq' } }); await ui.flush();
  ui.findAll(n => n.type === 'input' && n.props.type === 'checkbox')[0].props.onChange({ target: { checked: true } }); await ui.flush();
  await ui.button('保存规则').props.onClick(); await ui.flush();
  resolveRefresh({ ok: true, status: 200, data: fixture() }); await ui.flush();
  assert.equal(ui.findAll(n => n.type === 'input' && n.props.type === 'checkbox')[0].props.checked, true);
  assert.equal(ui.findAll(n => n.type === 'select' && n.props['aria-label'] === '接待模式')[0].props.value, 'auto_faq'); ui.dispose();
});

test('a delayed old conversation read cannot erase a newly generated draft', async () => {
  let deferDetail = false; let resolveOld;
  const fresh = detail({ revision: 2, drafts: [{ id: 'draft-1', text: '新生成的建议', source: 'knowledge_base', status: 'draft', confidence: 1 }] });
  let current = detail();
  response = (url, options) => {
    if (url.endsWith('/draft') && options.method === 'POST') { current = fresh; return { ok: true, status: 200, data: fresh }; }
    if (url.includes('/conversations/')) {
      if (deferDetail) { deferDetail = false; return new Promise(resolve => { resolveOld = resolve; }); }
      return { ok: true, status: 200, data: current };
    }
    return { ok: true, status: 200, data: fixture() };
  };
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  deferDetail = true; ui.button('刷新互动中心').props.onClick(); await ui.flush();
  await ui.button('生成回复建议').props.onClick(); await ui.flush();
  resolveOld({ ok: true, status: 200, data: detail() }); await ui.flush();
  assert.equal(ui.findAll(n => n.type === 'textarea' && n.props['aria-label'] === '回复内容')[0].props.value, '新生成的建议'); ui.dispose();
});

test('an older poll cannot replace the newer detail returned by opening the same conversation', async () => {
  let deferDetail = false; let resolveOld; let revision = 1;
  const atRevision = value => detail({ revision: value, drafts: [{ id: 'draft-1', text: `revision-${value}`, source: 'human', status: 'draft', confidence: 1 }] });
  response = url => {
    if (url.includes('/conversations/')) {
      if (deferDetail) { deferDetail = false; return new Promise(resolve => { resolveOld = resolve; }); }
      return { ok: true, status: 200, data: atRevision(revision) };
    }
    return { ok: true, status: 200, data: fixture() };
  };
  const ui = view(); await ui.flush(); ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  deferDetail = true; ui.button('刷新互动中心').props.onClick(); await ui.flush();
  revision = 2; ui.button('打开会话：模拟访客 · 了解亲子沟通课程').props.onClick(); await ui.flush();
  resolveOld({ ok: true, status: 200, data: atRevision(1) }); await ui.flush();
  assert.equal(ui.findAll(n => n.type === 'textarea' && n.props['aria-label'] === '回复内容')[0].props.value, 'revision-2'); ui.dispose();
});

test('keyboard navigation can reach the other interaction-center tabs', async () => {
  response = () => ({ ok: true, status: 200, data: fixture() });
  const ui = view(); await ui.flush();
  ui.findAll(n => n.props?.role === 'tablist')[0].props.onKeyDown({ key: 'ArrowRight', preventDefault() {} }); await ui.flush();
  assert.equal(ui.button('回复规则').props['aria-selected'], true); ui.dispose();
});

test('CreatorHub channel syncs its bound work without a URL and shows the last successful read', async () => {
  const channel = { ...fixture().channels[0], name: '抖音自有作品', platform: 'douyin', delivery_mode: 'creatorhub',
    capabilities: { can_sync: true, can_send: false } };
  const saved = { ...channel, last_sync_at: '2026-11-04T08:00:00Z' };
  response = (url) => ({ ok: true, status: 200, data: fixture({ channels: [url.endsWith('/sync') ? saved : channel] }) });
  const ui = view();
  try {
    await ui.flush(); ui.button('账号连接').props.onClick(); await ui.flush();
    const sync = ui.button('同步评论');
    assert.equal(sync.props.disabled, false);
    assert.equal(ui.findAll(n => n.type === 'input' && n.props['aria-label']?.startsWith('同步链接')).length, 0);
    sync.props.onClick(); await ui.flush();
    assert.equal(calls.at(-1).url, '/api/interactions/channels/channel-1/sync');
    assert.equal(JSON.parse(calls.at(-1).options.body).source_url, '');
    assert.ok(ui.findAll(n => n.type === 'p' && ui.text(n).startsWith('最近成功同步')).length);
  } finally {
    ui.dispose();
    response = () => ({ ok: true, status: 200, data: fixture() });
  }
});

test('a successful inbox refresh retains the failed platform action until the user retries', async () => {
  const channel={...fixture().channels[0],delivery_mode:'native',capabilities:{can_sync:true,can_send:false}};
  response=url=>url.endsWith('/sync') ? {ok:false,status:503,data:{detail:'同步暂缓，请稍后重试'}}
    : {ok:true,status:200,data:fixture({channels:[channel]})};
  const ui=view();
  try {
    await ui.flush(); ui.button('账号连接').props.onClick(); await ui.flush();
    ui.button('同步评论').props.onClick(); await ui.flush();
    assert.ok(ui.findAll(n=>n.props?.role==='alert' && ui.text(n).startsWith('同步暂缓，请稍后重试')).length);
    ui.button('刷新互动中心').props.onClick(); await ui.flush();
    assert.ok(ui.findAll(n=>n.props?.role==='alert' && ui.text(n).startsWith('同步暂缓，请稍后重试')).length);
  } finally {ui.dispose();response=()=>({ok:true,status:200,data:fixture()});}
});

test('switching customers ends an in-flight batch without sending old sources into the new workspace', async () => {
  let resolveFirst;
  const before=calls.length;
  const channels=[1,2].map(number=>({...fixture().channels[0],id:`native-${number}`,delivery_mode:'native',capabilities:{can_sync:true,can_send:false}}));
  response=(url,options)=>{
    if (url.endsWith('/sync') && options.method==='POST') return new Promise(resolve=>{resolveFirst ||= resolve;});
    return {ok:true,status:200,data:url.includes('client_id=client-2') ? fixture({client_id:'client-2',channels:[],conversations:[]}) : fixture({channels})};
  };
  const ui=view();
  try {
    await ui.flush();ui.button('同步消息').props.onClick();await ui.flush();
    ui.findAll(node=>node.type==='select' && node.props['aria-label']==='客户工作区')[0].props.onChange({target:{value:'client-2'}});await ui.flush();
    resolveFirst({ok:true,status:200,data:fixture({channels})});await ui.flush();
    const writes=calls.slice(before).filter(call=>call.url.endsWith('/sync'));
    assert.equal(writes.length,1);
    assert.equal(JSON.parse(writes[0].options.body).client_id,'client-1');
    assert.equal(ui.findAll(node=>node.props?.role==='status' && ui.text(node).includes('已同步')).length,0);
  } finally {ui.dispose();response=()=>({ok:true,status:200,data:fixture()});}
});
