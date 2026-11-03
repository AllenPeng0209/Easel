import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { parseHTML } = require('linkedom');

const tool = (id, status, observedAt) => ({ id, runId: 'run', kind: 'tool', callId: 'single-call', name: 'draft',
  status, observedAt });
const timeline = [
  tool('start', 'preparing', 1700000000000),
  { id: 'answer', runId: 'run', kind: 'decision', questionId: 'question', status: 'resolved', resolvedBy: 'human' },
  tool('end', 'failed', 1700000001000),
];
const render = (message, props = {}) => parseHTML(renderToStaticMarkup(React.createElement(
  componentModule('components/MessageBubble.tsx').default, { message, ...props },
))).document;

test('saved compact observations render by recorded order with distinct tool count and collapsed failure', () => {
  const document = render({ role: 'assistant', content: '', timeline, activity: '多行\n旧活动' });
  const details = document.querySelector('[data-agent-timeline]');
  assert.ok(details);
  assert.equal(details.hasAttribute('open'), false);
  assert.match(details.querySelector('summary').textContent, /1 次工具调用.*1 个失败/s);
  const rows = [...details.querySelectorAll('[data-timeline-observation]')];
  assert.deepEqual(rows.map(row => row.dataset.observationKind), ['tool', 'decision', 'tool']);
  assert.match(rows[1].textContent, /用户答复/);
  assert.equal(rows[1].querySelector('time'), null);
  const time = rows[0].querySelector('time');
  assert.equal(time.getAttribute('datetime') ?? time.getAttribute('dateTime'), '2023-11-14T22:13:20.000Z');
  assert.doesNotMatch(details.textContent, /多行|旧活动/);
});

test('live waiting is shown even when there are no fabricated pending-order records', () => {
  const document = render({ role: 'assistant', content: '' }, {
    isStreaming: true, timeline: [tool('start', 'running')], pendingTimelineQuestions: 1,
  });
  const details = document.querySelector('[data-agent-timeline]');
  assert.match(details.querySelector('summary').textContent, /1 项待答复/);
  assert.equal(details.querySelectorAll('[data-timeline-observation]').length, 1);
  assert.equal(details.querySelector('[data-observation-kind=decision]'), null);
});

test('legacy text remains visible without treating newline count as execution steps', () => {
  const document = render({ role: 'assistant', content: '', activity: '旧活动 A\n旧活动 B' });
  assert.equal(document.querySelector('[data-agent-timeline]'), null);
  assert.match(document.querySelector('.thinking-block').textContent, /旧活动 A/);
  assert.doesNotMatch(document.querySelector('.thinking-block').textContent, /2 步/);
});


test('bounded history labels retained observations and skips malformed rows', () => {
  const document = render({ role: 'assistant', content: '', timeline: [
    { ...tool('last', 'completed'), earlierObservationsOmitted: true },
    { id: 'unknown', kind: 'constructor', name: 'malformed imported payload' },
  ] });
  const details = document.querySelector('[data-agent-timeline]');
  assert.match(details.querySelector('summary').textContent, /保留记录中 1 次工具调用/);
  assert.ok(details.querySelector('[data-timeline-omitted]'));
  assert.equal(details.querySelectorAll('[data-timeline-observation]').length, 1);
  assert.doesNotMatch(details.textContent, /malformed imported payload/);
});


test('refreshing a mounted timeline keeps the user disclosure and earlier row identities', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Timeline = componentModule('components/AgentTimeline.tsx').default;
    const update = observations => React.act(() => root.render(React.createElement(Timeline, { observations })));
    await update(timeline.slice(0, 2));
    const details = document.querySelector('[data-agent-timeline]');
    const first = details.querySelector('[data-timeline-observation]');
    details.setAttribute('open', '');
    await update(timeline);
    assert.equal(document.querySelector('[data-agent-timeline]'), details);
    assert.equal(details.hasAttribute('open'), true);
    assert.equal(details.querySelector('[data-timeline-observation]'), first);
    assert.match(details.querySelector('summary').textContent, /1 个失败/);
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
