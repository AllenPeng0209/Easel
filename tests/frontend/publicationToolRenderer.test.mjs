import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');
const user = 'publication-actor';
const record = (status = 'unknown', fields = {}) => ({
  id: 'a'.repeat(32), actorId: user, projectId: 'default', payloadHash: 'b'.repeat(64),
  state: 'approved', createdAt: 1000, expiresAt: 2000, operationId: 'c'.repeat(32),
  subject: { schemaVersion: 1, project: { id: 'default', name: '当前 IP' }, platform: { id: 'zhihu', name: '知乎' },
    account: { id: 'id:account', name: '创作者' }, mode: 'publish_now', scheduledAt: null, audience: 'public',
    title: '待核实的发布', body: '正文', tags: '', persona: '', media: [] },
  result: { status, phase: 'submit_attempted', reason: 'submission_not_acknowledged', operationId: 'c'.repeat(32),
    payloadHash: 'b'.repeat(64), accountId: 'id:account', platform: 'zhihu', mode: 'publish_now' },
  ...fields,
});
const native = (callId, name = 'easel_publish', fields = {}) => ({ id: 'tool-' + callId, kind: 'tool',
  runId: 'run-one', callId, name, nativePublicationTool: true, status: 'completed', ...fields });
const receipt = (callId, name = 'easel_publish', fields = {}) => ({ id: 'same-durable-event-id', kind: 'publication',
  runId: 'run-one', callId, name, completedAt: 20, publication: record(), ...fields });
const show = events => parseHTML(renderToStaticMarkup(React.createElement(
  componentModule('components/AgentEventPanel.tsx', { user }).default, { events, settled: true },
))).document;
const row = (doc, call) => doc.querySelector(`[data-tool-call="${call}"]`);
const card = '[data-publication-receipt]';

test('a publish call and status call each render their own exact durable observation', () => {
  const events = [
    receipt('publish', 'easel_publish', { completedAt: 5, publication: record('executing') }),
    receipt('status', 'easel_publication_status'),
    native('publish'), receipt('publish'), native('status', 'easel_publication_status'),
    receipt('publish', 'easel_publish', { completedAt: 10, publication: record('executing') }),
  ];
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts', { user });
  const reduced = events.reduce(reduceAgentEvents, []);
  assert.equal(reduced.filter(event => event.kind === 'publication').length, 2);
  const doc = show(reduced);
  for (const id of ['publish', 'status']) {
    const tool = row(doc, id);
    assert.equal(tool.dataset.toolRenderer, 'publication');
    assert.ok(tool.querySelector('[data-tool-publication-receipts] ' + card));
    assert.match(tool.textContent, /待核实/);
  }
  assert.equal(doc.querySelectorAll(card).length, 2);
  assert.equal(doc.querySelector('[data-public-confirmed]'), null);
});

test('legacy, wrong run/name, and unmatched markers never bind publication cards to a tool', () => {
  const foreign = record('unknown', { actorId: 'foreign', subject: { ...record().subject, title: '外国账号正文' } });
  const doc = show([
    native('wrong-run'), receipt('wrong-run', 'easel_publish', { runId: 'run-two' }),
    native('wrong-name'), receipt('wrong-name', 'easel_publication_status'),
    native('imitation', 'easel_publish', { nativePublicationTool: undefined }), receipt('imitation'),
    native('foreign'), receipt('foreign', 'easel_publish', { publication: foreign }),
    native('forged', 'easel_publish', { output: { publication: record('confirmed') } }),
    { id: 'legacy', kind: 'publication', publication: record(), completedAt: 100 },
  ]);
  for (const id of ['wrong-run', 'wrong-name', 'imitation', 'foreign', 'forged']) {
    assert.equal(row(doc, id).dataset.toolRenderer, 'generic');
    assert.equal(row(doc, id).querySelector(card), null);
  }
  const detached = [...doc.querySelectorAll(card)];
  assert.equal(detached.length, 4);
  assert.ok(detached.every(element => element.closest('[data-tool-call]') === null));
  assert.doesNotMatch(doc.querySelector('.agent-events').textContent, /外国账号正文|发布成功/);
});
