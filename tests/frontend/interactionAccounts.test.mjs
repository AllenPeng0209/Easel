import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const { parseHTML } = require('linkedom');
const React = require('react');
const { act } = React;
const source = fileURLToPath(new URL('../../web/frontend/src/components/InteractionAccounts.tsx', import.meta.url));
const codeCache = new Map();
function compile(path) {
  if (!codeCache.has(path)) codeCache.set(path, ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText);
  return codeCache.get(path);
}
compile(source);
const tick = () => new Promise(resolve => setImmediate(resolve));
const account = (nickname = '测试账号') => ({ id: 1, platform: 'douyin', nickname, status: 'active', has_read_login: true });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

async function view(respond = () => [account()], initialProps = {}) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  let focused = document.body;
  Object.defineProperty(document, 'activeElement', { get: () => focused?.isConnected ? focused : document.body });
  const originalFocus = Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, 'focus');
  const originalMatches = Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, 'matches');
  window.HTMLElement.prototype.focus = function () { focused = this; };
  const matches = window.HTMLElement.prototype.matches;
  window.HTMLElement.prototype.matches = function (selector) { return selector === ':focus' ? document.activeElement === this : matches.call(this, selector); };
  const saved = new Map();
  for (const [key, value] of Object.entries({ window, document, HTMLElement: window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true })) {
    saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  }
  const { createRoot } = require('react-dom/client');
  const root = createRoot(document.getElementById('root'));
  const calls = [];
  const timers = new Map();
  let timerId = 0;
  const modules = new Map();
  const handlers = new Map();
  let permissions = { canOperate: true, canManageAccounts: true };
  const load = path => {
    if (modules.has(path)) return modules.get(path);
    const exports = {};
    modules.set(path, exports);
    const code = compile(path);
    runInNewContext(code, { exports, window, document, HTMLElement: window.HTMLElement, AbortController, Error,
      navigator: { userAgent: 'offline test', platform: 'MacIntel', maxTouchPoints: 0 },
      setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id; },
      clearTimeout: id => timers.delete(id),
      require: name => {
        if (name === 'react') return React;
        if (name === 'react/jsx-runtime') {
          const runtime = require(name);
          const observe = method => (type, props, ...args) => {
            if (type === 'button' && typeof props.children === 'string') handlers.set(props.children, props.onClick);
            return runtime[method](type, props, ...args);
          };
          return { ...runtime, jsx: observe('jsx'), jsxs: observe('jsxs') };
        }
        if (name.endsWith('/ProjectSpace')) return { useIpPermissions: () => permissions };
        if (name.endsWith('/projectScope')) return { projectFetch: async (url, options = {}) => {
          calls.push({ url, options });
          const data = await respond(url, options);
          return { ok: true, json: async () => data };
        } };
        if (name.endsWith('.css')) return {};
        return load(resolve(dirname(path), name + '.ts'));
      },
    });
    return exports;
  };
  const Component = load(source).default;
  const button = label => {
    const match = [...document.querySelectorAll('button')].find(node => node.getAttribute('aria-label') === label || node.textContent === label);
    assert.ok(match, `Missing button ${label}`);
    return match;
  };
  const render = async props => act(async () => { root.render(React.createElement(Component, { ...initialProps, ...props })); await tick(); });
  const click = async label => act(async () => {
    const target = button(label);
    target.focus();
    target.dispatchEvent(new window.Event('click', { bubbles: true, cancelable: true }));
    await tick();
  });
  await render({});
  return { document, calls, button, render, click, handler: label => handlers.get(label),
    async select(label, value) { await act(async () => {
      const target = [...document.querySelectorAll('label')].find(node => node.firstChild?.textContent === label)?.querySelector('select');
      assert.ok(target, `Missing selector ${label}`);
      Object.defineProperty(target, 'value', { configurable: true, get: () => value });
      target.dispatchEvent(new window.Event('change', { bubbles: true })); await tick();
    }); },
    async setPermissions(next) { permissions = next; await render({}); },
    get pendingPolls() { return timers.size; },
    async key(key) { await act(async () => {
      const event = new window.Event('keydown', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'key', { value: key });
      document.dispatchEvent(event); await tick();
    }); },
    async settle(action) { await act(async () => { action(); await tick(); }); },
    async poll() { const [id, callback] = timers.entries().next().value; timers.delete(id); await act(async () => { await callback(); await tick(); }); },
    async dispose() { await act(async () => root.unmount());
      for (const [name, descriptor] of [['focus', originalFocus], ['matches', originalMatches]]) {
        if (descriptor) Object.defineProperty(window.HTMLElement.prototype, name, descriptor);
        else delete window.HTMLElement.prototype[name];
      }
      for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    } },
  };
}

test('closing an in-flight login does not let its response reopen the dialog', async () => {
  const pending = deferred();
  const ui = await view(url => url.includes('/start') ? pending.promise : [account()]);
  try {
    await ui.click('＋ 添加账号');
    await ui.click('打开登录窗口');
    await ui.click('关闭账号连接');
    assert.equal(ui.button('＋ 添加账号').disabled, false);
    await ui.settle(() => pending.resolve({ task_id: 'old-login', status: 'opening' }));
    assert.equal(ui.document.querySelector('[role="dialog"]'), null);
    assert.equal(ui.calls.filter(call => call.url.includes('/poll')).length, 0);
    assert.equal(ui.pendingPolls, 0);
  } finally { await ui.dispose(); }
});

test('a dismissed login cannot replace a newer login request or clear its busy state', async () => {
  const first = deferred(); const second = deferred(); let starts = 0;
  const ui = await view(url => url.includes('/start') ? (++starts === 1 ? first.promise : second.promise)
    : url.includes('/poll') ? { status: 'waiting' } : [account()]);
  try {
    await ui.click('＋ 添加账号'); await ui.click('打开登录窗口'); await ui.click('关闭账号连接');
    await ui.click('＋ 添加账号'); await ui.click('打开登录窗口');
    assert.equal(starts, 2);
    assert.equal(ui.button('＋ 添加账号').disabled, true);
    await ui.settle(() => first.resolve({ task_id: 'old-login', status: 'opening' }));
    assert.ok(ui.document.querySelector('[role="dialog"]'));
    assert.equal(ui.button('＋ 添加账号').disabled, true);
    assert.equal(ui.pendingPolls, 0);
    await ui.settle(() => second.resolve({ task_id: 'new-login', status: 'opening' }));
    await ui.poll();
    assert.equal(ui.calls.filter(call => call.url.includes('/poll')).at(-1).url, '/api/operations/api/accounts/connect/poll?task_id=new-login');
  } finally { await ui.dispose(); }
});

test('login waiting state keeps focus inside the dialog and closing restores the invoking button', async () => {
  const pending = deferred();
  const ui = await view(url => url.includes('/start') ? pending.promise : [account()]);
  try {
    const trigger = ui.button('＋ 添加账号');
    await ui.click('＋ 添加账号'); await ui.click('打开登录窗口');
    await ui.settle(() => pending.resolve({ task_id: 'login-one', status: 'opening' }));
    assert.equal(ui.document.activeElement, ui.button('关闭账号连接'));
    await ui.click('关闭账号连接');
    assert.equal(ui.document.activeElement, trigger);
  } finally { await ui.dispose(); }
});

test('keyboard focus cannot escape the waiting dialog and Escape restores its trigger', async () => {
  const pending = deferred();
  const ui = await view(url => url.includes('/start') ? pending.promise : [account()]);
  try {
    const trigger = ui.button('＋ 添加账号');
    await ui.click('＋ 添加账号'); await ui.click('打开登录窗口');
    ui.button('管理').focus();
    await ui.key('Tab');
    assert.equal(ui.document.activeElement, ui.button('关闭账号连接'));
    await ui.key('Escape');
    assert.equal(ui.document.querySelector('[role="dialog"]'), null);
    assert.equal(ui.document.activeElement, trigger);
  } finally { await ui.dispose(); }
});

test('retrying a failed login keeps focus inside the waiting dialog', async () => {
  const pending = deferred(); let starts = 0;
  const ui = await view(url => url.includes('/start') ? (++starts === 1 ? Promise.reject(new Error('离线登录失败')) : pending.promise) : [account()]);
  try {
    await ui.click('＋ 添加账号'); await ui.click('打开登录窗口');
    assert.ok(ui.document.body.textContent.includes('离线登录失败'));
    await ui.click('重试登录');
    assert.equal(ui.document.activeElement, ui.button('关闭账号连接'));
    assert.equal(starts, 2);
  } finally { await ui.dispose(); }
});

test('account table exposes named column headers', async () => {
  const ui = await view();
  try { assert.deepEqual([...ui.document.querySelectorAll('[role="columnheader"]')].map(node => node.textContent), ['账号', '平台', '登录状态', '操作']); }
  finally { await ui.dispose(); }
});

test('a late account refresh cannot overwrite a newer verification result', async () => {
  const old = deferred(); let reads = 0;
  const ui = await view(url => url.endsWith('/accounts') ? (++reads === 1 ? [account('旧昵称')] : reads === 2 ? old.promise : [account('新昵称')]) : { ok: true });
  try {
    await ui.render({ refreshToken: 1 });
    await ui.click('管理'); await ui.click('校验登录');
    assert.ok(ui.document.body.textContent.includes('新昵称'));
    await ui.settle(() => old.resolve([account('旧昵称')]));
    assert.ok(ui.document.body.textContent.includes('新昵称'));
    assert.ok(!ui.document.body.textContent.includes('旧昵称'));
  } finally { await ui.dispose(); }
});

const platforms = [
  { platform: 'xiaohongshu', name: '小红书', backend: 'xhs', supported: true, loggedIn: false, note: '' },
  { platform: 'kuaishou', name: '快手', backend: 'web', supported: true, loggedIn: false, note: '' },
  { platform: 'weixin-channels', name: '微信视频号', backend: 'web', supported: true, loggedIn: false, note: '' },
  { platform: 'zhihu', name: '知乎', backend: 'web', supported: true, loggedIn: false, note: '' },
  { platform: 'bilibili', name: 'B站', backend: 'biliup', supported: true, loggedIn: false, note: '' },
  { platform: 'douyin', name: '抖音', backend: 'web', supported: true, loggedIn: false, note: '' },
  { platform: 'wechat-oa', name: '微信公众号', backend: 'wechat-oa', supported: true, loggedIn: false, note: '' },
  ...['Facebook', 'Instagram', 'TikTok', 'X', 'YouTube'].map(name => ({ platform: name.toLowerCase(), name, backend: 'social-browser', supported: true, loggedIn: false, note: '', loginMode: 'browser' })),
];

for (const platform of ['bilibili', 'youtube', 'wechat-oa']) {
  test(`the unified picker keeps ${platform} available and delegates to its original account login`, async () => {
    const chosen = [];
    const ui = await view(undefined, { platforms, onPlatformLogin: item => chosen.push(item) });
    try {
      await ui.click('＋ 添加账号');
      const values = [...ui.document.querySelectorAll('label select')][0].querySelectorAll('option');
      assert.equal(values.length, 12, 'The picker must include the original catalog without duplicating 小红书');
      await ui.select('平台', platform);
      await ui.click('打开登录窗口');
      assert.equal(chosen[0], platforms.find(item => item.platform === platform));
      assert.equal(ui.document.querySelector('[role="dialog"]'), null);
      assert.equal(ui.calls.filter(call => call.options.method === 'POST').length, 0);
    } finally { await ui.dispose(); }
  });
}

test('小红书 has one login for content and interaction without asking the user to choose a purpose', async () => {
  const chosen = [];
  const ui = await view(url => url.includes('/start') ? { task_id: 'xhs-unified', status: 'opening' } : [account()], { platforms, onPlatformLogin: item => chosen.push(item) });
  try {
    await ui.click('＋ 添加账号'); await ui.select('平台', 'xhs');
    assert.ok(![...ui.document.querySelectorAll('label')].some(label => label.textContent.startsWith('用途')));
    await ui.click('打开登录窗口');
    assert.equal(chosen.length, 0);
    assert.equal(ui.calls.find(call => call.options.method === 'POST').url, '/api/operations/api/accounts/connect/start?platform=xhs');
  } finally { await ui.dispose(); }
});

test('Douyin uses the unified login service when the full platform catalog is present', async () => {
  const chosen = [];
  const ui = await view(url => url.includes('/start') ? { task_id: 'native', status: 'opening' } : [account()], { platforms, onPlatformLogin: item => chosen.push(item) });
  try {
    await ui.click('＋ 添加账号'); await ui.click('打开登录窗口');
    assert.equal(ui.calls.find(call => call.options.method === 'POST').url, '/api/operations/api/accounts/connect/start?platform=douyin');
    assert.equal(chosen.length, 0);
  } finally { await ui.dispose(); }
});

for (const capabilities of [
  { creator: 'needs_verification', comments: 'verified', dm: 'needs_verification' },
  { creator: 'verified', comments: 'needs_verification', dm: 'needs_verification' },
]) test('a partial unified login reports pending verification instead of claiming both login checks passed', async () => {
  const ui = await view(url => url.includes('/start') ? { task_id: 'partial', status: 'opening' }
    : url.includes('/poll') ? { status: 'confirmed', completion: 'partial', capabilities }
      : [account()], { platforms });
  try {
    await ui.click('＋ 添加账号'); await ui.click('打开登录窗口'); await ui.poll();
    assert.equal(ui.document.querySelector('[role="dialog"]'), null);
    assert.ok(ui.document.body.textContent.includes('未完成验证的功能'));
    assert.ok(!ui.document.body.textContent.includes('内容后台与主站登录已完成检查'));
  } finally { await ui.dispose(); }
});

test('a unified login displays its safe platform verification hint', async () => {
  const ui = await view(url => url.includes('/start') ? { task_id: 'needs-proof', status: 'opening', hint: '正在检查已有登录。' }
    : url.includes('/poll') ? { status: 'error', hint: '主站与内容后台账号不一致，请使用同一账号完成验证。' }
      : [account()]);
  try {
    await ui.click('＋ 添加账号'); await ui.click('打开登录窗口');
    assert.ok(ui.document.body.textContent.includes('正在检查已有登录。'));
    await ui.poll();
    assert.ok(ui.document.body.textContent.includes('主站与内容后台账号不一致'));
  } finally { await ui.dispose(); }
});

test('an unsupported catalog platform cannot start a login', async () => {
  const chosen = [];
  const ui = await view(undefined, { platforms: [{ ...platforms[4], supported: false }], onPlatformLogin: item => chosen.push(item) });
  try {
    await ui.click('＋ 添加账号'); await ui.select('平台', 'bilibili');
    assert.equal(ui.button('打开登录窗口').disabled, true);
    await ui.click('打开登录窗口');
    assert.equal(chosen.length, 0);
    assert.equal(ui.calls.filter(call => call.options.method === 'POST').length, 0);
  } finally { await ui.dispose(); }
});

test('removing account permission blocks a captured catalog confirmation from starting login', async () => {
  const chosen = [];
  const ui = await view(undefined, { platforms, onPlatformLogin: item => chosen.push(item) });
  try {
    await ui.click('＋ 添加账号'); await ui.select('平台', 'youtube');
    const oldConfirm = ui.handler('打开登录窗口');
    await ui.setPermissions({ canOperate: true, canManageAccounts: false });
    assert.equal(ui.document.querySelector('[role="dialog"]'), null);
    await ui.settle(oldConfirm);
    assert.equal(chosen.length, 0);
    assert.equal(ui.calls.filter(call => call.options.method === 'POST').length, 0);
  } finally { await ui.dispose(); }
});

test('closing the catalog blocks an older confirmation from starting a platform login', async () => {
  const chosen = [];
  const ui = await view(undefined, { platforms, onPlatformLogin: item => chosen.push(item) });
  try {
    await ui.click('＋ 添加账号'); await ui.select('平台', 'youtube');
    const oldConfirm = ui.handler('打开登录窗口');
    await ui.click('关闭账号连接');
    await ui.settle(oldConfirm);
    assert.equal(chosen.length, 0);
    assert.equal(ui.calls.filter(call => call.options.method === 'POST').length, 0);
  } finally { await ui.dispose(); }
});
