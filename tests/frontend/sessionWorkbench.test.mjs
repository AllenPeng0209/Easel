import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const entry = fileURLToPath(new URL('../../web/frontend/src/components/Sidebar.tsx', import.meta.url));
const content = node => typeof node === 'string' || typeof node === 'number' ? String(node)
  : [node?.props?.children].flat(Infinity).map(child => child ? content(child) : '').join('');
const flatten = tree => {
  const nodes = [];
  const visit = node => { if (!node || typeof node !== 'object') return; nodes.push(node); [node.props?.children].flat(Infinity).forEach(visit); };
  visit(tree); return nodes;
};
const fixture = (id, title, updated, extra = {}) => ({ id, title, updated, created: 1,
  messages: [{ role: 'user', content: '用户可见问题' }, { role: 'assistant', content: '回答' }], ...extra });

function mount(sessions, overrides = {}, { narrow = false } = {}) {
  const slots = [], events = [], downloads = [];
  let cursor = 0;
  const jsx = (type, props) => ({ type, props });
  const hooks = { useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
    return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }]; },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; }, useEffect() {}, useLayoutEffect() {} };
  const cache = new Map();
  const load = path => {
    if (cache.has(path)) return cache.get(path);
    const exports = {};
    const compiled = ts.transpileModule(readFileSync(path, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    cache.set(path, exports);
    runInNewContext(compiled, { exports, Blob, window: { matchMedia: () => ({ matches: narrow, addEventListener() {}, removeEventListener() {} }) },
      URL: { createObjectURL(blob) { downloads.push(blob); return 'blob:export'; }, revokeObjectURL() {} },
      document: { createElement: () => ({ click() {}, remove() {} }), body: { appendChild() {} } }, setTimeout: fn => fn(),
      require: name => name === 'react' ? hooks
        : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment: 'fragment' }
          : name === './ProjectSpace' ? { ProjectSwitcher: 'IPSwitcher', useIpPermissions: () => ({ canOperate: true, isPlatformAdmin: true }) }
            : (name.includes('sessionNavigation') || name.includes('vendor/deepseek-harness/navigation') || name.startsWith('./markdown') || name === './useModalLayer' || name === './focus' || name === './keyboard-composition')
              ? load(resolve(dirname(path), name.endsWith('.ts') ? name : `${name}.ts`))
              : new Proxy({}, { get: (_, name) => name }),
    });
    return exports;
  };
  const component = load(entry).default;
  const props = { currentPage: 'chat', onPageChange: page => events.push(['page', page]), personas: [], selectedPersona: '',
    onPersonaChange() {}, onNewProfile() {}, sessions, activeSessionId: null,
    onSessionSelect: id => events.push(['select', id]), onSessionDelete: id => events.push(['delete', id]),
    onSessionRename: (id, title) => events.push(['rename', id, title]), onSessionArchive: (id, value) => events.push(['archive', id, value]),
    onSessionPin: (id, value) => events.push(['pin', id, value]), onNewChat() {}, gatewayStatus: 'connected', apiDirect: false,
    agentRuntime: 'codex', onOpenSettings() {}, ...overrides };
  const render = () => { cursor = 0; return flatten(component(props)); };
  const open = () => { render().find(node => node.type === 'button' && node.props['aria-label'] === '历史对话').props.onClick(); return render(); };
  return { render, open, events, downloads };
}
const rows = nodes => nodes.filter(node => node.props?.className?.split(' ').includes('session-item') && node.props.role === 'button');
const rowTitles = nodes => rows(nodes).map(row => flatten(row).find(node => node.props.className === 'session-item-title')).map(content);
const stop = { stopPropagation() {} };

test('session search matches title and ID without searching hidden agent instructions', () => {
  const ui = mount([fixture('education-42', '教育策划', 1), fixture('food', '美食', 3, { messages: [{ role: 'user', content: 'hello', agentContent: 'education-42 private' }] })]);
  let nodes = ui.open();
  const search = nodes.find(node => node.type === 'input' && node.props['aria-label'] === '搜索历史对话');
  assert.ok(search, 'history must expose metadata search');
  search.props.onChange({ target: { value: ' EDUCATION-42 ' } });
  assert.deepEqual(rowTitles(ui.render()), ['教育策划']);
});

test('pinned sessions lead recent sessions and pin actions use stable session identity', () => {
  const ui = mount([fixture('older', '置顶旧会话', 1, { pinned: true }), fixture('latest', '最近会话', 9), fixture('middle', '中间会话', 5)]);
  const nodes = ui.open();
  assert.deepEqual(rowTitles(nodes), ['置顶旧会话', '最近会话', '中间会话']);
  const pin = flatten(rows(nodes)[1]).find(node => node.type === 'button' && node.props.title === '置顶');
  assert.ok(pin, 'ordinary history rows must expose pin action');
  pin.props.onClick(stop);
  assert.deepEqual(ui.events, [['pin', 'latest', true]]);
});

test('archive filtering and gradual expansion retain every history item without mutating the catalog', () => {
  const sessions = Array.from({ length: 12 }, (_, index) => fixture(`s${index}`, `会话${index}`, 12 - index));
  sessions.push(fixture('archived', '归档结果', 90, { archived: true }));
  const before = JSON.stringify(sessions);
  const ui = mount(sessions);
  let nodes = ui.open();
  assert.equal(rows(nodes).length, 5, 'default history window must be bounded');
  nodes.find(node => node.type === 'button' && node.props['aria-label'] === '显示更多历史对话').props.onClick();
  assert.equal(rows(ui.render()).length, 10);
  const filter = ui.render().find(node => node.type === 'select' && node.props['aria-label'] === '归档筛选');
  assert.ok(filter);
  filter.props.onChange({ target: { value: 'only' } });
  assert.deepEqual(rowTitles(ui.render()), ['归档结果']);
  assert.equal(JSON.stringify(sessions), before);
});

test('current and waiting sessions remain discoverable beyond the initial history window', () => {
  const sessions = Array.from({ length: 9 }, (_, index) => fixture(`s${index}`, `会话${index}`, 20 - index));
  const ui = mount(sessions, { activeSessionId: 's6', sessionStates: { s8: 'waiting' } });
  const titles = rowTitles(ui.open());
  assert.ok(titles.includes('会话6'));
  assert.ok(titles.includes('会话8'));
});

test('Markdown export contains visible transcript and attachment names but omits runtime instructions and secrets', async () => {
  const session = fixture('export', '测试导出', 1, { messages: [
    { role: 'user', content: '帮我处理\n第二行', agentContent: 'PRIVATE INJECTED INSTRUCTION', attachments: [{ name: '素材.png', path: '/private/cookies/token.json', id: 'a' }] },
    { role: 'assistant', content: '```js\nconst value = 1;', thinking: 'PRIVATE REASONING', activity: 'PRIVATE TOOL OUTPUT' },
    { role: 'user', content: 'api_key=sk-proj-123456789012345678901234567890' },
  ] });
  const ui = mount([session]);
  const nodes = ui.open();
  const download = flatten(rows(nodes)[0]).find(node => node.type === 'button' && node.props.title === '导出 Markdown');
  assert.ok(download, 'session action must expose Markdown export');
  download.props.onClick(stop);
  assert.equal(ui.downloads.length, 1);
  const markdown = await ui.downloads[0].text();
  assert.match(markdown, /^# 测试导出\n/);
  assert.match(markdown, /> 帮我处理\n> 第二行/);
  assert.match(markdown, /素材\.png/);
  assert.match(markdown, /const value = 1;\n```/);
  assert.doesNotMatch(markdown, /PRIVATE|\/private\/cookies|sk-proj-123456/);
});

test('desktop sidebar collapses to an actionable rail and restores its navigation', () => {
  const ui = mount([]);
  let nodes = ui.render();
  const collapse = nodes.find(node => node.type === 'button' && node.props['aria-label'] === '收起侧栏');
  assert.ok(collapse, 'expanded desktop sidebar needs collapse control');
  collapse.props.onClick();
  nodes = ui.render();
  assert.ok(nodes.some(node => node.props?.className?.includes('sidebar-rail')));
  assert.ok(!nodes.some(node => node.type === 'section' && node.props['aria-label'] === '内容生产'));
  nodes.find(node => node.type === 'button' && node.props['aria-label'] === '展开侧栏').props.onClick();
  assert.ok(ui.render().some(node => node.type === 'section' && node.props['aria-label'] === '内容生产'));
});

test('narrow viewport starts with a closed drawer and overlay click closes it', () => {
  const ui = mount([], {}, { narrow: true });
  let nodes = ui.render();
  const opener = nodes.find(node => node.type === 'button' && node.props['aria-label'] === '打开导航');
  assert.ok(opener, 'mobile layout needs its own reachable opener');
  assert.ok(!nodes.some(node => node.props?.['aria-modal'] === true));
  opener.props.onClick();
  nodes = ui.render();
  assert.ok(nodes.some(node => node.props?.role === 'dialog' && node.props['aria-modal'] === true));
  nodes.find(node => node.props?.className === 'sidebar-drawer-mask').props.onClick();
  assert.ok(!ui.render().some(node => node.props?.['aria-modal'] === true));
});
