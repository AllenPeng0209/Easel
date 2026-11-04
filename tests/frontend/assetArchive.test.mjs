import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, realpath, rm, symlink, writeFile, access } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { archiveAssetDirectory, archiveBundleAssets } from '../../web/frontend/build/assetArchive.mjs';

async function fixture(action) {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'easel-assets-')));
  try { await action(root); } finally { await rm(root, { recursive: true, force: true }); }
}

async function output(root, name, files) {
  const directory = join(root, name);
  const bundle = {};
  for (const [fileName, contents] of Object.entries(files)) {
    const target = join(directory, fileName);
    await mkdir(join(target, '..'), { recursive: true });
    await writeFile(target, contents);
    bundle[fileName] = { fileName };
  }
  return { directory, bundle };
}

const exists = path => access(path).then(() => true, () => false);

test('fresh consecutive builds retain the old lazy-import graph and all generated media assets', async () => fixture(async root => {
  const archive = join(root, 'archive');
  const old = await output(root, 'old-build', {
    'index.html': '<script src="assets/index-old.js"></script>',
    'assets/index-old.js': 'import("./Panel-old.js")',
    'assets/Panel-old.js': 'import "./Panel-old.css"',
    'assets/Panel-old.css': '@font-face{src:url(./fonts/old.woff2)}',
    'assets/fonts/old.woff2': Buffer.from([0, 1, 2, 255]),
    'assets/preview-old.png': Buffer.from([137, 80, 78, 71]),
  });
  await archiveBundleAssets(old.directory, old.bundle, archive);
  await rm(old.directory, { recursive: true });
  const next = await output(root, 'new-build', {
    'index.html': '<script src="assets/index-new.js"></script>',
    'assets/index-new.js': 'import("./Panel-new.js")',
    'assets/Panel-new.js': 'export default "new"',
  });
  await archiveBundleAssets(next.directory, next.bundle, archive);
  assert.equal(await readFile(join(archive, 'Panel-old.js'), 'utf8'), 'import "./Panel-old.css"');
  assert.equal(await readFile(join(archive, 'index-old.js'), 'utf8'), 'import("./Panel-old.js")');
  assert.deepEqual(await readFile(join(archive, 'fonts/old.woff2')), Buffer.from([0, 1, 2, 255]));
  assert.equal(await readFile(join(archive, 'Panel-new.js'), 'utf8'), 'export default "new"');
  assert.equal(await exists(join(archive, 'index.html')), false);
}));

test('the existing canonical asset directory is preserved before a fresh staging release', async () => fixture(async root => {
  const existing = await output(root, 'canonical', {
    'assets/existing-hash.js': 'export const prior = true',
    'assets/existing-hash.css': 'body{color:teal}',
  });
  const archive = join(root, 'archive');
  await archiveAssetDirectory(join(existing.directory, 'assets'), archive);
  await rm(existing.directory, { recursive: true });
  assert.equal(await readFile(join(archive, 'existing-hash.js'), 'utf8'), 'export const prior = true');
  assert.equal(await readFile(join(archive, 'existing-hash.css'), 'utf8'), 'body{color:teal}');
}));

test('an immutable filename cannot be replaced with different bytes', async () => fixture(async root => {
  const old = await output(root, 'old', { 'assets/shared-hash.js': 'old-valid-module' });
  const next = await output(root, 'next', { 'assets/shared-hash.js': 'different-module' });
  const archive = join(root, 'archive');
  await archiveBundleAssets(old.directory, old.bundle, archive);
  await assert.rejects(archiveBundleAssets(next.directory, next.bundle, archive), /different|conflict|immutable/i);
  assert.equal(await readFile(join(archive, 'shared-hash.js'), 'utf8'), 'old-valid-module');
}));

test('HTML entrypoints and outputs outside assets never enter the public compatibility archive', async () => fixture(async root => {
  const build = await output(root, 'build', {
    'index.html': 'current-entry-only',
    'favicon.svg': '<svg/>',
    'business/private.txt': 'business-data-must-not-be-published',
    'assets/index.html': 'not-a-module',
    'assets/allowed-hash.js': 'export {}',
  });
  const archive = join(root, 'archive');
  await archiveBundleAssets(build.directory, build.bundle, archive);
  assert.deepEqual(await readdir(archive), ['allowed-hash.js']);
}));

test('asset traversal is rejected before reading outside the generated output', async () => fixture(async root => {
  const build = await output(root, 'build', { 'assets/good-hash.js': 'export {}' });
  for (const fileName of ['assets/../private.js', '../assets/private.js', '/assets/private.js', 'assets//private.js', 'assets/./private.js', 'assets\\private.js']) {
    await assert.rejects(archiveBundleAssets(build.directory, { [fileName]: { fileName } }, join(root, 'archive')), /path|asset|travers|relative/i);
  }
  assert.equal(await exists(join(root, 'archive/private.js')), false);
}));

test('source symlinks cannot publish a private file or a foreign directory', async () => fixture(async root => {
  const build = await output(root, 'build', { 'assets/good-hash.js': 'export {}' });
  const privateFile = join(root, 'private.txt');
  await writeFile(privateFile, 'private-data');
  await symlink(privateFile, join(build.directory, 'assets/leak-hash.js'));
  await assert.rejects(archiveAssetDirectory(join(build.directory, 'assets'), join(root, 'archive')), /symbolic|symlink/i);
  assert.equal(await exists(join(root, 'archive/leak-hash.js')), false);
  await symlink(join(build.directory, 'assets'), join(root, 'aliased-assets'), 'dir');
  await assert.rejects(archiveAssetDirectory(join(root, 'aliased-assets'), join(root, 'other-archive')), /symbolic|symlink/i);
}));

test('a symlinked archive subdirectory cannot redirect writes', async () => fixture(async root => {
  const build = await output(root, 'build', { 'assets/fonts/face-hash.woff2': 'font' });
  const archive = join(root, 'archive');
  const outside = join(root, 'outside');
  await mkdir(archive); await mkdir(outside);
  await symlink(outside, join(archive, 'fonts'), 'dir');
  await assert.rejects(archiveBundleAssets(build.directory, build.bundle, archive), /symbolic|symlink/i);
  assert.equal(await exists(join(outside, 'face-hash.woff2')), false);
}));

test('concurrent identical builds keep one complete immutable asset', async () => fixture(async root => {
  const contents = Buffer.alloc(256 * 1024, 71);
  const build = await output(root, 'build', { 'assets/large-hash.js': contents });
  const archive = join(root, 'archive');
  await Promise.all(Array.from({ length: 6 }, () => archiveBundleAssets(build.directory, build.bundle, archive)));
  assert.deepEqual(await readFile(join(archive, 'large-hash.js')), contents);
  assert.deepEqual(await readdir(archive), ['large-hash.js']);
}));
