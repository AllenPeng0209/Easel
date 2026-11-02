import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const compiled = ts.transpileModule(readFileSync(new URL('../../web/frontend/src/components/ProjectSpace.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

function view() {
  let cursor = 0;
  const states = [], switches = [];
  const space = { projects: [{ id: 'a', name: '汽车观察', description: '汽车内容' }, { id: 'b', name: '城市生活', description: '生活内容' }],
    current: { id: 'a', name: '汽车观察', description: '汽车内容' } };
  const jsx = (type, props) => ({ type, props });
  const hooks = { createContext: () => ({}), useContext: () => space,
    useEffect() {}, useRef: () => ({current:null}),
    useState(initial) { const index = cursor++; if (!(index in states)) states[index] = initial;
      return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }]; } };
  const exports = {};
  runInNewContext(compiled, { exports, document: {body:{}}, window: {location:{pathname:'/'}},
    require: name => name === 'react' ? hooks : name === 'react-dom' ? {createPortal: child => child}
      : name === 'react/jsx-runtime' ? {jsx, jsxs:jsx, Fragment:'fragment'}
      : name.includes('projectScope') ? {ACTIVE_PROJECT_ID:'a', switchProject:id => switches.push(id)} : {},
  });
  const all = tree => { const result = []; const walk = node => { if (!node || typeof node !== 'object') return;
    result.push(node); [node.props?.children].flat(Infinity).forEach(walk); }; walk(tree); return result; };
  return { render() { cursor = 0; return all(exports.ProjectSwitcher()); }, switches };
}

test('the IP entry opens a central selection dialog and selecting another IP switches the whole space', () => {
  const ui = view();
  const trigger = ui.render().find(node => node.props?.className?.includes('project-trigger '));
  trigger.props.onClick();
  const nodes = ui.render();
  const dialog = nodes.find(node => node.props?.role === 'dialog');
  assert.ok(dialog, 'IP selection must open a modal dialog');
  assert.equal(dialog.props['aria-modal'], 'true');
  assert.ok(!nodes.some(node => node.props?.className === 'project-popover'));
  const target = nodes.find(node => node.type === 'button' && node.props?.className?.includes('project-option') && !node.props?.['aria-current']);
  assert.ok(target, 'The central dialog must offer other IPs directly');
  target.props.onClick();
  assert.deepEqual(ui.switches, ['b']);
});

test('a signed-in user with no IP has an actionable empty workspace instead of an endless loader', () => {
  let index = 0;
  const hooks = { createContext: () => ({}), useEffect() {},
    useState(initial) { const position = index++; return [position === 2 ? true : initial, () => {}]; } };
  const jsx = (type, props) => ({type,props});
  const exports = {};
  runInNewContext(compiled, {exports,window:{location:{pathname:'/'}},require:name => name === 'react' ? hooks
    : name === 'react/jsx-runtime' ? {jsx,jsxs:jsx,Fragment:'fragment'}
    : name.includes('projectScope') ? {ACTIVE_PROJECT_ID:'default'} : {}});
  const tree = exports.default({children:'private space'});
  const nodes = [];
  const visit = node => { if (!node || typeof node !== 'object') return; nodes.push(node); [node.props?.children].flat(Infinity).forEach(visit); };
  visit(tree);
  assert.ok(nodes.some(node => node.type === 'form'), 'An account without membership must be able to create its own IP');
  assert.ok(!nodes.some(node => node.props?.children === 'private space'));
});
