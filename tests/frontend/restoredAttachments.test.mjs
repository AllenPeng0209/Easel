import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const restored = (key, name = key + '.txt') => ({ key, name, size: 1, mime: 'text/plain', status: 'ready',
  reference: { id: key, name, path: '_inbox/scope/batch/' + name } });

test('restored references are checked in bounded scoped batches, preserving result positions', async () => {
  const calls = [];
  const { validateRestoredAttachments } = componentModule('lib/restoredAttachments.ts', {
    user: 'owner', project: 'other-ip', globals: { Headers, fetch: async (url, options) => {
      const body = JSON.parse(options.body);
      calls.push({ url, body, options });
      return Response.json({ valid: body.attachments.map(ref => ref.id !== 'gone') });
    } },
  });
  const files = [restored('gone'), ...Array.from({ length: 100 }, (_, index) => restored(String(index)))];
  const signal = new AbortController().signal;
  const invalid = await validateRestoredAttachments('scope-id', files, signal);
  assert.deepEqual(Array.from(invalid, item => item.key), ['gone']);
  assert.deepEqual(calls.map(call => call.body.attachments.length), [100, 1]);
  assert.ok(calls.every(call => call.url === '/api/projects/other-ip/upload/validate'
    && call.body.sessionId === 'scope-id' && call.options.signal === signal
    && call.options.headers.get('X-Easel-User') === 'owner'));
});

test('a malformed or failed verification remains unconfirmed', async () => {
  for (const response of [Response.json({ valid: ['false'] }), Response.json({ valid: [] }), new Response('', { status: 503 })]) {
    const { validateRestoredAttachments } = componentModule('lib/restoredAttachments.ts', {
      globals: { fetch: async () => response },
    });
    await assert.rejects(validateRestoredAttachments('scope', [restored('one')], new AbortController().signal));
  }
});

test('only the exact restored reference is invalidated and removed from send-ready files', () => {
  const { AttachmentUploadQueue } = componentModule('lib/uploadQueue.ts');
  const first = restored('one');
  const second = restored('two');
  const queue = new AttachmentUploadQueue(() => assert.fail('No upload should run'), () => {}, [first, second]);
  queue.invalidateRestored([{ ...first, reference: { ...first.reference, path: 'old' } }]);
  assert.equal(queue.ready.length, 2);
  queue.invalidateRestored([first]);
  assert.deepEqual(Array.from(queue.ready, item => item.name), ['two.txt']);
  assert.equal(queue.snapshot[0].status, 'failed');
  assert.match(queue.snapshot[0].error, /重新选择/);
});

test('draft text and saved references isolate identical session IDs across accounts and IPs', () => {
  const storageValues = new Map();
  const draft = (user, project) => componentModule('lib/chatDrafts.ts', { user, project, storageValues });
  const ownerDefault = draft('owner', 'default');
  const ownerOtherIp = draft('owner', 'ip-two');
  const otherUser = draft('operator', 'default');
  const data = (text, ref) => ({ text, attachments: [restored(ref)], interruptedFiles: [] });
  ownerDefault.saveChatDraft('same', data('owner main', 'main'), 'owner-main');
  ownerOtherIp.saveChatDraft('same', data('owner other IP', 'other-ip'), 'owner-ip');
  otherUser.saveChatDraft('same', data('operator main', 'operator'), 'operator-main');
  assert.equal(ownerDefault.loadChatDraft('same').text, 'owner main');
  assert.equal(ownerOtherIp.loadChatDraft('same').attachments[0].reference.id, 'other-ip');
  assert.equal(otherUser.loadChatDraft('same').attachments[0].reference.id, 'operator');
  ownerDefault.consumeChatDraft('same', 'owner-main');
  assert.equal(ownerDefault.loadChatDraft('same').text, '');
  assert.equal(ownerOtherIp.loadChatDraft('same').text, 'owner other IP');
  assert.equal(otherUser.loadChatDraft('same').text, 'operator main');
});
