import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { sessionNavigation } from './sessionNavigationHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const compiled = ts.transpileModule(readFileSync(new URL('../../web/frontend/src/components/Sidebar.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

const text = node => typeof node === 'string' || typeof node === 'number' ? String(node)
  : [node?.props?.children].flat(Infinity).map(child => child ? text(child) : '').join('');
function descendants(tree) {
  const nodes = [];
  const visit = node => { if (!node || typeof node !== 'object') return; nodes.push(node); [node.props?.children].flat(Infinity).forEach(visit); };
  visit(tree);
  return nodes;
}

function sidebar({ currentPage = 'chat', role = 'owner', isPlatformAdmin = true } = {}) {
  let cursor = 0;
  const slots = [], events = [];
  const permissions = { role, canOperate: role !== 'viewer', canManageAccounts: role === 'owner', isPlatformAdmin };
  const hooks = { useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
    return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; }, useEffect() {}, useLayoutEffect() {} };
  const jsx = (type, props) => ({ type, props });
  const mod = {};
  runInNewContext(compiled, { exports: mod, require: name => name === 'react' ? hooks
    : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx, Fragment: 'fragment' }
      : name === './AuthGate' ? { AuthAccountMenu: 'AuthAccountMenu', useAuth: () => null }
      : name === './ProjectSpace' ? { ProjectSwitcher: 'IPSwitcher', useIpPermissions: () => permissions }
        : name.endsWith('/sessionNavigation') ? sessionNavigation
          : name.endsWith('/useModalLayer') ? { useModalLayer() {} }
        : new Proxy({}, { get: (_, name) => name }) });
  const props = { currentPage, onPageChange: page => events.push(['page', page]),
    sessions: [{ id: 'existing', title: '已有对话', messages: [{ role: 'user', content: 'hi' }] },
      { id: 'archived', title: '归档对话', messages: [{ role: 'user', content: 'old' }], archived: true }],
    activeSessionId: 'existing', onSessionSelect: id => events.push(['select', id]),
    onSessionDelete: id => events.push(['delete', id]), onSessionRename: (id, title) => events.push(['rename', id, title]),
    onSessionArchive: (id, archived) => events.push(['archive', id, archived]), onNewChat: () => events.push(['new-chat']),
    gatewayStatus: 'connected', apiDirect: false, agentRuntime: 'codex', onOpenSettings: () => events.push(['settings']),
  };
  return { events, permissions, props, render(overrides = {}) { cursor = 0; const tree = mod.default({ ...props, ...overrides }); return { tree, nodes: descendants(tree) }; } };
}
const findButton = (nodes, label) => nodes.find(node => node.type === 'button' && (node.props['aria-label'] === label || text(node) === label));
const stop = { stopPropagation() {} };

test('AI is the primary entrance before separate production and operations navigation groups', () => {
  const ui = sidebar();
  const { nodes } = ui.render();
  const ai = findButton(nodes, 'AI 对话');
  const production = nodes.find(node => node.type === 'section' && node.props['aria-label'] === '内容生产');
  const operations = nodes.find(node => node.type === 'section' && node.props['aria-label'] === '运营管理');
  assert.ok(ai && production && operations, 'The sidebar needs AI, production and operations as distinct levels');
  assert.ok(nodes.indexOf(ai) < nodes.indexOf(production) && nodes.indexOf(production) < nodes.indexOf(operations));
  ai.props.onClick();
  assert.deepEqual(ui.events, [['page', 'chat']]);
  assert.equal(nodes.filter(node => node.type === 'button' && node.props['aria-label'] === 'AI 对话').length, 1);
});

test('production and operations entries route to the existing pages in the correct group', () => {
  const ui = sidebar();
  const { nodes } = ui.render();
  const production = nodes.find(node => node.type === 'section' && node.props['aria-label'] === '内容生产');
  const operations = nodes.find(node => node.type === 'section' && node.props['aria-label'] === '运营管理');
  assert.ok(production && operations);
  for (const label of ['创作画像', '视频工坊', '内容库', '技能库']) findButton(descendants(production), label).props.onClick();
  for (const label of ['运营总览', '账号管理', '发布中心', '内容日历', '互动中心']) findButton(descendants(operations), label).props.onClick();
  assert.deepEqual(ui.events.map(event => event[1]), ['profile', 'workshop', 'outputs', 'skills', 'dashboard', 'accounts', 'publish', 'calendar', 'interactions']);
});

test('retired planning routes expose no navigation entry', () => {
  const ui = sidebar();
  for (const currentPage of ['trends', 'ideas', 'breakdown', 'workshop']) {
    const { nodes } = ui.render({ currentPage });
    assert.equal(findButton(nodes, '选题策划'), undefined);
    assert.equal(findButton(nodes, '热点雷达'), undefined);
    assert.equal(findButton(nodes, '爆款拆解'), undefined);
    assert.ok(findButton(nodes, '视频工坊'));
  }
});

test('history starts collapsed and retains selection, rename, archive and delete after expansion', () => {
  const ui = sidebar();
  let { nodes } = ui.render();
  assert.ok(!nodes.some(node => node.props?.className === 'session-item-title' && text(node) === '已有对话'));
  const toggle = findButton(nodes, '历史对话');
  assert.equal(toggle?.props['aria-expanded'], false);
  toggle.props.onClick();
  nodes = ui.render().nodes;
  const row = nodes.find(node => node.props?.className?.includes('session-item') && descendants(node).some(child => child.props?.className === 'session-item-title' && text(child) === '已有对话'));
  assert.ok(row);
  row.props.onClick();
  descendants(row).find(node => node.type === 'button' && node.props.title === '归档').props.onClick(stop);
  descendants(row).find(node => node.type === 'button' && node.props.title === '删除').props.onClick(stop);
  descendants(row).find(node => node.type === 'button' && node.props.title === '重命名').props.onClick(stop);
  nodes = ui.render().nodes;
  nodes.find(node => node.type === 'input' && node.props.className === 'session-rename-input').props.onChange({ target: { value: '重新命名' } });
  nodes = ui.render().nodes;
  nodes.find(node => node.type === 'input' && node.props.className === 'session-rename-input').props.onKeyDown({ key: 'Enter' });
  assert.deepEqual(ui.events, [['select', 'existing'], ['archive', 'existing', true], ['delete', 'existing'], ['rename', 'existing', '重新命名']]);
  const archivedToggle = ui.render().nodes.find(node => node.type === 'button' && node.props['aria-controls'] === 'sidebar-archived-history');
  assert.ok(archivedToggle);
  archivedToggle.props.onClick();
  nodes = ui.render().nodes;
  const archivedRow = nodes.find(node => node.props?.className?.includes('session-item') && descendants(node).some(child => child.props?.className === 'session-item-title' && text(child) === '归档对话'));
  descendants(archivedRow).find(node => node.type === 'button' && node.props.title === '取消归档').props.onClick(stop);
  assert.deepEqual(ui.events.at(-1), ['archive', 'archived', false]);
});

test('viewer sees the AI entry as read-only and cannot access writable or administrator shortcuts', () => {
  const ui = sidebar({ currentPage: 'dashboard', role: 'viewer', isPlatformAdmin: false });
  const { nodes } = ui.render();
  const ai = findButton(nodes, 'AI 对话');
  assert.ok(ai);
  assert.equal(ai.props.disabled, true);
  ai.props.onClick();
  const create = nodes.find(node => node.type === 'button' && node.props.title === '新建对话');
  if (create) { assert.equal(create.props.disabled, true); create.props.onClick(); }
  assert.ok(!findButton(nodes, '发布中心'));
  assert.ok(!findButton(nodes, '技能库'));
  assert.ok(!findButton(nodes, '历史对话'));
  assert.deepEqual(ui.events, []);
});
