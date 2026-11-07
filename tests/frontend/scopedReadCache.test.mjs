import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function cache() {
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(new URL('../../web/frontend/src/lib/scopedReadCache.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 } }).outputText,
  { exports, AbortController, Promise, Date });
  return new exports.ScopedReadCache();
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('two consumers share a read and one cancellation does not cancel the other', async () => {
  const reads = cache(); let complete, calls = 0, upstream;
  const loader = signal => { calls++; upstream = signal; return new Promise(resolve => { complete = resolve; }); };
  const controller = new AbortController();
  const first = reads.read('owner:ip:outputs', loader, { signal: controller.signal, ttl: 1000 });
  const second = reads.read('owner:ip:outputs', loader, { ttl: 1000 });
  await tick(); controller.abort();
  await assert.rejects(first, error => error.name === 'AbortError');
  assert.equal(upstream.aborted, false);
  complete({ files: ['clip.mp4'] });
  assert.deepEqual(await second, { files: ['clip.mp4'] });
  assert.deepEqual(await reads.read('owner:ip:outputs', loader, { ttl: 1000 }), { files: ['clip.mp4'] });
  assert.equal(calls, 1);
});

test('a mutation invalidates in-flight reads and another IP gets its own result', async () => {
  const reads = cache(); let complete;
  const old = reads.read('first-ip', () => new Promise(resolve => { complete = resolve; }), { ttl: 1000 });
  await tick();
  reads.invalidate();
  const current = reads.read('first-ip', async () => 'new', { ttl: 1000 });
  complete('stale');
  await assert.rejects(old, error => error.name === 'AbortError');
  assert.equal(await current, 'new');
  assert.equal(await reads.read('second-ip', async () => 'different', { ttl: 1000 }), 'different');
  assert.equal(await reads.read('first-ip', async () => 'unexpected', { ttl: 1000 }), 'new');
});
