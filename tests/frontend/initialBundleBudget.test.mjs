import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

test('a cold login does not download the full application bundle', () => {
  const root = fileURLToPath(new URL('../../web/frontend/', import.meta.url));
  const output = realpathSync(mkdtempSync(resolve(tmpdir(), 'chirpiva-initial-bundle-')));
  try {
    const build = spawnSync(process.execPath, ['node_modules/vite/bin/vite.js', 'build', '--outDir', output],
      { cwd: root, encoding: 'utf8', timeout: 60000 });
    assert.equal(build.status, 0, build.stderr);
    const html = readFileSync(resolve(output, 'index.html'), 'utf8');
    const paths = [...html.matchAll(/(?:src|href)="\.\/([^"?]+\.js)"/g)].map(match => match[1]);
    assert.ok(paths.length);
    const bytes = [...new Set(paths)].reduce((total, path) => total + gzipSync(readFileSync(resolve(output, path))).length, 0);
    assert.ok(bytes < 128 * 1024, `Login initially downloads ${bytes} compressed JavaScript bytes`);
    assert.equal(html.includes('fonts.googleapis.com'), false, 'The login shell should use its reliable local font stack');
  } finally {
    rmSync(output, { recursive: true, force: true });
  }
});
