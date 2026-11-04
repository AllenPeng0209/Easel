import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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
const idea = (id, title = id) => ({ id, title, note: '备注', source: '已有选题', status: 'pending' });
const project = (id, title = id, extra = {}) => ({ id, title, brief: '已有创作要求', reference_url: '',
  aspect_ratio: '9:16', revision: 1, stage: 'draft', beeftv_canvas_id: 'canvas-' + id,
  engine_current_revision: 1, engine_dirty: false, deliverables: [], reviews: [], comments: [], renders: [], ...extra });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

function compile(path, dependencies, globals) {
  const source = readFileSync(new URL(`../../web/frontend/src/${path}`, import.meta.url), 'utf8');
  const exports = {};
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2023, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  runInNewContext(code, { exports, URL, Date, AbortController, AbortSignal, setTimeout, clearTimeout,
    ...globals, require: name => Object.hasOwn(dependencies, name) ? dependencies[name]
      : name.endsWith('.css') ? {} : require(name) });
  return exports;
}

async function mount(file, initialProps = {}, { ideas = [], projects = [], detail, role = 'operator', isPlatformAdmin = false, trends = [], saveIdea } = {}) {
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
  const componentApi = {
    fetchIdeas: async () => { reads.push('ideas'); return ideas; },
    fetchTrendSources: async () => ({ sources: trends.map(group => ({ id: group.platform, label: group.label, default: true })), warnings: [] }),
    fetchTrends: async () => ({ trends, updated: 1234, warnings: [] }),
    createIdea: saveIdea ? async input => { writes.push(input); return saveIdea(input); } : denied,
    updateIdea: denied, deleteIdea: denied, createSchedule: denied,
  };
  const workshopApi = { ...workshop,
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
    '../lib/api': componentApi, '../lib/workshop': workshopApi, '../lib/businessObjectApi': businessApi,
    './TrendSourcesDialog': { default: () => null },
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

test('a completed production idea links to the exact workshop in the current IP', async () => {
  const link = `#/business/active/workshop/${A}/artifact/${B}`;
  const reference = url => ({ platform: 'easel_workshop', url });
  const ui = await mount('IdeasPage', { onUseTopic() {} }, { ideas: [
    { ...idea('current'), provenance: reference('http://127.0.0.1:7860/' + link) },
    { ...idea('other'), provenance: reference('http://127.0.0.1:7860/' + link.replace('/active/', '/other/')) },
    { ...idea('external'), provenance: reference('https://example.test/' + link) },
  ] });
  try {
    assert.equal(ui.document.querySelector('[data-idea-id="current"] a')?.getAttribute('href'), link);
    assert.ok(!ui.document.querySelector('[data-idea-id="other"] a'));
    assert.ok(!ui.document.querySelector('[data-idea-id="external"] a'));
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

test('idea navigation anchors and focuses only the exact existing idea without creating content', async () => {
  const ui = await mount('IdeasPage', { initialIdeaId: 'b'.repeat(12), onUseTopic() { throw new Error('no auto creation'); } },
    { ideas: [idea('a'.repeat(12), 'First'), idea('b'.repeat(12), 'Target')] });
  try {
    assert.equal(ui.document.querySelector('[data-idea-selected="true"]')?.getAttribute('data-idea-id'), 'b'.repeat(12));
    assert.ok(ui.focused.includes('b'.repeat(12)) && ui.scrolled.includes('b'.repeat(12)));
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

test('missing or malformed idea ID is explicit instead of highlighting the first idea', async () => {
  for (const initialIdeaId of ['c'.repeat(12), '../not-an-id', 'https://external.test/idea', 'idea-one\n']) {
    const ui = await mount('IdeasPage', { initialIdeaId, onUseTopic() {} }, { ideas: [idea('a'.repeat(12), 'First')] });
    try { assert.match(ui.document.body.textContent, /选题.*不可用|选题.*不存在/);
      assert.equal(ui.document.querySelector('[data-idea-selected="true"]'), null); assert.deepEqual(ui.writes, []);
    } finally { await ui.dispose(); }
  }
});

test('the stored legacy idea-one ID from the Host fixture still focuses its real idea', async () => {
  const ui = await mount('IdeasPage', { initialIdeaId: 'idea-one', onUseTopic() {} },
    { ideas: [idea('a'.repeat(12), 'New ID'), idea('idea-one', 'Legacy stored idea')] });
  try {
    assert.equal(ui.document.querySelector('[data-idea-selected="true"]')?.getAttribute('data-idea-id'), 'idea-one');
    assert.equal(ui.focused.at(-1), 'idea-one');
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

test('default idea page retains its ordinary list without automatic focus or writes', async () => {
  const ui = await mount('IdeasPage', { onUseTopic() {} }, { ideas: [idea('a'.repeat(12), 'Ordinary')] });
  try { assert.match(ui.document.body.textContent, /Ordinary/); assert.equal(ui.document.querySelector('[data-idea-selected="true"]'), null);
    assert.equal(ui.focused.length, 0); assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

test('starting creation from a saved idea passes its ID on explicit click', async () => {
  const used = [];
  const ui = await mount('IdeasPage', { onUseTopic: id => used.push(id) },
    { ideas: [idea('idea-one', 'Same title'), idea('idea-two', 'Same title')] });
  try {
    assert.deepEqual(used, []);
    const card = [...ui.document.querySelectorAll('.idea-card')][1];
    const start = [...card.querySelectorAll('button')].find(button => button.textContent.includes('做内容'));
    assert.ok(start);
    await act(async () => start.click());
    assert.deepEqual(used, ['idea-two']);
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

const trendGroups = ['weibo', 'douyin'].map(platform => ({ platform, label: platform,
  items: [{ title: 'Same title', url: 'https://source.test/' + platform, hot: '' }] }));

test('unsaved trend explicitly saves provenance once before starting with the returned ID', async () => {
  const pending = deferred();
  const used = [];
  const ui = await mount('TrendsPage', { onUseSavedIdea: id => used.push(id) },
    { trends: trendGroups, saveIdea: () => pending.promise });
  try {
    assert.deepEqual(ui.writes, []);
    const start = ui.document.querySelector('.trend-use');
    assert.equal(start.textContent, '保存并做内容');
    await act(async () => { start.click(); start.click(); });
    assert.equal(ui.writes.length, 1);
    assert.deepEqual({ ...ui.writes[0].provenance }, { platform: 'weibo', url: 'https://source.test/weibo', collectedAt: 1234 });
    assert.deepEqual(used, []);
    await act(async () => pending.resolve({ id: 'host-idea-one' }));
    assert.deepEqual(used, ['host-idea-one']);
    await act(async () => ui.document.querySelector('.trend-use').click());
    assert.deepEqual(used, ['host-idea-one', 'host-idea-one']);
    assert.equal(ui.writes.length, 1);
  } finally { await ui.dispose(); }
});

test('saved trend identity uses platform, URL and title, and failed saves stay retryable', async () => {
  const used = [];
  let attempts = 0;
  const ui = await mount('TrendsPage', { onUseSavedIdea: id => used.push(id) }, {
    trends: trendGroups, ideas: [{ id: 'existing', title: 'Same title', provenance: {
      platform: 'weibo', url: 'https://source.test/weibo', collectedAt: 1000 } }],
    saveIdea: () => { if (!attempts++) throw new Error('unavailable'); return { id: 'new-douyin' }; },
  });
  try {
    const [saved, unsaved] = ui.document.querySelectorAll('.trend-use');
    assert.equal(saved.textContent, '做内容');
    assert.equal(unsaved.textContent, '保存并做内容');
    await act(async () => saved.click());
    assert.deepEqual(used, ['existing']);
    assert.equal(ui.writes.length, 0);
    await act(async () => unsaved.click());
    assert.match(ui.document.body.textContent, /保存选题失败/);
    assert.deepEqual(used, ['existing']);
    await act(async () => unsaved.click());
    assert.deepEqual(used, ['existing', 'new-douyin']);
    assert.equal(ui.writes.length, 2);
  } finally { await ui.dispose(); }
});

test('changing the idea reference moves selection to that exact existing idea', async () => {
  const first = 'a'.repeat(12), second = 'b'.repeat(12);
  const ui = await mount('IdeasPage', { initialIdeaId: first, onUseTopic() {} }, { ideas: [idea(first), idea(second)] });
  try {
    await ui.render({ initialIdeaId: second, onUseTopic() {} });
    assert.equal(ui.document.querySelectorAll('[data-idea-selected="true"]').length, 1);
    assert.equal(ui.document.querySelector('[data-idea-selected="true"]').getAttribute('data-idea-id'), second);
    assert.equal(ui.focused.at(-1), second); assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

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

test('deep link observes a completed export without collecting until explicit user action', async () => {
  const receipt = { id: 'render-one', operation_id: 'op', task_id: 'task', content_revision: 1, engine_revision: 1,
    status: 'succeeded', progress: 1, artifact_id: '', created_at: '', updated_at: '' };
  const ui = await mount('VideoWorkshopPage', { initialProjectId: A }, { projects: [project(A, 'Exported', { renders: [receipt] })] });
  try {
    await ui.flush(); assert.deepEqual(ui.writes, []);
    const collect = [...ui.document.querySelectorAll('button')].find(button => button.textContent.includes('收取已导出的成片'));
    assert.ok(collect, 'completed export collection requires an explicit, correctly labelled control');
    await act(async () => collect.click()); await ui.flush();
    assert.equal(ui.writes.filter(row => row.options?.method === 'POST' && row.url.endsWith('/collect')).length, 1);
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
    assert.match(ui.document.querySelector('video').getAttribute('src'), /old\.mp4$/);
    const button = [...ui.document.querySelectorAll('button')].find(item => item.textContent === '将此成片带回聊天');
    assert.ok(button);
    await act(async () => button.click());
    assert.deepEqual(refs, [{ id: A, revision: 2, artifactId: old.id, artifactSha256: old.sha256 }]);
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

test('canvas share explicitly rereads the saved engine revision and rejects an unavailable revision', async () => {
  const refs = [];
  let engineRevision = 1;
  const current = project(A);
  const ui = await mount('VideoWorkshopPage', { initialProjectId: A, initialView: 'canvas',
    onUseWorkshop: value => refs.push({ ...value }) }, { projects: [current], isPlatformAdmin: true,
    detail: () => ({ ...current, engine_current_revision: engineRevision }),
  });
  try {
    assert.equal(ui.document.querySelector('#workshop-canvas-tab')?.getAttribute('aria-selected'), 'true');
    assert.deepEqual(refs, []);
    const button = [...ui.document.querySelectorAll('button')].find(item => item.textContent === '将画布状态带回聊天');
    assert.ok(button);
    engineRevision = null;
    await act(async () => button.click());
    assert.match(ui.document.body.textContent, /画布.*版本.*无法|无法.*画布.*版本/);
    assert.deepEqual(refs, []);
    engineRevision = 9;
    await act(async () => button.click());
    assert.deepEqual(refs, [{ id: A, revision: 1, engineRevision: 9 }]);
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});

test('a pending canvas share is cancelled when the selected project changes', async () => {
  const refs = [];
  const delayed = deferred();
  const first = project(A);
  const second = project(B);
  let hold = false;
  const onUseWorkshop = value => refs.push(value);
  const ui = await mount('VideoWorkshopPage', { initialProjectId: A, initialView: 'canvas', onUseWorkshop }, {
    projects: [first, second], isPlatformAdmin: true, detail: id => id === B ? second : hold ? delayed.promise : first,
  });
  try {
    hold = true;
    const button = [...ui.document.querySelectorAll('button')].find(item => item.textContent === '将画布状态带回聊天');
    assert.ok(button);
    await act(async () => button.click());
    const request = ui.reads.filter(read => Array.isArray(read) && read[0] === 'detail').at(-1)[2];
    await ui.render({ initialProjectId: B, initialView: 'canvas', onUseWorkshop });
    assert.equal(request.aborted, true);
    await act(async () => delayed.resolve(first));
    assert.deepEqual(refs, []);
    assert.deepEqual(ui.writes, []);
  } finally { await ui.dispose(); }
});
