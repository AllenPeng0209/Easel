import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const workshop = 'a'.repeat(32);
const artifact = 'f'.repeat(32);
const job = (id, overrides = {}) => ({
  id: id.repeat(32), workshopProjectId: workshop, title: '作品 ' + id, taskId: null, status: 'queued',
  progress: null, elapsedMs: null, createdAt: null, updatedAt: null, finishedAt: null,
  artifactId: null, artifactSha256: null, contentRevision: null, engineRevision: null, ...overrides,
});

async function fixture(run) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const matches = window.HTMLElement.prototype.matches;
  const matchesDescriptor = Object.getOwnPropertyDescriptor(window.HTMLElement.prototype, 'matches');
  Object.defineProperty(window.HTMLElement.prototype, 'matches', { configurable: true, value(selector) {
    return selector === ':focus' ? document.activeElement === this : matches.call(this, selector);
  } });
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  let visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { get: () => visibility });
  const requests = [];
  const timers = new Map();
  const opens = [];
  const closes = [];
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Drawer = componentModule('components/WorkshopJobsDrawer.tsx', { user: 'viewer', project: 'scoped', globals: {
      window, document, HTMLElement: window.HTMLElement, Headers, Response,
      setTimeout(callback, ms) { const id = Symbol(); timers.set(id, { callback, ms }); return id; },
      clearTimeout(id) { timers.delete(id); },
      fetch: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })),
    } }).default;
    const update = (open, canOperate = false) => React.act(() => root.render(React.createElement(Drawer, {
      open, canOperate, onClose() { closes.push('closed'); }, onOpenWorkshop: (...args) => opens.push(args),
    })));
    const resolve = (index, jobs, projectId = 'scoped') => React.act(async () =>
      requests[index].resolve(Response.json({ projectId, jobs })));
    const visibilityTo = value => React.act(() => {
      visibility = value;
      document.dispatchEvent(new window.Event('visibilitychange'));
    });
    await run({ document, update, requests, resolve, visibilityTo, timers, opens, closes });
  } finally {
    await React.act(() => root.unmount());
    if (matchesDescriptor) Object.defineProperty(window.HTMLElement.prototype, 'matches', matchesDescriptor);
    else delete window.HTMLElement.prototype.matches;
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

test('viewer roster displays only observed status/progress/elapsed and navigates to an exact adopted artifact', async () => {
  await fixture(async ({ document, update, requests, resolve, opens }) => {
    await update(false);
    assert.equal(requests.length, 0);
    await update(true);
    assert.equal(requests[0].url, '/api/projects/scoped/workshop/jobs');
    assert.equal(requests[0].options.headers.get('X-Easel-User'), 'viewer');
    assert.equal(requests[0].options.method ?? 'GET', 'GET');
    await resolve(0, [job('1', { status: 'running', progress: .35, elapsedMs: 2000, taskId: 'task-observed' }),
      job('2', { status: 'awaiting_review', artifactId: artifact, artifactSha256: 'b'.repeat(64) })]);
    const first = document.querySelector(`[data-workshop-job="${'1'.repeat(32)}"]`);
    const second = document.querySelector(`[data-workshop-job="${'2'.repeat(32)}"]`);
    assert.match(first.textContent, /运行中.*task-observed.*35%.*2 秒/s);
    assert.match(second.textContent, /待审核.*进度未知.*耗时未知/s);
    await React.act(() => second.querySelector('[data-open-workshop-job]').click());
    assert.deepEqual(opens, [[workshop, artifact]]);
    assert.equal(requests.length, 2);
    assert.equal(requests[1].url, `/api/projects/scoped/workshop/projects/${workshop}/renders/${'1'.repeat(32)}`);
  });
});

test('hidden/closed drawer aborts exact reads and cannot apply late or foreign snapshots', async () => {
  await fixture(async ({ document, update, requests, resolve, visibilityTo, timers }) => {
    await update(true);
    await visibilityTo('hidden');
    assert.equal(requests[0].options.signal.aborted, true);
    await resolve(0, [job('1')]);
    assert.equal(document.querySelector('[data-workshop-job]'), null);
    await visibilityTo('visible');
    assert.equal(requests.length, 2);
    await resolve(1, [job('2')], 'another-ip');
    assert.equal(document.querySelector('[data-workshop-job]'), null);
    assert.ok(document.querySelector('[data-workshop-jobs-error]'));
    await update(false);
    assert.equal(timers.size, 0);
  });
});


test('the next roster poll starts only after the previous scoped GET finishes', async () => {
  await fixture(async ({ document, update, requests, resolve, timers, visibilityTo }) => {
    await update(true);
    assert.deepEqual([...timers.values()].map(timer => timer.ms), [12000]);
    await resolve(0, [job('1')]);
    assert.deepEqual([...timers.values()].map(timer => timer.ms), [5000]);
    const [key, poll] = [...timers][0];
    timers.delete(key);
    await React.act(() => poll.callback());
    assert.equal(requests.length, 2);
    assert.equal(requests[1].url, requests[0].url);
    assert.deepEqual([...timers.values()].map(timer => timer.ms), [12000]);
    await resolve(1, [job('1', { status: 'running', progress: 0 })]);
    assert.equal(document.querySelector('[data-workshop-job-progress]').dataset.workshopJobProgress, '0');
    await visibilityTo('hidden');
    assert.equal(timers.size, 0);
  });
});


test('visible cycles observe only exact active jobs in bounded fair rotation, then reread the roster', async () => {
  await fixture(async ({ update, requests, resolve, timers }) => {
    const rows = ['1', '2', '3', '4', '5'].map(id => job(id, { status: 'running', taskId: 'task-' + id }));
    rows.push(job('6', { status: 'unconfirmed', taskId: 'unconfirmed' }), job('7', { status: 'completed', taskId: 'done' }));
    const exact = ids => ids.map(id => `/api/projects/scoped/workshop/projects/${workshop}/renders/${id.repeat(32)}`);
    const settleObservations = indexes => React.act(async () => {
      for (const index of indexes) requests[index].resolve(Response.json({ unrelated: 'ignored provider payload' }));
    });
    await update(true);
    await resolve(0, rows);
    assert.deepEqual(requests.slice(1).map(request => request.url), exact(['1', '2', '3']));
    await settleObservations([1, 2, 3]);
    assert.equal(requests[4].url, requests[0].url);
    await resolve(4, [...rows].reverse());
    const [key, poll] = [...timers][0];
    timers.delete(key);
    await React.act(() => poll.callback());
    await resolve(5, [...rows].reverse());
    assert.deepEqual(requests.slice(6).map(request => request.url), exact(['4', '5', '1']));
    await update(false);
    assert.ok(requests.slice(6).every(request => request.options.signal.aborted));
    assert.equal(timers.size, 0);
  });
});


test('only an operator can confirm cancellation, and the exact task stays pending until a server receipt', async () => {
  await fixture(async ({ document, update, requests, resolve, closes }) => {
    const row = job('1', { status: 'running', taskId: 'provider-task', canCancel: true });
    await update(true);
    await resolve(0, [row]);
    assert.equal(document.querySelector('[data-cancel-workshop-job]'), null);
    await update(true, true);
    const action = document.querySelector('[data-cancel-workshop-job]');
    assert.ok(action);
    await React.act(() => action.click());
    const confirm = document.querySelector('[data-workshop-cancel-confirmation]');
    assert.match(confirm.textContent, /scoped.*BeefTV.*provider-task/s);
    assert.equal(requests.filter(request => request.options.method === 'POST').length, 0);
    await React.act(() => confirm.querySelector('[data-confirm-workshop-cancel]').click());
    const submitted = requests.filter(request => request.options.method === 'POST');
    assert.equal(submitted.length, 1);
    assert.equal(submitted[0].url, `/api/projects/scoped/workshop/projects/${workshop}/renders/${row.id}/cancel`);
    assert.deepEqual(JSON.parse(submitted[0].options.body), { taskId: 'provider-task' });
    assert.equal(confirm.querySelector('[data-confirm-workshop-cancel]').hasAttribute('disabled'), true);
    assert.equal(document.querySelector('[aria-label="关闭工坊任务"]').hasAttribute('disabled'), true);
    const view = document.defaultView;
    await React.act(() => document.querySelector('.workshop-jobs-overlay')
      .dispatchEvent(new view.Event('mousedown', { bubbles: true })));
    assert.deepEqual(closes, []);
    assert.equal(document.querySelector('[data-workshop-job-status]').dataset.workshopJobStatus, 'running');
    await React.act(async () => submitted[0].resolve(Response.json({ status: 'pending', taskId: row.taskId, renderId: row.id,
      jobStatus: 'running', requestedAt: 100, updatedAt: 100 })));
    assert.equal(document.querySelector('[data-workshop-job-status]').dataset.workshopJobStatus, 'stopping');
    assert.equal(document.querySelector('[data-workshop-cancel-confirmation]'), null);
    assert.equal(requests.filter(request => request.options.method === 'POST').length, 1);
  });
});

test('an ambiguous cancellation transport leaves the task unconfirmed and offers no blind retry', async () => {
  await fixture(async ({ document, update, requests, resolve }) => {
    const row = job('1', { status: 'queued', taskId: 'provider-task', canCancel: true });
    await update(true, true);
    await resolve(0, [row]);
    await React.act(() => document.querySelector('[data-cancel-workshop-job]').click());
    await React.act(() => document.querySelector('[data-confirm-workshop-cancel]').click());
    const request = requests.find(value => value.options.method === 'POST');
    await React.act(async () => request.resolve(Response.json({}, { status: 502 })));
    assert.match(document.querySelector('[data-workshop-cancel-result]').textContent, /未确认/);
    assert.equal(document.querySelector('[data-workshop-job-status=cancelled]'), null);
    assert.equal(document.querySelector('[data-cancel-workshop-job]'), null);
    assert.equal(requests.filter(value => value.options.method === 'POST').length, 1);
  });
});


test('durable unconfirmed state offers explicit lookup and confirmed cancellation survives drawer reopen', async () => {
  await fixture(async ({ document, update, requests, resolve }) => {
    const active = job('1', { status: 'running', taskId: 'provider-task', canCancel: true });
    const cancellation = { status: 'unconfirmed', taskId: active.taskId, renderId: active.id,
      jobStatus: 'running', requestedAt: 100, updatedAt: 200 };
    await update(true, true);
    await resolve(0, [{ ...active, cancellation }]);
    assert.equal(document.querySelector('[data-workshop-job-status]').dataset.workshopJobStatus, 'unconfirmed');
    const verify = document.querySelector('[data-cancel-workshop-job=verify]');
    assert.equal(verify.textContent, '核对取消状态');
    await React.act(() => verify.click());
    assert.equal(requests.filter(request => request.options.method === 'POST').length, 0);
    await React.act(() => document.querySelector('[data-confirm-workshop-cancel]').click());
    const post = requests.find(request => request.options.method === 'POST');
    const completed = { ...cancellation, status: 'cancelled', jobStatus: 'cancelled', updatedAt: 300 };
    await React.act(async () => post.resolve(Response.json(completed)));
    assert.equal(document.querySelector('[data-workshop-job-status]').dataset.workshopJobStatus, 'cancelled');
    await update(false, true);
    await update(true, true);
    await resolve(requests.length - 1, [{ ...active, status: 'cancelled', canCancel: false, cancellation: completed }]);
    assert.equal(document.querySelector('[data-workshop-job-status]').dataset.workshopJobStatus, 'cancelled');
    assert.equal(document.querySelector('[data-cancel-workshop-job]'), null);
    assert.equal(requests.filter(request => request.options.method === 'POST').length, 1);
  });
});
