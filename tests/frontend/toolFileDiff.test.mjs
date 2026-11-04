import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = new URL('../../web/frontend/src/vendor/deepseek-harness/tool/file-diff.ts', import.meta.url);

function projection() {
  const exports = {};
  if (existsSync(source)) {
    const code = ts.transpileModule(readFileSync(source, 'utf8'), { compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
    } }).outputText;
    runInNewContext(code, { exports, TextEncoder, TextDecoder });
  }
  assert.equal(typeof exports.projectUnifiedDiff, 'function');
  return exports.projectUnifiedDiff;
}

const plain = value => JSON.parse(JSON.stringify(value));

test('projects the exact reported hunks and keeps their independent old/new line positions', () => {
  const project = projection();
  const text = [
    'diff --git a/clip.txt b/clip.txt', 'index abc..def 100644', '--- a/clip.txt', '+++ b/clip.txt',
    '@@ -2,2 +3,3 @@ intro', ' shared', '-old', '+new', '+<em>second</em>',
    '@@ -20 +22 @@', '-tail', '\\ No newline at end of file', '+next', '',
  ].join('\r\n');
  const result = project(text);
  assert.equal(result.kind, 'unified');
  assert.equal(result.text, text);
  assert.equal(result.added, 3);
  assert.equal(result.removed, 2);
  assert.deepEqual(plain(result.hunks), [
    { header: '@@ -2,2 +3,3 @@ intro', rows: [
      { kind: 'context', old: 2, new: 3, text: 'shared' },
      { kind: 'del', old: 3, text: 'old' },
      { kind: 'add', new: 4, text: 'new' },
      { kind: 'add', new: 5, text: '<em>second</em>' },
    ] },
    { header: '@@ -20 +22 @@', rows: [
      { kind: 'del', old: 20, text: 'tail' },
      { kind: 'context', text: '\\ No newline at end of file' },
      { kind: 'add', new: 22, text: 'next' },
    ] },
  ]);
});

test('zero-count add and delete hunks keep the missing side unnumbered', () => {
  const project = projection();
  assert.deepEqual(plain(project('@@ -0,0 +1 @@\n+created').hunks[0].rows), [
    { kind: 'add', new: 1, text: 'created' },
  ]);
  assert.deepEqual(plain(project('@@ -8 +0,0 @@\n-deleted').hunks[0].rows), [
    { kind: 'del', old: 8, text: 'deleted' },
  ]);
});

test('uses raw fallback for invalid counts, metadata and unsupported line formats', () => {
  const project = projection();
  for (const text of [
    '@@ -1,2 +1 @@\n-old\n+new',
    '@@ -1 +1 @@\n-old\n+new\n+unexpected',
    '@@ -1 +1 @@\nnot a diff line',
    '@@ -0 +1 @@\n-old\n+new',
    '@@ -9007199254740992 +1 @@\n-old\n+new',
    '@@ -1 +1 @@\n\\ No newline at end of file\n-old\n+new',
    '--- a/file\n@@ -1 +1 @@\n-old\n+new',
    '@@ -1 +1 @@\n-old\n+new\ndiff --git a/other b/other\n@@ -1 +1 @@\n-a\n+b',
  ]) {
    assert.deepEqual(plain(project(text)), { kind: 'raw', reason: 'malformed', text });
  }
  const headerless = '-old\n+new';
  assert.deepEqual(plain(project(headerless)), { kind: 'raw', reason: 'unstructured', text: headerless });
  assert.deepEqual(plain(project(undefined)), { kind: 'missing' });
  assert.deepEqual(plain(project('  \n')), { kind: 'missing' });
});

test('bounds bytes and rows before projecting and avoids structural claims for clipped reports', () => {
  const project = projection();
  const overBytes = project('🙂'.repeat(20000));
  assert.equal(overBytes.kind, 'raw');
  assert.equal(overBytes.reason, 'truncated');
  assert.ok(Buffer.byteLength(overBytes.text, 'utf8') <= 65536);
  assert.ok(!overBytes.text.endsWith('\uFFFD'));

  const overLines = project('x\n'.repeat(6000));
  assert.equal(overLines.kind, 'raw');
  assert.equal(overLines.reason, 'truncated');
  assert.equal(overLines.text.split('\n').length, 5000);

  const clippedByEngine = '@@ -1 +1 @@\n-old\n+new\n[truncated: remaining content omitted]';
  assert.deepEqual(plain(project(clippedByEngine)), { kind: 'raw', reason: 'truncated', text: clippedByEngine });
});
