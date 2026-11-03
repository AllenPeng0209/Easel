import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';

const projectId = 'a'.repeat(32);
const renderId = 'b'.repeat(32);
const receipt = (extra = {}) => ({
  id: renderId, operation_id: 'original-operation', task_id: 'original-task',
  content_revision: 2, engine_revision: 3, status: 'running', submission_state: 'confirmed',
  progress: 0.35, created_at: '2026-11-03T02:58:00Z', updated_at: '2026-11-03T03:00:00Z', ...extra,
});
const load = globals => componentModule('lib/workshop.ts', { user: 'owner', project: 'workshop-ip', globals });

test('binds the latest recorded render to the exact current project revision', () => {
  const { selectChatWorkshopRender } = load();
  const project = { id: projectId, revision: 2, renders: [
    receipt({ content_revision: 1 }), receipt(), receipt({ content_revision: 3 }),
  ] };
  assert.equal(selectChatWorkshopRender(project, projectId, 2)?.id, renderId);
  assert.equal(selectChatWorkshopRender(project, 'c'.repeat(32), 2), null);
  assert.equal(selectChatWorkshopRender({ ...project, archived_at: '2026-11-03' }, projectId, 2), null);
});

test('only observes confirmed active receipts and distinguishes rendered from collected', () => {
  const { chatWorkshopRenderState } = load();
  assert.deepEqual(JSON.parse(JSON.stringify(chatWorkshopRenderState(receipt()))),
    { code: 'running', label: '渲染进行中', active: true });
  assert.equal(chatWorkshopRenderState(receipt({ status: 'failed', submission_state: 'unconfirmed' })).code, 'unconfirmed');
  assert.equal(chatWorkshopRenderState(receipt({ status: 'succeeded' })).code, 'uncollected');
  assert.equal(chatWorkshopRenderState(receipt({ status: 'succeeded', artifact_id: 'c'.repeat(32) })).code, 'collected');
});

test('reads one authorized receipt and rejects wrong identity or stale replacement', async () => {
  const requests = [];
  let response = receipt({ status: 'succeeded', updated_at: '2026-11-03T03:00:01Z' });
  const { observeChatWorkshopRender } = load({ Headers, Response, fetch: async (url, options) => {
    requests.push([url, options]);
    return new Response(JSON.stringify(response));
  } });
  const signal = new AbortController().signal;
  assert.equal((await observeChatWorkshopRender(projectId, receipt(), signal)).status, 'succeeded');
  const [[url, options]] = requests;
  assert.equal(url, `/api/projects/workshop-ip/workshop/projects/${projectId}/renders/${renderId}`);
  assert.equal(options.headers.get('X-Easel-User'), 'owner');
  assert.equal(options.signal, signal);
  assert.equal(options.method, undefined);
  response = { ...response, task_id: 'wrong-task' };
  await assert.rejects(observeChatWorkshopRender(projectId, receipt(), signal));
  response = receipt({ updated_at: '2026-11-03T02:59:00Z', progress: 0 });
  assert.equal((await observeChatWorkshopRender(projectId, receipt(), signal)).progress, 0.35);
});


test('mounted observation aborts when hidden and does not poll a terminal receipt', async () => {
  const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
  const React = require('react');
  const { parseHTML } = require('linkedom');
  const { window } = parseHTML('<html><body><div id="root"></div></body></html>');
  const { document } = window;
  window.location = { pathname: "/", origin: "http://127.0.0.1:7860" };
  let visibility = 'visible';
  Object.defineProperty(document, 'visibilityState', { get: () => visibility });
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true, writable: true },
    document: { value: document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true },
  });
  const requests = [];
  const timers = [];
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Observation = componentModule('components/WorkshopRenderObservation.tsx', {
      user: 'owner', project: 'workshop-ip',
      globals: { window, document, Headers, Response, setTimeout: callback => timers.push(callback),
        fetch: async (url, { signal }) => {
          requests.push({ url, signal });
          if (requests.length === 1) return new Promise((_resolve, reject) => {
            signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
          });
          return new Response(JSON.stringify(receipt({ status: 'succeeded', progress: 1, updated_at: '2026-11-03T03:00:01Z' })));
        },
      },
    }).default;
    await React.act(async () => root.render(React.createElement(Observation, { projectId, initial: receipt() })));
    assert.equal(requests.length, 1);
    assert.equal(document.querySelector('[data-render-state]').dataset.renderState, 'running');
    await React.act(async () => {
      visibility = 'hidden';
      document.dispatchEvent(new window.Event('visibilitychange'));
    });
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(requests.length, 1);
    await React.act(async () => {
      visibility = 'visible';
      document.dispatchEvent(new window.Event('visibilitychange'));
    });
    assert.equal(requests.length, 2);
    assert.equal(document.querySelector('[data-render-state]').dataset.renderState, 'uncollected');
    assert.equal(timers.length, 0);
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  }
});

test('only transient transport failures are retryable', async () => {
  let responseStatus = 503;
  const { observeChatWorkshopRender, canRetryChatWorkshopObservation } = load({
    Headers, Response, fetch: async () => new Response(null, { status: responseStatus }),
  });
  const signal = new AbortController().signal;
  await assert.rejects(observeChatWorkshopRender(projectId, receipt(), signal), canRetryChatWorkshopObservation);
  responseStatus = 403;
  await assert.rejects(observeChatWorkshopRender(projectId, receipt(), signal),
    error => !canRetryChatWorkshopObservation(error));
});

test('only confirmed provider progress is displayed, including a real zero', () => {
  const { workshopProgress } = load();
  assert.equal(workshopProgress(receipt({ progress: 0, progress_known: true })), 0);
  assert.equal(workshopProgress(receipt({ progress: 0.35, progress_known: true })), 0.35);
  assert.equal(workshopProgress(receipt({ progress: 0.35 })), null);
  assert.equal(workshopProgress(receipt({ progress: 2, progress_known: true })), null);
});

test('workshop chat only shows cancellation receipts bound to its exact render and task', () => {
  const { workshopCancellationLabel } = load();
  const cancellation = { status: 'pending', taskId: 'original-task', renderId, jobStatus: 'running' };
  assert.equal(workshopCancellationLabel(receipt({ cancellation })), '正在停止');
  assert.equal(workshopCancellationLabel(receipt({ cancellation: { ...cancellation, taskId: 'different-task' } })), null);
  assert.equal(workshopCancellationLabel(receipt({ cancellation: { ...cancellation, status: 'unconfirmed' } })), '停止结果待核对');
  assert.equal(workshopCancellationLabel(receipt({ cancellation: { ...cancellation, status: 'cancelled', jobStatus: 'cancelled' } })), null);
  assert.equal(workshopCancellationLabel(receipt({ status: 'cancelled', cancellation: {
    ...cancellation, status: 'cancelled', jobStatus: 'cancelled',
  } })), '已确认停止');
});
