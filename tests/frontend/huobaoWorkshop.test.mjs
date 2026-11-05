import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react'), ts = require('typescript'), { parseHTML } = require('linkedom');
const id = 'a'.repeat(32);
const project = { id, title: '橡皮屑里的十分钟', brief: '# 方案\n' + '很长的剧本内容。'.repeat(900),
  aspect_ratio: '9:16', revision: 2, stage: 'draft', script: '', assets: [], shots: [], deliverables: [],
  reviews: [], comments: [], renders: [], publications: [], publication_runs: [], updated_at: '2026-11-05T08:00:00Z' };

async function mount(props = {}, initial = project) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { origin: 'http://127.0.0.1:7860' };
  const previous = new Map();
  for (const [key, value] of Object.entries({ window, document, IS_REACT_ACT_ENVIRONMENT: true })) {
    previous.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, writable: true, configurable: true });
  }
  const calls = [], modules = new Map(), intervals = new Map();
  let current = initial, timer = 0;
  const scope = { projectUrl: value => value, projectFetch: async (url, options = {}) => {
    calls.push({ url, options });
    const data = url.endsWith('/engine/open') ? { engine: 'huobao-drama', upstream_commit: 'pinned-source',
      url: '/huobao-studio/drama/1/episode/1?xiangyaProject=' + id } : url.endsWith('/projects') ? { projects: [current] } : current;
    return { ok: true, status: 200, json: async () => data };
  } };
  const load = path => {
    if (modules.has(path)) return modules.get(path);
    const exports = {}; modules.set(path, exports);
    const code = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    runInNewContext(code, { exports, URL, AbortController, window, document, HTMLElement: window.HTMLElement, location: window.location,
      setTimeout: () => 1, clearTimeout() {},
      setInterval: callback => { intervals.set(++timer, callback); return timer; }, clearInterval: key => intervals.delete(key),
      require: name => name === 'react' || name === 'react/jsx-runtime' ? require(name)
        : name.endsWith('/projectScope') ? scope : name.endsWith('/ProjectSpace') ? { useIpPermissions: () => ({ canOperate: true }) }
          : name.endsWith('/icons') ? new Proxy({}, { get: () => () => null }) : name.endsWith('.css') ? {}
            : load(['.tsx', '.ts'].map(ext => resolve(dirname(path), name + ext)).find(existsSync)),
    });
    return exports;
  };
  const Component = load(fileURLToPath(new URL('../../web/frontend/src/components/VideoWorkshopPage.tsx', import.meta.url))).default;
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const tick = () => new Promise(resolve => setImmediate(resolve));
  await React.act(async () => { root.render(React.createElement(Component, props)); await tick(); });
  return { document, calls,
    setCurrent(next) { current = next; },
    async click(name) { const button = [...document.querySelectorAll('button')].find(node => node.textContent.trim() === name);
      assert.ok(button, `Missing button: ${name}`);
      await React.act(async () => { button.dispatchEvent(new window.Event('click', { bubbles: true })); await tick(); }); },
    async poll(next) { current = next; await React.act(async () => { for (const callback of [...intervals.values()]) callback(); await tick(); }); },
    async enter() { const button = document.querySelector(`[aria-label="进入项目：${project.title}"]`);
      await React.act(async () => { button.dispatchEvent(new window.Event('click', { bubbles: true })); await tick(); }); },
    async dispose() { await React.act(async () => root.unmount()); for (const [key, value] of previous) value ? Object.defineProperty(globalThis, key, value) : delete globalThis[key]; },
  };
}

test('workshop entry restores the compact project table and bounds long script previews', async () => {
  const ui = await mount();
  try {
    assert.ok(ui.document.querySelector('table[aria-label="视频项目列表"]'));
    assert.equal(ui.document.querySelectorAll('tbody tr').length, 1);
    assert.ok(ui.document.querySelector('.workshop-topic-cell p').textContent.length <= 180);
    assert.equal(ui.document.querySelectorAll('iframe').length, 0);
    assert.equal(ui.calls.filter(call => call.options.method === 'POST').length, 0);
  } finally { await ui.dispose(); }
});

test('rework materials refresh the project and expose media, subtitle files and exact shot candidates', async () => {
  const ui = await mount();
  try {
    await ui.enter();
    ui.setCurrent({ ...project, revision: 3, assets: [
      { id: 'clean', name: '净版视频', kind: 'video', path: 'film/clean.mp4', versions: [] },
      { id: 'voice', name: '独立对白', kind: 'audio', path: 'film/voice.wav', versions: [] },
      { id: 'caption', name: '可编辑字幕', kind: 'subtitle', path: 'film/subtitles.ass', versions: [] },
      { id: 'board', name: '逐镜表', kind: 'document', path: 'film/storyboard.json', versions: [] },
      { id: 'future', name: '尚未生成的封面', kind: 'image', path: '', versions: [] },
    ], shots: [{ id: 'shot-one', title: '母亲倾听', candidates: [
      { id: 'old', path: 'film/old.mp4', sha256: '0'.repeat(64) },
      { id: 'current', path: 'film/current.mp4', sha256: '1'.repeat(64) },
    ], selected_candidate_id: 'current' }], deliverables: [{ id: 'final', path: 'film/final.mp4', revision: 3, sha256: '2'.repeat(64) }] });
    const before = ui.calls.length;
    await ui.click('二修素材');
    const dialog = ui.document.querySelector('[role="dialog"][aria-label="二修素材与制作文件"]');
    assert.ok(dialog);
    assert.ok(ui.calls.slice(before).some(call => call.url.endsWith('/projects/' + id)));
    const links = [...dialog.querySelectorAll('a[download]')].map(node => node.getAttribute('href'));
    for (const name of ['clean.mp4', 'voice.wav', 'subtitles.ass', 'storyboard.json', 'old.mp4', 'current.mp4', 'final.mp4']) {
      assert.ok(links.some(href => href.endsWith('/' + name)), `Missing material ${name}`);
    }
    assert.ok(dialog.querySelector('audio[controls]'));
    assert.ok(dialog.querySelector('video[controls]'));
    assert.equal(dialog.querySelectorAll('iframe, object, embed').length, 0);
    assert.equal([...dialog.querySelectorAll('a')].some(node => !node.getAttribute('href')), false);
    assert.match(dialog.textContent, /尚未生成/);
    assert.match(dialog.textContent, /当前选片/);
    await ui.click('关闭素材');
    assert.equal(ui.document.querySelectorAll('[aria-label="二修素材与制作文件"]').length, 0);
    assert.ok(ui.document.querySelector('iframe[title="火宝原版视频制作工作台"]'));
  } finally { await ui.dispose(); }
});

test('entering a project loads the original upstream workbench through its scoped runtime', async () => {
  const ui = await mount();
  try {
    await ui.enter();
    const frame = ui.document.querySelector('iframe[title="火宝原版视频制作工作台"]');
    assert.ok(frame);
    assert.match(frame.getAttribute('src'), /huobao-studio\/drama\/1\/episode\/1/);
    const call = ui.calls.find(call => call.url.endsWith('/engine/open'));
    assert.equal(JSON.parse(call.options.body).expected_revision, 2);
  } finally { await ui.dispose(); }
});

test('review follows an asynchronous publication result without reopening the project', async () => {
  const pending = { ...project, stage: 'publishing', publication_runs: [{ result: { status: 'executing' } }] };
  const ui = await mount({ initialProjectId: id, initialView: 'review' }, pending);
  try {
    assert.match(ui.document.body.textContent, /正在发布/);
    await ui.poll({ ...pending, publication_runs: [{ result: { status: 'submitted', message: '已提交到测试接收端' } }] });
    assert.match(ui.document.body.textContent, /已提交，等待平台回执/);
    assert.doesNotMatch(ui.document.body.textContent, /正在发布/);
  } finally { await ui.dispose(); }
});
