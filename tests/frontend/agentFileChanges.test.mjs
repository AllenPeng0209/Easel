import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const { normalizeAgentEvent, latestAgentFileChanges } = componentModule('lib/agentEvents.ts');
const plain = value => JSON.parse(JSON.stringify(value));
const file = { path: 'story/script.md', kind: 'modified', diffStatus: 'text', added: 2, deleted: 1 };
const report = { files: [file], coverage: 'complete', warnings: ['shared_workspace'], total: 1 };
const event = { id: 'change-id', kind: 'changes', source: 'outputs_snapshot', status: 'completed', changes: report };

test('a round observation validates and carries only the structured net change evidence', () => {
  const raw = { ...event, changes: { ...report, files: [{ ...file, diff: 'private bytes', url: 'https://fake.invalid' }] } };
  assert.deepEqual(plain(normalizeAgentEvent(raw)), event);
  assert.deepEqual(plain(latestAgentFileChanges([raw])), { status: 'completed', changes: report });
});

test('rejects unsafe paths, fabricated counts and unscoped reports', () => {
  for (const path of ['/tmp/private', '../outside', 'drafts/../../escaped', 'drafts/%2e%2e/escaped', 'foo\\bar', '_sessions/private', '.env']) {
    assert.equal(normalizeAgentEvent({ ...event, changes: { ...report, files: [{ ...file, path }] } }), null);
  }
  assert.equal(normalizeAgentEvent({ ...event, source: 'tool-text' }), null);
  assert.equal(normalizeAgentEvent({ ...event, changes: { ...report, total: 2 } }), null);
  assert.equal(normalizeAgentEvent({ ...event, changes: { ...report, files: [{ ...file, deleted: -1 }] } }), null);
  const binary = { path: 'cover.png', kind: 'added', diffStatus: 'binary' };
  assert.deepEqual(plain(normalizeAgentEvent({ ...event, changes: { ...report, files: [{ ...binary, added: 123, deleted: 0 }] } }).changes.files), [binary]);
});

test('complete empty observations are omitted and incomplete observations retain the gap', () => {
  const empty = { files: [], total: 0, warnings: ['shared_workspace'], coverage: 'complete' };
  assert.equal(latestAgentFileChanges([{ ...event, changes: empty }]), null);
  const partial = { ...empty, coverage: 'partial', warnings: ['shared_workspace', 'scan_limit'] };
  assert.deepEqual(plain(latestAgentFileChanges([{ ...event, status: 'stopped', changes: partial }])),
    { status: 'stopped', changes: partial });
});
