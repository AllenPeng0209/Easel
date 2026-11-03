import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');
const actor = 'workshop-owner';
const tool = (id, fields = {}) => ({ id: `tool-${id}`, runId: 'run-one', callId: id, kind: 'tool',
  name: 'easel_business_objects', nativeBusinessTool: true, status: 'completed', ...fields });
const business = (id, callId, fields = {}, record = {}) => ({ id: `event-${id}`, runId: 'run-one', callId,
  kind: 'business', name: 'easel_business_objects', ...fields, business: {
    schemaVersion: 1, actorId: actor, projectId: 'default', kind: 'workshop', id: id.repeat(32),
    title: `工坊 ${id}`, summary: '已保存工程', status: { code: 'draft', label: '草稿', source: 'stored' },
    version: { revision: 2, sha256: null }, snapshotHash: '9'.repeat(64), observedAt: 100,
    details: { project: { id: id.repeat(32), revision: 2, stage: 'draft' } },
    actions: [{ kind: 'open', target: 'workshop', entityId: id.repeat(32), label: '打开工坊' }], ...record,
  },
});
const show = events => parseHTML(renderToStaticMarkup(React.createElement(
  componentModule('components/AgentEventPanel.tsx', { user: actor }).default, { events, settled: true },
))).document;
const card = (document, id) => document.querySelector(`[data-business-object="${id.repeat(32)}"]`);
const row = (document, id) => document.querySelector(`[data-tool-call="${id}"]`);

test('typed workshop snapshots join the exact native call once, independently of delivery order', () => {
  const old = business('a', 'call-a', {}, { title: '旧观测' });
  const updated = business('a', 'call-a', { id: 'new-observation' }, { title: '新观测', observedAt: 200 });
  const document = show([old, tool('call-b'), updated, business('b', 'call-b'), tool('call-a')]);
  assert.equal(row(document, 'call-a').dataset.toolRenderer, 'workshop');
  assert.equal(row(document, 'call-b').dataset.toolRenderer, 'workshop');
  assert.equal(card(document, 'a').closest('[data-tool-call]'), row(document, 'call-a'));
  assert.equal(card(document, 'b').closest('[data-tool-call]'), row(document, 'call-b'));
  assert.equal(document.querySelectorAll('[data-business-object]').length, 2);
  assert.match(card(document, 'a').textContent, /新观测/);
  assert.doesNotMatch(document.querySelector('.agent-events').textContent, /旧观测/);
});

test('run/call mismatches, legacy markers and foreign records never enter a specialized workshop', () => {
  const record = business('f', 'foreign', {}, { actorId: 'someone-else', title: '不可见的外国资料' });
  const forged = business('e', 'forged').business;
  const document = show([
    tool('call-a'), business('a', 'call-a', { runId: 'run-two' }),
    tool('legacy', { runId: undefined }), business('b', 'legacy', { runId: undefined }),
    tool('imitation', { nativeBusinessTool: undefined }), business('c', 'imitation'),
    tool('foreign'), record,
    tool('forged', { output: { items: [forged] } }),
    business('d', 'host:workshop:turn'),
  ]);
  for (const id of ['call-a', 'legacy', 'imitation', 'foreign', 'forged']) assert.equal(row(document, id).dataset.toolRenderer, 'generic');
  for (const id of ['a', 'b', 'c', 'd']) assert.equal(card(document, id).closest('[data-tool-call]'), null);
  assert.equal(card(document, 'f'), null);
  assert.equal(card(document, 'e'), null);
  assert.doesNotMatch(document.querySelector('.agent-events').textContent, /不可见的外国资料/);
});

test('a provider reusing the same call ID in two runs keeps its workshop evidence isolated', () => {
  const document = show([
    tool('shared'), business('a', 'shared'),
    tool('shared', { runId: 'run-two' }), business('b', 'shared', { runId: 'run-two' }),
  ]);
  const first = card(document, 'a').closest('[data-tool-call]');
  const second = card(document, 'b').closest('[data-tool-call]');
  assert.equal(first.dataset.runId, 'run-one');
  assert.equal(second.dataset.runId, 'run-two');
  assert.notEqual(first.dataset.chatAnchorKey, second.dataset.chatAnchorKey);
});

test('a settled chat shows the exact current workshop render state without inventing collection or publication', () => {
  const id = 'a'.repeat(32);
  const current = { id: 'b'.repeat(32), operation_id: 'native-operation', task_id: 'native-task',
    content_revision: 2, engine_revision: 3, status: 'running', submission_state: 'confirmed',
    progress: 0.35, created_at: '2026-11-03T02:58:00Z', updated_at: '2026-11-03T03:00:00Z' };
  const details = { project: { id, revision: 2, stage: 'draft', renders: [
    { ...current, id: 'c'.repeat(32), content_revision: 1 }, current,
  ] } };
  const document = show([tool('call-a'), business('a', 'call-a', {}, { details })]);
  const state = row(document, 'call-a').querySelector('.agent-tool__status [data-render-observation]');
  assert.equal(state?.dataset.renderObservation, current.id);
  assert.equal(state?.dataset.renderState, 'running');
  assert.match(state.textContent, /渲染进行中.*35%/);
  assert.doesNotMatch(state.textContent, /收取|发布/);

  const standalone = show([business('a', 'unmatched', {}, { details })]);
  assert.equal(card(standalone, 'a').querySelector('[data-render-observation]'), null);
});
