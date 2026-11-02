import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const settle = () => new Promise(resolve => setImmediate(resolve));
const permissions = role => ({ role, canOperate: role !== 'viewer', canManageAccounts: role === 'owner', isPlatformAdmin: false });

function component(name, role, props = {}) {
  const source = readFileSync(new URL(`../../web/frontend/src/components/${name}.tsx`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  let cursor = 0, effects = [], tree, currentRole = role;
  const slots = [], calls = [], navigations = [], exports = {}, intervals = new Map();
  let timerSequence = 0;
  const accounts = [
    { platform: 'xiaohongshu', name: '小红书', backend: 'xhs', supported: true, loggedIn: true },
    { platform: 'douyin', name: '抖音', backend: 'douyin', supported: true, loggedIn: false },
  ];
  const api = new Proxy({
    fetchAccounts: async () => { calls.push('fetchAccounts'); return accounts; },
    fetchTrends: async () => ({ trends: [] }), fetchSchedule: async () => [], fetchOutputs: async () => [], fetchIdeas: async () => [],
    fetchAnalyticsPlatforms: async () => accounts.map(account => ({ ...account })),
    fetchPersonas: async () => [{ name: '汽车观察', description: '智能驾驶与新能源' }],
    fetchPersonaFiles: async () => ({ files: [{ filename: 'identity.md', content: '真实画像内容' }, { filename: 'platforms.md', content: '多平台运营资料' }] }),
  }, { get: (target, property) => property in target ? target[property] : async () => { calls.push(String(property)); return {}; } });
  const hooks = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }]; },
    useRef(initial) { const index = cursor++; if (!(index in slots)) slots[index] = { current: initial }; return slots[index]; },
    useCallback(callback) { return callback; },
    useEffect(effect, dependencies) { const index = cursor++; const previous = slots[index];
      if (!previous || !dependencies || dependencies.some((value, item) => !Object.is(value, previous[item]))) {
        slots[index] = dependencies; effects.push(effect);
      }
    },
  };
  const jsx = (type, properties) => ({ type, props: properties });
  const cache = { xiaohongshu: { loggedIn: true, name: '汽车观察', avatar: '' } };
  runInNewContext(code, {
    exports, Date, Error, Set, Object, setTimeout, clearTimeout,
    setInterval: callback => { const timer = ++timerSequence; intervals.set(timer, callback); return timer; },
    clearInterval: timer => intervals.delete(timer),
    localStorage: { getItem: key => key === 'easel_analytics' ? JSON.stringify({ xiaohongshu: {
      platform: 'xiaohongshu', name: '小红书', loggedIn: true, followers: 21, likes: 0, following: 1, notes: [],
    } }) : null, setItem() {} },
    window: { addEventListener() {}, removeEventListener() {}, confirm: () => true },
    document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible' },
    require: id => id === 'react' ? hooks
      : id === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment: 'fragment' }
      : id === './ProjectSpace' ? { useIpPermissions: () => permissions(currentRole) }
      : id === '../lib/api' ? api
      : id === '../lib/projectScope' ? { projectStorageKey: key => key }
      : id === '../lib/whoami' ? { getWhoamiCache: () => cache, setWhoamiCache() {}, verifyStale: () => calls.push('verifyStale') }
      : id === '../lib/sanitize' ? { renderMarkdown: text => text }
      : new Proxy({}, { get: (_, property) => String(property) }),
  });
  const flatten = root => { const result = []; const visit = node => {
    if (!node || typeof node !== 'object') return;
    if (typeof node.type === 'function') { visit(node.type(node.props)); return; }
    result.push(node); [node.props?.children].flat(Infinity).forEach(visit);
  }; visit(root); return result; };
  const render = () => { cursor = 0; effects = []; tree = flatten(exports.default({ persona: '',
    gatewayStatus: 'connected', apiDirect: true, agentRuntime: 'api', onNavigate: page => navigations.push(page),
    onUseTopic: topic => navigations.push(topic), onNewProfile: () => navigations.push('new-profile'),
    onUsePersona: persona => navigations.push(persona), onDeleted() {}, ...props }));
    const pending = effects; effects = []; pending.forEach(effect => effect()); return tree; };
  return { render, calls, navigations, intervals, setRole(next) { currentRole = next; },
    async ready() { render(); await settle(); render(); await settle(); return render(); } };
}

function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  return [node?.props?.children].flat(Infinity).map(text).join('');
}
const buttons = nodes => nodes.filter(node => node.type === 'button');

for (const role of ['viewer', 'operator']) {
  test(`${role} can inspect account status without identity probes or login and logout controls`, async () => {
    const ui = component('AccountsPage', role);
    const nodes = await ui.ready();
    assert.ok(nodes.some(node => text(node).includes('小红书')));
    assert.ok(!ui.calls.includes('verifyStale'), 'Opening a read-only account page must not start browser verification');
    const actions = buttons(nodes).map(text).join('|');
    assert.doesNotMatch(actions, /校验账号|退出登录|^登录$|保存并验证|提交验证码/);
    assert.ok(nodes.some(node => text(node).includes('只读')));
  });
}

test('an owner retains account verification and social platform login controls', async () => {
  const ui = component('AccountsPage', 'owner');
  const nodes = await ui.ready();
  assert.ok(ui.calls.includes('verifyStale'));
  assert.ok(buttons(nodes).some(node => text(node) === '校验账号'));
  assert.ok(buttons(nodes).some(node => text(node) === '登录'));
});

test('a viewer dashboard can select cached analytics without starting verification or fresh collection', async () => {
  const ui = component('DashboardPage', 'viewer');
  const nodes = await ui.ready();
  assert.ok(!ui.calls.includes('verifyStale'));
  const platform = buttons(nodes).find(node => text(node) === '小红书');
  assert.ok(platform, 'A viewer should be able to inspect the existing platform snapshot');
  platform.props.onClick();
  await settle();
  assert.ok(!ui.calls.includes('fetchAccountAnalytics'));
  assert.ok(!buttons(nodes).some(node => text(node).includes('刷新 →')));
});

test('a viewer can read the profile list without create or change-conversation actions', async () => {
  const ui = component('ProfilePage', 'viewer');
  const nodes = await ui.ready();
  assert.ok(nodes.some(node => text(node).includes('汽车观察')));
  assert.ok(!buttons(nodes).some(node => /新建画像|切换画像/.test(text(node))));
  assert.ok(buttons(nodes).some(node => text(node) === '查看'));
});

test('a viewer profile detail renders saved content and omits edit, delete and analytics-writing controls', async () => {
  const ui = component('ProfilePage', 'viewer', { persona: '汽车观察' });
  const nodes = await ui.ready();
  assert.ok(nodes.some(node => node.props?.dangerouslySetInnerHTML?.__html === '真实画像内容'));
  assert.ok(!buttons(nodes).some(node => /编辑资料|删除画像|抓取公众号数据|切换画像|^保存$/.test(text(node))));
  assert.equal(nodes.filter(node => node.type === 'textarea').length, 0);
  assert.ok(!ui.calls.includes('fetchAccountAnalytics'));
});

test('an operator retains profile creation and editing for content operations', async () => {
  const list = await component('ProfilePage', 'operator').ready();
  assert.ok(buttons(list).some(node => text(node).includes('新建画像')));
  const detail = await component('ProfilePage', 'operator', { persona: '汽车观察' }).ready();
  assert.ok(buttons(detail).some(node => text(node).includes('编辑资料')));
});

for (const page of ['AccountsPage', 'DashboardPage']) {
  test(`${page} does not start a delayed identity probe after the owner role is removed`, async () => {
    const ui = component(page, 'owner');
    ui.render();
    ui.setRole('viewer');
    ui.render();
    await settle();
    assert.ok(!ui.calls.includes('verifyStale'), 'A stale owner callback must not launch a browser after the current role changes');
  });
}

test('removing account-management permission stops an active login poll', async () => {
  const ui = component('AccountsPage', 'owner');
  const nodes = await ui.ready();
  await buttons(nodes).find(node => text(node) === '登录').props.onClick();
  assert.equal(ui.intervals.size, 1);
  ui.setRole('viewer');
  ui.render();
  assert.equal(ui.intervals.size, 0);
});

for (const [label, method] of [['校验账号', 'accountWhoami'], ['登录', 'startLogin'], ['退出登录', 'logoutAccount']]) {
  test(`an old ${label} click handler cannot mutate accounts after the owner is downgraded`, async () => {
    const ui = component('AccountsPage', 'owner');
    const nodes = await ui.ready();
    const oldClick = buttons(nodes).find(node => text(node) === label).props.onClick;
    ui.setRole('operator');
    ui.render();
    oldClick();
    await settle();
    assert.ok(!ui.calls.includes(method), 'A removed owner control cannot retain permission through its captured callback');
  });
}

test('an old dashboard data-collection callback cannot bypass a viewer downgrade', async () => {
  const ui = component('DashboardPage', 'operator');
  const nodes = await ui.ready();
  const oldClick = buttons(nodes).find(node => text(node) === '小红书').props.onClick;
  ui.setRole('viewer');
  ui.render();
  oldClick();
  await settle();
  assert.ok(!ui.calls.includes('fetchAccountAnalytics'));
});

test('an old profile-create callback cannot change conversation context after a viewer downgrade', async () => {
  const ui = component('ProfilePage', 'operator');
  const nodes = await ui.ready();
  const oldClick = buttons(nodes).find(node => text(node).includes('新建画像')).props.onClick;
  ui.setRole('viewer');
  ui.render();
  oldClick();
  assert.deepEqual(ui.navigations, []);
});

test('an old profile-data callback cannot collect or write after a viewer downgrade', async () => {
  const ui = component('ProfilePage', 'operator', { persona: '汽车观察' });
  const nodes = await ui.ready();
  const oldClick = buttons(nodes).find(node => text(node).includes('抓取公众号数据')).props.onClick;
  ui.setRole('viewer');
  ui.render();
  await oldClick();
  assert.ok(!ui.calls.includes('fetchAccountAnalytics'));
  assert.ok(!ui.calls.includes('savePersonaFile'));
});
