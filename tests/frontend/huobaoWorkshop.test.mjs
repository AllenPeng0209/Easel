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

async function mount(props = {}, initial = project, { network } = {}) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { origin: 'http://127.0.0.1:7860' };
  const previous = new Map();
  for (const [key, value] of Object.entries({ window, document, IS_REACT_ACT_ENVIRONMENT: true })) {
    previous.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { value, writable: true, configurable: true });
  }
  const calls = [], modules = new Map(), timers = new Map();
  let current = initial, timer = 0, now = 0;
  const scope = { projectUrl: value => value, projectFetch: async (url, options = {}) => {
    calls.push({ url, options });
    if (network) { const response = await network(url, options); if (response) return response; }
    const data = url.endsWith('/engine/open') ? { engine: 'huobao-drama', upstream_commit: 'pinned-source',
      url: '/huobao-studio/drama/1/episode/1?xiangyaProject=' + id } : url.split('?')[0].endsWith('/projects') ? { projects: [current] } : current;
    return { ok: true, status: 200, json: async () => data };
  } };
  const load = path => {
    if (modules.has(path)) return modules.get(path);
    const exports = {}; modules.set(path, exports);
    const code = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    runInNewContext(code, { exports, URL, Error, AbortController, window, document, HTMLElement: window.HTMLElement, location: window.location,
      setTimeout: (callback, delay = 0) => { timers.set(++timer, { callback, at: now + delay }); return timer; }, clearTimeout: key => timers.delete(key),
      setInterval: (callback, delay) => { timers.set(++timer, { callback, at: now + delay, interval: delay }); return timer; }, clearInterval: key => timers.delete(key),
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
  const advance = async milliseconds => {
    const end = now + milliseconds;
    await React.act(async () => {
      for (;;) {
        const due = [...timers.entries()].filter(([, task]) => task.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [key, task] = due; now = task.at;
        if (task.interval) task.at += task.interval; else timers.delete(key);
        task.callback(); await tick();
      }
      now = end;
    });
  };
  return { document, calls, advance,
    setCurrent(next) { current = next; },
    async render(nextProps) { await React.act(async () => { root.render(React.createElement(Component, nextProps)); await tick(); }); },
    async settle(callback) { await React.act(async () => { callback(); await tick(); }); },
    async focus() { await React.act(async () => { window.dispatchEvent(new window.Event('focus')); await tick(); }); },
    async message(action) { const iframe = document.querySelector('iframe'); assert.ok(iframe);
      const source = {}; Object.defineProperty(iframe, 'contentWindow', { value: source, configurable: true });
      const event = new window.Event('message'); Object.assign(event, { origin: window.location.origin, source, data: { type: 'xiangya-huobao', project: id, action } });
      await React.act(async () => { window.dispatchEvent(event); await tick(); }); },
    async click(name) { const button = [...document.querySelectorAll('button')].find(node => node.textContent.trim() === name);
      assert.ok(button, `Missing button: ${name}`);
      await React.act(async () => { button.dispatchEvent(new window.Event('click', { bubbles: true })); await tick(); }); },
    async poll(next) { current = next; await advance(8000); },
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

test('project entry opens fullscreen with a reachable return and no retired materials shortcut', async () => {
  const ui = await mount();
  try {
    await ui.enter();
    const shell = ui.document.querySelector('.huobao-workshop-shell');
    assert.ok(shell.classList.contains('huobao-workshop-shell--fullscreen'));
    assert.equal([...ui.document.querySelectorAll('button')].some(button => button.textContent.includes('二修素材')), false);
    assert.ok(ui.document.querySelector('iframe[title="火宝原版视频制作工作台"]'));
    await ui.click('返回视频列表');
    assert.ok(ui.document.querySelector('table[aria-label="视频项目列表"]'));
    assert.equal(ui.document.querySelector('.huobao-workshop-shell'), null);
  } finally { await ui.dispose(); }
});

test('linked review defaults to fullscreen and removes both materials shortcuts', async () => {
  const ui = await mount({ initialProjectId: id, initialView: 'review' });
  try {
    assert.ok(ui.document.querySelector('.huobao-workshop-shell--fullscreen'));
    assert.ok(ui.document.querySelector('[aria-label="成片审核与发布"]'));
    assert.equal([...ui.document.querySelectorAll('button')].some(button => button.textContent.includes('二修素材')), false);
    await ui.click('返回视频列表');
    assert.ok(ui.document.querySelector('table[aria-label="视频项目列表"]'));
  } finally { await ui.dispose(); }
});

test('contained layout remains available only when explicitly requested', async () => {
  const ui = await mount({ initialProjectId: id, presentation: 'contained' });
  try {
    assert.ok(ui.document.querySelector('.huobao-workshop-shell'));
    assert.equal(ui.document.querySelector('.huobao-workshop-shell--fullscreen'), null);
    await ui.click('返回视频列表');
    assert.ok(ui.document.querySelector('table'));
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

const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

test('a slow workshop read completes without overlapping timer, focus or manual refresh requests', async () => {
  const first = deferred();
  const ui = await mount({}, project, { network: url => url.split('?')[0].endsWith('/projects') ? first.promise : undefined });
  try {
    await ui.advance(16000);
    await ui.focus();
    await ui.click('刷新');
    assert.equal(ui.calls.filter(call => call.url.split('?')[0].endsWith('/projects')).length, 1, 'A pending read must not spawn more reads');
    await ui.settle(() => first.resolve(response({ projects: [project] })));
    assert.ok(ui.document.querySelector('table[aria-label="视频项目列表"]'), 'The slow response must reach the page');
    await ui.advance(14999);
    assert.equal(ui.calls.filter(call => call.url.split('?')[0].endsWith('/projects')).length, 1);
    await ui.advance(1);
    assert.equal(ui.calls.filter(call => call.url.split('?')[0].endsWith('/projects')).length, 2, 'Poll again only after a complete quiet interval');
  } finally { await ui.dispose(); }
});

test('opening a project shows loading until its exact detail arrives and keeps an independent return button', async () => {
  const detail = deferred();
  const ui = await mount({}, project, { network: url => url.endsWith('/projects/' + id) ? detail.promise : undefined });
  try {
    await ui.enter();
    assert.match(ui.document.querySelector('[role="status"]').textContent, /正在读取视频项目/);
    assert.doesNotMatch(ui.document.body.textContent, /指定视频项目暂不可用/);
    await ui.settle(() => detail.resolve(response(project)));
    assert.ok(ui.document.querySelector('iframe'));
    await ui.click('返回视频列表');
    assert.ok(ui.document.querySelector('table[aria-label="视频项目列表"]'));
    assert.equal(ui.document.querySelector('iframe'), null);
  } finally { await ui.dispose(); }
});

test('a failed project read offers retry and does not masquerade as a missing project', async () => {
  let unavailable = true;
  const ui = await mount({ initialProjectId: id }, project, { network: url => url.endsWith('/projects/' + id)
    ? response(unavailable ? { detail: '服务暂时不可用' } : project, unavailable ? 503 : 200) : undefined });
  try {
    assert.match(ui.document.body.textContent, /服务暂时不可用/);
    assert.doesNotMatch(ui.document.body.textContent, /项目不存在/);
    unavailable = false;
    await ui.click('重试');
    assert.ok(ui.document.querySelector('iframe'));
    await ui.click('返回视频列表');
    assert.ok(ui.document.querySelector('table'));
  } finally { await ui.dispose(); }
});

test('missing projects have a distinct empty state and a route back to the list', async () => {
  const ui = await mount({ initialProjectId: id }, project, { network: url => url.endsWith('/projects/' + id)
    ? response({ detail: 'not found' }, 404) : undefined });
  try {
    assert.match(ui.document.body.textContent, /项目不存在或已被移除/);
    await ui.click('返回视频列表');
    assert.ok(ui.document.querySelector('table'));
  } finally { await ui.dispose(); }
});

test('switching projects discards late reads from the previous project', async () => {
  const nextId = 'b'.repeat(32), old = deferred(), next = deferred();
  const nextProject = { ...project, id: nextId, title: 'Second project' };
  const ui = await mount({ initialProjectId: id }, project, { network: url => url.split('?')[0].endsWith('/projects')
    ? response({ projects: [project, nextProject] }) : url.endsWith('/projects/' + id) ? old.promise
      : url.endsWith('/projects/' + nextId) ? next.promise : undefined });
  try {
    await ui.render({ initialProjectId: nextId });
    assert.equal(ui.calls.find(call => call.url.endsWith('/projects/' + id)).options.signal.aborted, true);
    await ui.settle(() => next.resolve(response(nextProject)));
    assert.equal(ui.document.querySelector('.huobao-workshop-shell').getAttribute('data-workshop-project-id'), nextId);
    await ui.settle(() => old.resolve(response(project)));
    assert.equal(ui.document.querySelector('.huobao-workshop-shell').getAttribute('data-workshop-project-id'), nextId);
  } finally { await ui.dispose(); }
});

test('iframe AI chat navigation hands over context without submitting a production request', async () => {
  const opened = [], submitted = [];
  const ui = await mount({ initialProjectId: id, onOpenChat: reference => opened.push(reference), onUseWorkshop: reference => submitted.push(reference) });
  try {
    await ui.message('chat');
    assert.equal(submitted.length, 0, 'Opening chat must not invoke the production callback');
    assert.equal(opened.length, 1);
    assert.equal(opened[0].id, id);
  } finally { await ui.dispose(); }
});

test('an unavailable embedded workbench can be retried without leaving the project', async () => {
  let unavailable = true;
  const ui = await mount({ initialProjectId: id }, project, { network: url => url.endsWith('/engine/open') && unavailable
    ? response({ detail: '工作台暂未就绪' }, 503) : undefined });
  try {
    assert.match(ui.document.body.textContent, /工作台暂未就绪/);
    unavailable = false;
    await ui.click('重新连接');
    assert.ok(ui.document.querySelector('iframe'));
    await ui.click('返回视频列表');
    assert.ok(ui.document.querySelector('table'));
  } finally { await ui.dispose(); }
});

test('leaving the workbench cancels a pending AI chat handoff', async () => {
  const handoff = deferred(), opened = [];
  let detailReads = 0;
  const ui = await mount({ initialProjectId: id, onOpenChat: reference => opened.push(reference) }, project, {
    network: url => url.endsWith('/projects/' + id) && ++detailReads === 2 ? handoff.promise : undefined,
  });
  try {
    await ui.message('chat');
    await ui.click('返回视频列表');
    await ui.settle(() => handoff.resolve(response(project)));
    assert.equal(opened.length, 0, 'A stale handoff must not navigate after the user left');
    assert.ok(ui.document.querySelector('table'));
  } finally { await ui.dispose(); }
});

test('an unrelated same-project render does not cancel an AI chat handoff', async () => {
  const handoff = deferred(), opened = [];
  let detailReads = 0;
  const ui = await mount({ initialProjectId: id, onOpenChat: reference => opened.push(reference) }, project, {
    network: url => url.endsWith('/projects/' + id) && ++detailReads === 2 ? handoff.promise : undefined,
  });
  try {
    await ui.message('chat');
    await ui.render({ initialProjectId: id, onOpenChat: reference => opened.push(reference) });
    await ui.settle(() => handoff.resolve(response(project)));
    assert.equal(opened.length, 1, 'Unrelated renders must retain the in-progress handoff');
    assert.equal(opened[0].id, id);
  } finally { await ui.dispose(); }
});
