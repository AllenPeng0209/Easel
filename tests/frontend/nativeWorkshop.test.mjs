import { componentModule } from './agentMessagesHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript'), React = require('react');
const { parseHTML } = require('linkedom');
const compile = name => ts.transpileModule(readFileSync(new URL('../../web/frontend/src/' + name, import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const id = 'a'.repeat(32), artifact = 'b'.repeat(32), approval = 'c'.repeat(32), hash = 'd'.repeat(64);
function film() {
  return { id, title: '先听孩子说完', brief: '家庭教育短剧', aspect_ratio: '9:16', revision: 3, stage: 'rendered',
    script: '母亲先听孩子说完。', assets: [{ id: 'mother', kind: 'character', name: '母亲', path: 'film/mother.png', versions: [] }],
    shots: [{ id: 'shot-1', title: '倾听', prompt: '母亲放下手机', seconds: 5, asset_ids: ['mother'], selected_candidate_id: '1'.repeat(32),
      candidates: [{ id: '1'.repeat(32), path: 'film/v1.mp4', sha256: hash }, { id: '2'.repeat(32), path: 'film/v2.mp4', sha256: hash }] }],
    deliverables: [{ id: artifact, path: 'film/final.mp4', sha256: hash, revision: 3 }], reviews: [], comments: [], renders: [], publications: [], publication_runs: [],
    publication_preparation: { approval_id: approval, payload_hash: hash, revision: 3, artifact_id: artifact, subject: {
      platform: { id: 'douyin', name: '抖音' }, account: { id: 'account', name: '家庭教育账号' }, title: '先听孩子说完', body: '家庭教育短剧', tags: '家庭教育', aiGenerated: true } } };
}

async function view(initialView) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.HTMLElement.prototype.scrollIntoView = () => {};
  const saved = new Map();
  for (const [key, value] of Object.entries({ window, document, HTMLElement: window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true })) {
    saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  }
  const { createRoot } = require('react-dom/client'), runtime = require('react/jsx-runtime');
  const root = createRoot(document.getElementById('root'));
  let current = film();
  const calls = [], inputs = [];
  const scope = { projectUrl: value => value, projectFetch: async (url, options = {}) => {
    calls.push({ url, options });
    let data = url.split('?')[0].endsWith('/projects') ? { projects: [current] } : current;
    if (url.endsWith('/select')) {
      current = { ...current, revision: 4, shots: [{ ...current.shots[0], selected_candidate_id: '2'.repeat(32) }] }; data = current;
    }
    if (url.endsWith('/reviews')) {
      current = { ...current, stage: 'publishing', publication_runs: [{ result: { status: 'submitted' } }] }; data = current;
    }
    if (url.endsWith('/publish/prepare')) {
      current = { ...current, publication_preparation: { ...current.publication_preparation,
        payload_hash: 'e'.repeat(64), subject: { ...current.publication_preparation.subject,
          account: { id: 'new-account', name: '重新核对的账号' } } } };
      data = { project: current };
    }
    return { ok: true, status: 200, json: async () => data };
  } };
  const environment = { URL, AbortController, console, window, document, setTimeout: () => 1, clearTimeout() {},
    setInterval: () => 1, clearInterval() {} };
  const library = {};
  runInNewContext(compile('lib/workshop.ts'), { ...environment, exports: library, require: name => name === './scopedReadCache' ? componentModule('lib/scopedReadCache.ts') : scope });
  const module = {};
  runInNewContext(compile('components/WorkshopReviewPanel.tsx'), { ...environment, exports: module,
    require: name => name === 'react' ? React : name === 'react/jsx-runtime' ? {
      ...runtime, jsx(type, props, key) { if (type === 'input') inputs.push(props); return runtime.jsx(type, props, key); },
      jsxs(type, props, key) { return runtime.jsxs(type, props, key); },
    } : name.endsWith('/workshop') ? library : name.endsWith('/ProjectSpace') ? { useIpPermissions: () => ({ canOperate: true }) }
      : name.endsWith('/icons') ? new Proxy({}, { get: () => () => null }) : {},
  });
  const tick = () => new Promise(resolve => setImmediate(resolve));
  function Harness() { const [project, update] = React.useState(current); return React.createElement(module.default, { project, onUpdate: update }); }
  await React.act(async () => { root.render(React.createElement(Harness)); await tick(); });
  return { document, calls, inputs,
    async click(text) { const button = [...document.querySelectorAll('button')].find(node => node.textContent === text);
      assert.ok(button, 'Missing button ' + text);
      await React.act(async () => {
        button.dispatchEvent(new window.Event('click', { bubbles: true }));
        const form = button.closest('form');
        if (form && button.getAttribute('type') !== 'button') form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
        await tick();
      }); },
    async check() { await React.act(async () => { inputs.findLast(row => row.type === 'checkbox').onChange({ target: { checked: true } }); await tick(); }); },
    async dispose() { await React.act(async () => root.unmount()); for (const [key, value] of saved) value ? Object.defineProperty(globalThis, key, value) : delete globalThis[key]; },
  };
}

test('review shows exact account and one approval submits the bound film for automatic publication', async () => {
  const ui = await view('review');
  try {
    assert.match(ui.document.body.textContent, /家庭教育账号/);
    await ui.check();
    await ui.click('审核通过并发布');
    const call = ui.calls.find(row => row.url.endsWith('/reviews'));
    assert.equal(JSON.parse(call.options.body).publication_approval_id, approval);
    assert.equal(JSON.parse(call.options.body).publication_payload_hash, hash);
    assert.match(ui.document.body.textContent, /已提交/);
  } finally { await ui.dispose(); }
});

test('an expired or changed publication can be prepared again before human approval', async () => {
  const ui = await view('review');
  try {
    await ui.click('重新核对发布信息');
    await ui.click('核对账号并准备发布');
    assert.match(ui.document.body.textContent, /重新核对的账号/);
    await ui.check();
    await ui.click('审核通过并发布');
    const call = ui.calls.find(row => row.url.endsWith('/reviews'));
    assert.equal(JSON.parse(call.options.body).publication_payload_hash, 'e'.repeat(64));
  } finally { await ui.dispose(); }
});
