import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const file = () => ({ artifactId: 'a'.repeat(32), projectId: 'default', sessionId: 'chat-one', turnId: 'turn-one',
  path: 'cat/final.mp4', name: 'final.mp4', mime: 'video/mp4', size: 42,
  description: '猫咪今天也在认真上班', status: 'available', createdAt: Date.now(),
  url: `/api/chat/artifacts/${'a'.repeat(32)}/content`, storageMode: 'snapshot', revision: 2,
  sha256: 'b'.repeat(64), bundleSha256: null });
function load(user = 'operator-one', project = 'default', storageValues) {
  const events = [];
  const globals = { window: { location: { pathname: '/', origin: 'http://127.0.0.1:7860' },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    dispatchEvent: event => { events.push(event); return true; } } };
  return { helper: componentModule('lib/publicationHandoff.ts', { user, project, globals, storageValues }), events,
    store: () => componentModule('lib/store.ts', { user, project, globals, storageValues }) };
}

test('snapshot handoff carries current actor/IP and exact media version with explicit copy/platform', () => {
  const { helper, events } = load();
  assert.equal(typeof helper.requestDeliverablePublication, 'function', 'missing deliverable publication handoff');
  assert.equal(helper.requestDeliverablePublication(file(), { title: '猫咪打工日记', body: '先伸个懒腰，再继续努力。', tags: '猫咪,AI创作', platform: 'douyin' }), true);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'easel:publish-handoff');
  const handoff = events[0].detail;
  assert.equal(handoff.kind, 'media'); assert.equal(handoff.actorId, 'operator-one'); assert.equal(handoff.projectId, 'default');
  assert.equal(handoff.copy.platform, 'douyin'); assert.equal(handoff.copy.body, '先伸个懒腰，再继续努力。');
  assert.equal(handoff.media[0].artifactId, file().artifactId); assert.equal(handoff.media[0].revision, 2);
  assert.equal(handoff.media[0].sha256, file().sha256);
});

test('publish titles use explicit copy or the display filename rather than technical delivery descriptions', () => {
  const technical = { ...file(), name: 'render-20261104.mp4',
    description: '成片待确认｜720×1280｜12秒｜画面与声音已核验｜未发布',
    displayName: '猫咪打工日记.mp4' };
  const { helper, events } = load();
  assert.equal(helper.requestDeliverablePublication(technical), true);
  assert.equal(events[0].detail.copy.title, '猫咪打工日记');
  assert.equal(helper.requestDeliverablePublication(technical, { title: '已确认的出版标题', body: '原文案' }), true);
  assert.equal(events[1].detail.copy.title, '已确认的出版标题');
  assert.equal(events[1].detail.copy.body, '原文案');
  assert.equal(helper.requestDeliverablePublication({ ...technical, displayName: '  ' }), true);
  assert.equal(events[2].detail.copy.title, 'render-20261104');
  assert.equal(helper.requestDeliverablePublication({ ...technical, displayName: 'Mr. Meow' }), true);
  assert.equal(events[3].detail.copy.title, 'Mr. Meow');
  assert.deepEqual(events[0].detail.media[0].sha256, technical.sha256);
});

test('legacy paths, unavailable files, foreign IP and signed-out cards never emit a publish handoff', () => {
  const { helper, events } = load();
  assert.equal(typeof helper.requestDeliverablePublication, 'function');
  for (const invalid of [{ ...file(), storageMode: 'legacy-current', revision: null, sha256: null },
    { ...file(), status: 'unavailable' }, { ...file(), projectId: 'other-ip' },
    { ...file(), path: '../private.mp4' }, { ...file(), mime: 'text/plain' }]) {
    assert.equal(helper.requestDeliverablePublication(invalid), false);
  }
  assert.equal(load('').helper.requestDeliverablePublication(file()), false);
  assert.equal(events.length, 0);
});

test('persisted media references stay scoped and require the same server snapshot before preparation', () => {
  const values = new Map();
  const { helper, events, store } = load('operator-one', 'default', values);
  assert.equal(typeof helper.requestDeliverablePublication, 'function');
  helper.requestDeliverablePublication(file());
  const reference = events[0].detail.media[0];
  store().savePublishDraft({ title: '猫咪', body: '', platforms: ['douyin'], overrides: {}, tags: '', mediaReferences: [reference] });
  const restored = store().loadPublishDraft();
  assert.equal(restored.mediaReferences.length, 1);
  assert.equal(helper.publicationMediaReferenceMatches(reference, file()), true);
  assert.equal(helper.publicationMediaReferenceMatches(reference, { ...file(), sha256: 'c'.repeat(64) }), false);
  assert.equal(helper.publicationMediaReferenceMatches(reference, { ...file(), revision: 3 }), false);
  assert.equal(helper.publicationMediaReferenceMatches(reference, { ...file(), status: 'unavailable' }), false);
  assert.equal(load('operator-two').helper.validPublicationMediaReference(reference), false);
  assert.equal(load('operator-one', 'other-ip').helper.validPublicationMediaReference(reference), false);
  assert.equal(helper.validPublicationMediaReference({ ...reference, expiresAt: Date.now() - 1 }), false);
  assert.equal(load('operator-two', 'default', values).store().loadPublishDraft().mediaReferences.length, 0);
});

test('an existing approval handoff preserves only the exact scoped approval ID/hash and expires as navigation', () => {
  const { helper, events } = load();
  assert.equal(typeof helper.requestApprovalPublication, 'function');
  const approval = { id: 'same-approval', payloadHash: 'd'.repeat(64), actorId: 'operator-one', projectId: 'default',
    subject: { project: { id: 'default' } } };
  assert.equal(helper.requestApprovalPublication(approval), true);
  const handoff = events[0].detail;
  assert.equal(handoff.kind, 'approval'); assert.equal(handoff.approvalId, 'same-approval');
  assert.equal(handoff.payloadHash, approval.payloadHash);
  assert.ok(helper.normalizePublicationHandoff(handoff));
  assert.equal(helper.normalizePublicationHandoff({ ...handoff, expiresAt: Date.now() - 1 }), null);
  assert.equal(helper.normalizePublicationHandoff({ ...handoff, actorId: 'operator-two' }), null);
  assert.equal(helper.requestApprovalPublication({ ...approval, projectId: 'other-ip' }), false);
  assert.equal(events.length, 1);
});
