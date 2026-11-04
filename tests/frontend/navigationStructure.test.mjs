import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function compile(path) {
  return ts.transpileModule(readFileSync(new URL('../../web/frontend/src/' + path, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
}
const jsx = (type, props) => ({ type, props });
function nodes(tree) {
  const result = [];
  const walk = node => { if (!node || typeof node !== 'object') return; result.push(node); [node.props?.children].flat(Infinity).forEach(walk); };
  walk(tree); return result;
}
const text = node => typeof node === 'string' || typeof node === 'number' ? String(node)
  : !node || typeof node !== 'object' ? '' : [node.props?.children].flat(Infinity).map(text).join('');

function subnav(canOperate = true) {
  const exports = {};
  runInNewContext(compile('components/SubNav.tsx'), { exports, require: name => name === 'react/jsx-runtime'
    ? { jsx, jsxs:jsx } : name === './ProjectSpace' ? { useIpPermissions: () => ({canOperate}) }
      : new Proxy({}, {get: (_, key) => key}) });
  const navigated=[];
  const buttons = nodes(exports.default({current:'ideas',onNavigate: page => navigated.push(page)})).filter(node => node.type === 'button');
  return {buttons,navigated};
}

test('topic planning keeps discovery, ideas and breakdown together without operations tabs', () => {
  const ui=subnav();
  assert.deepEqual(ui.buttons.map(text),['AI 对话','热点雷达','选题库','爆款拆解']);
  ui.buttons[0].props.onClick();
  ui.buttons[1].props.onClick();
  assert.deepEqual(ui.navigated,['chat','trends']);
});

test('viewer topic navigation returns to its read-only overview and does not expose analysis execution', () => {
  const ui=subnav(false);
  assert.deepEqual(ui.buttons.map(text),['运营总览','热点雷达','选题库']);
  ui.buttons[0].props.onClick();
  assert.deepEqual(ui.navigated,['dashboard']);
});

for (const canOperate of [true,false]) test(`the application opens the ${canOperate ? 'AI conversation' : 'viewer overview'} by default`, () => {
  const exports={};
  const hooks={lazy: () => 'lazy-page',Suspense:'suspense',useRef: value => ({current:value}),useEffect() {},
    useCallback: fn => fn,useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}]};
  runInNewContext(compile('App.tsx'), {exports,localStorage:{getItem:()=>null},
    require: name => name === 'react' ? hooks : name === 'react/jsx-runtime' ? {jsx,jsxs:jsx,Fragment:'fragment'}
      : name === './components/ProjectSpace' ? {ProjectBreadcrumb:'breadcrumb',useIpPermissions:()=>({canOperate,isPlatformAdmin:true})}
      : name === './lib/projectScope' ? {projectStorageKey:key=>key,projectUrl:url=>url,ACTIVE_PROJECT_ID:'default'}
      : name === './lib/store' ? {loadSessions:()=>[],loadActiveId:()=>null}
      : name === './components/SuperagentPage' ? {useChatSuperagent:()=>({})}
      : {default: name === './components/Sidebar' ? 'sidebar' : 'page'},
  });
  const sidebar=nodes(exports.default()).find(node=>node.props?.onPageChange && Object.hasOwn(node.props,'currentPage'));
  assert.equal(sidebar.props.currentPage,canOperate ? 'chat' : 'dashboard');
  const breadcrumb = nodes(exports.default()).find(node => node.type === 'breadcrumb');
  const jobs = nodes(breadcrumb.props.children).find(node => node.type === 'button');
  assert.equal(jobs.props['aria-haspopup'], 'dialog');
  assert.equal(text(jobs), '后台任务');
});
