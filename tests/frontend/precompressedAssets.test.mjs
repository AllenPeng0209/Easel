import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { brotliDecompressSync, gunzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

test('public build precompression preserves source bytes and produces valid gzip and Brotli', () => {
  const folder = mkdtempSync(resolve(tmpdir(), 'chirpiva-public-asset-'));
  try {
    const file = resolve(folder, 'entry-TestHash.js');
    const original = Buffer.from('export const message="响应迅速";\n'.repeat(1000));
    writeFileSync(file, original);
    const run = spawnSync(process.execPath, [fileURLToPath(new URL('../../deploy/chirpiva/precompress-assets.mjs', import.meta.url)), folder], { encoding: 'utf8' });
    assert.equal(run.status, 0, run.stderr);
    assert.deepEqual(readFileSync(file), original);
    assert.deepEqual(gunzipSync(readFileSync(file + '.gz')), original);
    assert.deepEqual(brotliDecompressSync(readFileSync(file + '.br')), original);
  } finally { rmSync(folder, { recursive: true, force: true }); }
});
