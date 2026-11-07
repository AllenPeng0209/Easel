import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');

test('latency batches contain bounded labels and numbers without resource URLs', async () => {
  const source = readFileSync(new URL('../../web/frontend/src/lib/performance.ts', import.meta.url), 'utf8');
  const exports = {}, requests = [];
  const scope = { projectUrl: value => value, projectFetch: async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) }); return { ok: true };
  } };
  runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023 } }).outputText,
    { exports, require: () => scope, Date, JSON, Number, setInterval: () => 1,
      sessionStorage: { getItem: () => 'verified-owner' }, navigator: { connection: { effectiveType: '4g' } },
      window: { innerWidth: 1200, location: { pathname: '/' }, addEventListener() {} },
      performance: { getEntriesByType: () => [] } });
  exports.startPerformanceMonitoring();
  exports.recordPerformance('video_first_frame', 123, 'workshop');
  exports.recordPerformance('api', 8, 'https://private.test/file?access=fixture');
  await exports.flushPerformanceMetrics();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/performance/metrics');
  assert.equal(requests[0].body.metrics.length, 1);
  assert.deepEqual(requests[0].body.metrics[0], { metric: 'video_first_frame', label: 'workshop', value_ms: 123, network: '4g', device: 'desktop' });
});
