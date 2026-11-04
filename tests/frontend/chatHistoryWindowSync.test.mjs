import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const { mergeWindowedCatalog } = componentModule('lib/sessionSync.ts');
const msg = (content) => ({ role: 'user', content });
const row = (updated, messages, base) => ({
  id: 'chat', updated, created: 0, title: 'Chat', messages,
  ...(base ? { historyWindow: { start: 20, before: 'cursor-' + base, base } } : {}),
});

test('server acknowledgement advances the base without losing messages appended during PUT', () => {
  const sent = row(10, [msg('saved'), msg('submitted')], 'old');
  const latest = row(11, [...sent.messages, msg('in flight')], 'old');
  const saved = row(10, sent.messages, 'confirmed');
  const [merged] = mergeWindowedCatalog([latest], [saved], [], [sent]);
  assert.equal(merged.historyWindow.base, 'confirmed');
  assert.equal(merged.updated, 11);
  assert.equal(merged.messages, latest.messages);
});

test('a divergent server acknowledgement cannot certify an unsaved local suffix', () => {
  const sent = row(10, [msg('submitted')], 'old');
  const latest = row(11, [...sent.messages, msg('unsaved')], 'old');
  const saved = row(10, [msg('another tab')], 'foreign');
  assert.equal(mergeWindowedCatalog([latest], [saved], [], [sent])[0], latest);
});

test('initial restore uses a bounded authoritative window when the cached complete history is identical', () => {
  const cached = row(10, [...Array.from({ length: 20 }, (_, index) => msg(String(index))), msg('newest')]);
  const saved = row(10, [msg('newest')], 'fresh');
  assert.equal(mergeWindowedCatalog([cached], [saved])[0], saved);
});
