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

test('a real publication-center decision retains its Host provenance without claiming a chat human answer or default', () => {
  const { observeAgentTimeline } = componentModule('lib/agentTimeline.ts');
  const observations = observeAgentTimeline([], { id: 'publication-decision', kind: 'decision', questionId: 'publication-question',
    status: 'resolved', resolvedBy: 'publication_host' });
  assert.equal(observations[0].resolvedBy, 'publication_host');
  const document = render({ role: 'assistant', content: '', timeline: observations });
  const text = document.querySelector('[data-agent-timeline]').textContent;
  assert.match(text, /发布中心的决定已同步/);
  assert.doesNotMatch(text, /用户答复已记录|默认答案已记录/);
});

test('saved compact observations render by recorded order with distinct tool count and collapsed failure', () => {
  const document = render({ role: 'assistant', content: '', timeline, activity: '多行\n旧活动' });
  const details = document.querySelector('[data-agent-timeline]');
  assert.ok(details);
  assert.equal(details.hasAttribute('open'), false);
  assert.match(details.querySelector('summary').textContent, /1 次工具调用.*1 个失败/s);
  const rows = [...details.querySelectorAll('[data-timeline-observation]')];
  assert.deepEqual(rows.map(row => row.dataset.observationKind), ['tool', 'decision']);
  assert.equal(rows[0].dataset.observationStatus, 'failed');
  assert.ok(rows[0].querySelector('[data-locate-failed-tool]'));
  assert.match(rows[1].textContent, /用户答复/);
  assert.equal(rows[1].querySelector('time'), null);
  const time = rows[0].querySelector('time');
  assert.equal(time.getAttribute('datetime') ?? time.getAttribute('dateTime'), '2023-11-14T22:13:20.000Z');
  assert.doesNotMatch(details.textContent, /多行|旧活动/);
});

test('a settled one-call turn shows its tool detail and latest usage without a duplicate timeline or answer badge', () => {
  const preparing = tool('start', 'preparing', 1700000000000);
  const completed = tool('end', 'completed', 1700000001000);
  const document = render({
    role: 'assistant', content: '', status: 'completed',
    answerCompletion: { itemId: 'answer' },
    timeline: [preparing, completed],
    events: [preparing, completed,
      { id: 'usage-1', runId: 'run', turnId: 'turn', kind: 'usage', output: { tokens: 1234 } },
      { id: 'usage-2', runId: 'run', turnId: 'turn', kind: 'usage', output: { tokens: 5678 } }],
  });
  assert.equal(document.querySelector('[data-agent-timeline]'), null);
  assert.equal(document.querySelectorAll('[data-tool-call="single-call"]').length, 1);
  assert.equal(document.querySelector('[data-answer-phase]'), null);
  assert.ok(document.querySelector('[data-turn-phase=completed]'));
  const details = document.querySelector('[aria-label="任务执行记录"]');
  assert.equal([...details.querySelectorAll('summary')].filter(row => row.textContent === '本轮用量').length, 1);
  assert.match(details.textContent, /5678/);
  assert.doesNotMatch(details.textContent, /1234/);
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
