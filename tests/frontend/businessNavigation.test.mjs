import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, posix } from 'node:path';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const React = require('react');
const { act } = React;
const { createRoot } = require('react-dom/client');
const { parseHTML } = require('linkedom');
const A = 'a'.repeat(32), B = 'b'.repeat(32);
const project = (id, title = id, extra = {}) => ({ id, title, brief: '已有创作要求', reference_url: '',
  aspect_ratio: '9:16', revision: 1, stage: 'draft', beeftv_canvas_id: 'canvas-' + id,
  engine_current_revision: 1, engine_dirty: false, deliverables: [], reviews: [], comments: [], renders: [], ...extra });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

function compile(path, dependencies, globals) {
  const source = readFileSync(new URL(`../../web/frontend/src/${path}`, import.meta.url), 'utf8');
  const exports = {};
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2023, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  runInNewContext(code, { exports, URL, Date, AbortController, AbortSignal, setTimeout, clearTimeout, setInterval, clearInterval,
    ...globals, require: name => Object.hasOwn(dependencies, name) ? dependencies[name]
      : name.endsWith('.css') ? {} : name.startsWith('.') ? compile(['.tsx','.ts'].map(ext => posix.join(dirname(path),name)+ext).find(candidate => existsSync(new URL('../../web/frontend/src/'+candidate,import.meta.url))), dependencies, globals) : require(name) });
  return exports;
}

async function mount(file, initialProps = {}, { projects = [], detail, role = 'operator', isPlatformAdmin = false } = {}) {
  const { window } = parseHTML('<!doctype html><html><body><div id="root"></div></body></html>');
  const document = window.document;
  Object.defineProperty(window, 'location', { value: { origin: 'http://127.0.0.1:7860' }, configurable: true });
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
  const focused = [], scrolled = [], reads = [], writes = [];
  window.HTMLElement.prototype.focus = function () { focused.push(this.getAttribute('data-idea-id') || this.getAttribute('data-workshop-project-id')); };
  window.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this.getAttribute('data-idea-id') || this.getAttribute('data-workshop-project-id')); };
  const old = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true }, document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const denied = async (...args) => { writes.push(args); throw new Error('Business navigation cannot write'); };
  const workshop = compile('lib/workshop.ts', { './projectScope': { projectUrl: value => value,
    projectFetch: async (url, options = {}) => {
      if (options.method && options.method !== 'GET') writes.push({ url, options }); else reads.push(url);
      const receipt = projects[0]?.renders?.[0] || {};
      return { ok: true, json: async () => url.includes('/collect') ? project(projects[0].id, projects[0].title,
        { revision: 2, renders: [{ ...receipt, artifact_id: 'collected' }] }) : receipt };
    } } }, { window, document });
  const workshopApi = { ...workshop,
    openHuobaoStudio: async value => { reads.push(['engine', value.id]); return { url: '/huobao-studio/drama/1/episode/1', engine: 'huobao-drama' }; },
    fetchWorkshopStatus: async () => ({ state: 'unavailable', message: 'fixture' }),
    fetchWorkshopProjects: async () => { reads.push('list'); return { projects }; },
    fetchWorkshopProjectsWithActive: async id => { reads.push(['active', id]); return { projects }; },
    fetchWorkshopProject: async (id, signal) => { reads.push(['detail', id, signal]); return detail ? detail(id) : projects.find(item => item.id === id) || Promise.reject(new Error('404')); },
    createWorkshopProject: denied, updateWorkshopProject: denied, archiveWorkshopProject: denied, connectWorkshopCanvas: denied,
    addWorkshopComment: denied, addWorkshopReview: denied, recordWorkshopPublication: denied,
  };
  const businessApi = compile('lib/businessObjectApi.ts',
    { './projectScope': { ACTIVE_PROJECT_ID: 'active' }, './businessObjects': {} }, { window, document });
  const Component = compile(`components/${file}.tsx`, { react: React, 'react/jsx-runtime': require('react/jsx-runtime'),
    '../lib/workshop': workshopApi, '../lib/businessObjectApi': businessApi,
    './icons': new Proxy({}, { get: () => () => null }), './ProjectSpace': { useIpPermissions: () => ({
      canOperate: role !== 'viewer', isPlatformAdmin, role }) } }, { window, document }).default;
  const root = createRoot(document.getElementById('root'));
  const render = async props => { await act(async () => { root.render(React.createElement(Component, props)); await new Promise(resolve => setImmediate(resolve)); }); };
  const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); };
  await render(initialProps); await flush();
  return { document, reads, writes, focused, scrolled, render, flush,
    async dispose() { await act(async () => root.unmount()); for (const [key, descriptor] of Object.entries(old)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } } };
}

test('workshop deep link opens the requested real project and ordinary entry stays a list', async () => {
  const items = [project(A, 'First'), project(B, 'Exact target')];
  const ui = await mount('VideoWorkshopPage', { initialProjectId: B }, { projects: items, role: 'viewer' });
  try { assert.equal(ui.document.querySelector('[data-workshop-project-id]')?.getAttribute('data-workshop-project-id'), B);
    assert.ok(ui.reads.some(row => Array.isArray(row) && row[0] === 'detail' && row[1] === B)); assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
  const ordinary = await mount('VideoWorkshopPage', {}, { projects: items });
  try { assert.equal(ordinary.document.querySelector('[data-workshop-project-id]'), null);
    assert.match(ordinary.document.body.textContent, /Exact target/); assert.deepEqual(ordinary.writes, []);
  } finally { await ordinary.dispose(); }
});

test('workshop inaccessible detail never opens the first project or a cached target', async () => {
  const ui = await mount('VideoWorkshopPage', { initialProjectId: B }, { projects: [project(A), project(B)],
    detail: async () => { throw new Error('403 inaccessible'); } });
  try { assert.match(ui.document.body.textContent, /项目.*不可用|项目.*无权/);
    assert.equal(ui.document.querySelector('[data-workshop-project-id]'), null); assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

test('switching project navigation aborts old ownership and discards its late response', async () => {
  const old = deferred(), next = deferred();
  const ui = await mount('VideoWorkshopPage', { initialProjectId: A }, { projects: [project(A), project(B)], detail: id => id === A ? old.promise : next.promise });
  try {
    await ui.render({ initialProjectId: B });
    assert.equal(ui.reads.find(row => Array.isArray(row) && row[0] === 'detail' && row[1] === A)[2].aborted, true);
    assert.equal(ui.document.querySelector('[data-workshop-project-id]'), null);
    await act(async () => next.resolve(project(B, 'New target'))); await ui.flush();
    assert.equal(ui.document.querySelector('[data-workshop-project-id]')?.getAttribute('data-workshop-project-id'), B);
    await act(async () => old.resolve(project(A, 'Stale target'))); await ui.flush();
    assert.equal(ui.document.querySelector('[data-workshop-project-id]')?.getAttribute('data-workshop-project-id'), B);
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

test('unmount aborts a pending exact lookup before its eventual response', async () => {
  const pending = deferred();
  const ui = await mount('VideoWorkshopPage', { initialProjectId: A }, { projects: [project(A)], detail: () => pending.promise });
  const signal = ui.reads.find(row => Array.isArray(row) && row[0] === 'detail')[2];
  await ui.dispose();
  assert.equal(signal.aborted, true);
  await act(async () => pending.resolve(project(A)));
  assert.equal(ui.document.getElementById('root').textContent, ''); assert.deepEqual(ui.writes, []);
});

test('malformed workshop ID is unavailable without an exact endpoint request', async () => {
  const ui = await mount('VideoWorkshopPage', { initialProjectId: '../another-project' }, { projects: [project(A)] });
  try { assert.match(ui.document.body.textContent, /项目.*不可用/);
    assert.equal(ui.document.querySelector('[data-workshop-project-id]'), null);
    assert.equal(ui.reads.some(row => Array.isArray(row) && row[0] === 'detail'), false);
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

test('linked workshop opens the exact historical delivery and explicitly sends that delivery to chat', async () => {
  const refs = [];
  const old = { id: 'old-artifact', path: 'old.mp4', sha256: 'c'.repeat(64), revision: 1, created_at: '' };
  const newer = { ...old, id: 'new-artifact', path: 'new.mp4', sha256: 'd'.repeat(64), revision: 2 };
  const ui = await mount('VideoWorkshopPage', {
    initialProjectId: A, initialView: 'review', initialArtifactId: old.id, onUseWorkshop: value => refs.push({ ...value }),
  }, { projects: [project(A, 'Deliveries', { revision: 2, deliverables: [old, newer] })] });
  try {
    assert.deepEqual(refs, []);
    assert.match(ui.document.querySelector('video').getAttribute('src'), /old\.mp4\?playback=original$/);
    const button = [...ui.document.querySelectorAll('button')].find(item => item.textContent === '带回对话继续修改');
    assert.ok(button);
    await act(async () => button.click());
    assert.deepEqual(refs, [{ id: A, revision: 2, artifactId: old.id, artifactSha256: old.sha256 }]);
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});
