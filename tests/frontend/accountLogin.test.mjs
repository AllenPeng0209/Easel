import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/components/AccountsPage.tsx', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('AccountsPage.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const component = parsed.statements.find(node =>
  ts.isFunctionDeclaration(node) && node.name?.text === 'AccountsPage');
const names = ['closeQr', 'handleLogin', 'handleMpLogin', 'loginSettled'];
const statements = component.body.statements.filter(node =>
  ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration =>
    names.includes(declaration.name.getText(parsed))));
assert.equal(statements.length, names.length);
const { outputText } = ts.transpileModule(
  statements.map(node => node.getText(parsed)).join('\n') + '\n({ closeQr, handleLogin, handleMpLogin, loginSettled });',
  { compilerOptions: { target: ts.ScriptTarget.ES2022 } });

test('a failed old avatar does not prevent a newly verified avatar from rendering', () => {
  const directorySource = ts.createSourceFile('InteractionAccounts.tsx', readFileSync(new URL('../../web/frontend/src/components/InteractionAccounts.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const avatar = directorySource.statements.find(node =>
    ts.isFunctionDeclaration(node) && node.name?.text === 'Avatar');
  const { outputText: compiled } = ts.transpileModule(
    avatar.getText(directorySource) + '\nAvatar;',
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX } },
  );
  let broken = '';
  const render = runInNewContext(compiled, {
    exports: {},
    useState: () => [broken, value => { broken = value; }],
    require: () => ({ jsx: (type, props) => ({ type, props }) }),
  });
  const old = render({ url: 'https://self.test/old', name: 'Me' });
  assert.equal(old.type, 'img');
  old.props.onError();
  assert.equal(render({ url: 'https://self.test/old', name: 'Me' }).type, 'div');
  const current = render({ url: 'https://self.test/current', name: 'Me' });
  assert.equal(current.type, 'img');
  assert.equal(current.props.src, 'https://self.test/current');
});

test('post-login verification refreshes the one directory only after an authoritative result', async () => {
  const declaration = component.body.statements.find(node =>
    ts.isVariableStatement(node) && node.declarationList.declarations.some(variable =>
      variable.name.getText(parsed) === 'runWhoami'));
  const { outputText: compiled } = ts.transpileModule(declaration.getText(parsed) + '\nrunWhoami;',
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } });
  const writes = []; let refreshes = 0;
  const verify = runInNewContext(compiled, {
    useCallback: callback => callback, whoamiRequests: { current: {} }, aliveRef: { current: true },
    accountPermission: { current: true }, load: () => { refreshes++; },
    setWhoamiCache: (platform, value) => writes.push({ platform, value }),
    accountWhoami: async (platform, force) => {
      assert.equal(platform, 'wechat-oa'); assert.equal(force, true);
      return { loggedIn: false, name: '', avatar: '' };
    },
  });
  verify('wechat-oa');
  assert.equal(refreshes, 0);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(refreshes, 1);
  assert.equal(writes[0].value.loggedIn, false);
});

test('failed post-login verification preserves verified identity and does not invent directory changes', async () => {
  const declaration = component.body.statements.find(node =>
    ts.isVariableStatement(node) && node.declarationList.declarations.some(variable =>
      variable.name.getText(parsed) === 'runWhoami'));
  const { outputText: compiled } = ts.transpileModule(declaration.getText(parsed) + '\nrunWhoami;',
    { compilerOptions: { target: ts.ScriptTarget.ES2022 } });
  const verify = runInNewContext(compiled, {
    useCallback: callback => callback, whoamiRequests: { current: {} }, aliveRef: { current: true },
    accountPermission: { current: true },
    load: () => assert.fail('Unknown identity must not rewrite the displayed directory'),
    setWhoamiCache: () => assert.fail('Unknown identity cannot replace the verified cache'),
    accountWhoami: async () => { throw new Error('identity endpoint not ready'); },
  });
  verify('weixin-channels');
  await new Promise(resolve => setImmediate(resolve));
});

function setup() {
  const document = { visibilityState: 'visible' };
  let now = Date.now();
  const state = { qr: null, busy: '', err: '', intervals: [], verifies: [], accounts: [], refreshes: 0 };
  const pending = [];
  const qrRequest = { current: 0 };
  const pollRef = { current: null };
  const aliveRef = { current: true };
  let status = { state: 'qr_ready', message: '', qr: '_login/test.png', qrTs: 123 };
  let statusRequest = async () => status;
  const start = () => new Promise((resolve, reject) => pending.push({ resolve, reject }));
  const change = key => value => {
    state[key] = typeof value === 'function' ? value(state[key]) : value;
  };
  const handlers = runInNewContext(outputText, {
    Error, document, Date: { now: () => now },
    useCallback: callback => callback,
    qrRequest, aliveRef, pollRef,
    whoamiRequests: { current: {} }, replacement: { current: { id: '', revision: 0 } }, qrPlatformRef: { current: '' }, qrSession: { get current() { return state.qr; } },
    setQr: change('qr'), setBusy: change('busy'), setErr: change('err'),
    setAccounts: change('accounts'), setWhoami: () => {},
    setSmsCode: () => {}, setSmsErr: () => {}, setSmsBusy: () => {},
    setTerminalMsg: change('terminalMsg'), setQrNonce: () => {},
    setWhoamiCache: () => {}, openCred: () => { state.credentials = true; },
    load: () => { state.refreshes++; }, runWhoami: platform => state.verifies.push(platform),
    stopPoll: () => { pollRef.current = null; },
    startLogin: start, startMpLogin: start,
    loginStatus: () => statusRequest(), mpLoginStatus: () => statusRequest(),
    setInterval: (callback, delay) => {
      const interval = { callback, delay };
      state.intervals.push(interval);
      return interval;
    },
  });
  return { handlers, state, pending, aliveRef, hidden: value => { document.visibilityState = value ? 'hidden' : 'visible'; }, advance: ms => { now += ms; }, setStatus: value => { status = value; },
    setStatusRequest: callback => { statusRequest = callback; } };
}

test('overseas login opens a browser modal with no QR instruction and polls to success', async () => {
  const view = setup();
  const operation = view.handlers.handleLogin({ platform: 'facebook', name: 'Facebook',
    supported: true, backend: 'social-browser', loginMode: 'browser' });
  assert.equal(view.state.qr.mode, 'browser');
  assert.doesNotMatch(view.state.qr.message, /二维码/);
  view.pending[0].resolve({ mode: 'browser', state: 'window_login', message: '请在浏览器完成登录' });
  await operation;
  assert.equal(view.state.qr.mode, 'browser');
  assert.equal(view.state.qr.state, 'window_login');
  view.setStatus({ state: 'success', message: '登录成功', qr: '' });
  await view.state.intervals[0].callback();
  assert.equal(view.state.qr.state, 'success');
  assert.deepEqual(view.state.verifies, ['facebook']);
});

for (const handler of ['handleLogin', 'handleMpLogin']) {
  test(`${handler} serializes polling and never overwrites terminal success`, async () => {
    const view = setup();
    const operation = view.handlers[handler]({ platform: 'weixin-channels', name: '视频号', supported: true });
    view.pending[0].resolve({ mode: 'qr', state: 'starting' });
    await operation;
    let resolve;
    let calls = 0;
    view.setStatusRequest(() => {
      calls += 1;
      return new Promise(done => { resolve = done; });
    });
    const poll = view.state.intervals[0].callback;
    const first = poll();
    await poll();
    assert.equal(calls, 1);
    resolve({ state: 'success', message: '登录成功', qr: '' });
    await first;
    await poll();
    assert.equal(calls, 1);
    assert.equal(view.state.qr.state, 'success');
    assert.deepEqual(view.state.verifies, ['weixin-channels']);
  });

  test(`${handler} surfaces persistent polling failures instead of spinning forever`, async () => {
    const view = setup();
    const operation = view.handlers[handler]({ platform: 'weixin-channels', name: '视频号', supported: true });
    view.pending[0].resolve({ mode: 'qr', state: 'starting' });
    await operation;
    view.setStatusRequest(async () => { throw new Error('network'); });
    for (let attempt = 0; attempt < 5; attempt += 1) await view.state.intervals[0].callback();
    assert.equal(view.state.qr.state, 'error');
    assert.match(view.state.qr.message, /连接中断/);
    assert.equal(view.state.qr.qr, '');
  });

  test(`${handler} opens the login modal before its start request resolves`, async () => {
    const view = setup();
    const operation = view.handlers[handler]({ platform: 'xiaohongshu', name: '小红书', supported: true });
    assert.equal(view.state.qr.state, 'starting');
    assert.equal(view.state.qr.qr, '');
    assert.equal(view.pending.length, 1);
    view.pending[0].resolve({ mode: 'qr', state: 'starting', qr: '' });
    await operation;
    assert.equal(view.state.intervals[0].delay, 1000);
    await view.state.intervals[0].callback();
    assert.equal(view.state.qr.state, 'qr_ready');
    assert.equal(view.state.qr.qrTs, 123);
  });

  test(`${handler} keeps a closed modal closed when its start response arrives`, async () => {
    const view = setup();
    const operation = view.handlers[handler]({ platform: 'xiaohongshu', name: '小红书', supported: true });
    view.handlers.closeQr();
    view.pending[0].resolve({ mode: 'qr', state: 'qr_ready', qr: 'old.png' });
    await operation;
    assert.equal(view.state.qr, null);
    assert.equal(view.state.busy, '');
    assert.equal(view.state.intervals.length, 0);
  });

  test(`${handler} shows startup failures inside the open modal`, async () => {
    const view = setup();
    const operation = view.handlers[handler]({ platform: 'xiaohongshu', name: '小红书', supported: true });
    view.pending[0].reject(new Error('启动失败'));
    await operation;
    assert.equal(view.state.qr.state, 'error');
    assert.equal(view.state.qr.message, '启动失败');
    assert.equal(view.state.busy, '');
  });

  test(`${handler} discards polling responses from a previous login`, async () => {
    const view = setup();
    const account = { platform: 'xiaohongshu', name: '小红书', supported: true };
    const first = view.handlers[handler](account);
    view.pending[0].resolve({ mode: 'qr', state: 'starting' });
    await first;
    const oldPoll = view.state.intervals[0].callback;
    const second = view.handlers[handler]({ ...account, platform: 'zhihu', name: '知乎' });
    view.setStatus({ state: 'success', message: '', qr: '' });
    await oldPoll();
    assert.equal(view.state.qr.platform, 'zhihu');
    assert.equal(view.state.qr.state, 'starting');
    assert.equal(view.state.verifies.length, 0);
    view.pending[1].resolve({ mode: 'qr', state: 'starting' });
    await second;
  });
}

test('closing an owned login refreshes accounts after its acknowledgement, not before', async () => {
  const view = setup();
  const account = { platform: 'bilibili', name: 'B站', supported: true };
  const attempt = { id: 'owned-api-login', platform: 'bilibili' };
  const operation = view.handlers.handleLogin(account);
  view.pending[0].resolve({ mode: 'qr', state: 'qr_ready', qr: 'qr.png', attempt }); await operation;
  view.handlers.closeQr();
  assert.equal(view.state.refreshes, 0);
  view.handlers.loginSettled(attempt, false);
  assert.equal(view.state.refreshes, 0); assert.match(view.state.err, /尚未确认/);
  view.handlers.loginSettled(attempt, true); assert.equal(view.state.refreshes, 1);
});

for (const handler of ['handleLogin', 'handleMpLogin']) {
  test(`${handler} pauses hidden status polls and rechecks success before its wait deadline`, async () => {
    const view = setup(); let calls = 0;
    const operation = view.handlers[handler]({ platform: 'bilibili', name: 'B站', supported: true });
    view.pending[0].resolve({ mode: 'qr', state: 'starting' }); await operation;
    view.setStatusRequest(async () => { calls++; return {state:'success',message:'',qr:''}; });
    view.hidden(true); view.advance(700000);
    for (let i = 0; i < 3; i++) await view.state.intervals[0].callback();
    assert.equal(calls, 0); assert.equal(view.state.qr.state, 'starting');
    view.hidden(false); await view.state.intervals[0].callback();
    assert.equal(calls, 1); assert.equal(view.state.qr.state, 'success');
  });
}
