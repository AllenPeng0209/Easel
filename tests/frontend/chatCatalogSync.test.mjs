import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');

function tracker() {
  const path = new URL('../../web/frontend/src/lib/chatCatalogSync.ts', import.meta.url);
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023,
  } }).outputText, { exports });
  return new exports.CatalogSyncTracker();
}
const first = { id: 'first', title: 'Saved video', created: 1, updated: 2,
  messages: [{ role: 'assistant', content: 'ready', events: [{ id: 'tool', detailRef: 'a'.repeat(64) }] }] };

test('reading an unchanged catalog does not schedule a full-history write', () => {
  const sync = tracker();
  sync.observe([first]);
  assert.equal(sync.pending([structuredClone(first)]).length, 0);
  assert.equal(sync.pending([{ ...first, historyWindow: { start: 0, base: 'cursor-only-change', before: null } }]).length, 0);
});

test('only a dirty session is transmitted and an unused blank draft remains local', () => {
  const sync = tracker();
  sync.observe([first]);
  const changed = { ...first, title: 'New title', updated: 3 };
  const pending = sync.pending([changed, { id: 'blank', title: '新对话', created: 2, messages: [] }]);
  assert.equal(pending.length, 1);
  assert.equal(pending[0].id, 'first');
  sync.observe([changed]);
  assert.equal(sync.pending([changed]).length, 0);
  assert.equal(sync.pending([{ id: 'named', title: 'Named draft', created: 3, messages: [] }]).length, 1);
});
