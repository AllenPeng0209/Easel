import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { sessionNavigation } from './sessionNavigationHarness.mjs';

test('the sidebar routes its interaction-center entry to the separate inbox page', () => {
  const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
  const ts = require('typescript');
  const code = ts.transpileModule(readFileSync(new URL('../../web/frontend/src/components/Sidebar.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const mod = {};
  const jsx = (type, props) => ({ type, props });
  runInNewContext(code, { exports: mod, require: name => name === 'react'
    ? { useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}], useRef: initial => ({ current: initial }), useEffect() {}, useLayoutEffect() {} }
    : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx }
      : name === './AuthGate' ? { AuthAccountMenu: 'AuthAccountMenu', useAuth: () => null }
      : name.endsWith('/ProjectSpace') || name === './ProjectSpace' ? { ProjectSwitcher: 'IPSwitcher', useIpPermissions: () => ({ role: 'owner', canOperate: true, canManageAccounts: true, isPlatformAdmin: true }) }
        : name.endsWith('/sessionNavigation') ? sessionNavigation
          : name.endsWith('/useModalLayer') ? { useModalLayer() {} }
        : new Proxy({}, { get: (_, property) => property }) });
  const navigations = [];
  const tree = mod.default({ currentPage: 'dashboard', onPageChange: page => navigations.push(page),
    personas: [], selectedPersona: '', sessions: [], activeSessionId: null,
    gatewayStatus: 'connected', agentRuntime: 'openclaw', apiDirect: false });
  const buttons = [];
  const text = node => typeof node === 'string' ? node : node?.props?.children
    ? [node.props.children].flat(Infinity).map(text).join('') : '';
  const visit = node => {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'button') buttons.push(node);
    [node.props?.children].flat(Infinity).forEach(visit);
  };
  visit(tree);
  const entry = buttons.find(button => text(button) === '互动中心');
  assert.ok(entry, 'The inbox must have a separate sidebar entry');
  entry.props.onClick();
  assert.deepEqual(navigations, ['interactions']);
});
