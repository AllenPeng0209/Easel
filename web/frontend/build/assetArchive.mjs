/** Keep immutable build assets available to tabs opened before a deployment. */
import { constants } from 'node:fs';
import { chmod, link, lstat, mkdir, open, readdir, unlink } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { dirname, extname, isAbsolute, join, parse, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = fileURLToPath(new URL('../', import.meta.url));
export const FRONTEND_ASSET_ARCHIVE = resolve(frontendRoot, '../../.scratch/frontend-assets');

function relativeAssetPath(value) {
  if (typeof value !== 'string' || !value || isAbsolute(value) || value.includes('\\') || value.includes('\0')
    || value.split('/').some(part => !part || part === '.' || part === '..')) {
    throw new Error('Invalid relative asset path');
  }
  return value;
}

function isEntryHtml(value) {
  return ['.html', '.htm'].includes(extname(value).toLowerCase());
}

async function noSymlinks(path, allowMissing = false) {
  const absolute = resolve(path);
  let current = parse(absolute).root;
  for (const part of absolute.slice(current.length).split(sep)) {
    if (!part) continue;
    current = join(current, part);
    let info;
    try { info = await lstat(current); }
    catch (error) { if (allowMissing && error.code === 'ENOENT') return; throw error; }
    if (info.isSymbolicLink()) throw new Error('Asset archive refuses symbolic links');
  }
}

async function readRegular(path) {
  await noSymlinks(path);
  const file = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  try {
    if (!(await file.stat()).isFile()) throw new Error('Asset source must be a regular file');
    return await file.readFile();
  } finally { await file.close(); }
}

async function collect(directory, prefix = '') {
  const result = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const relative = relativeAssetPath(prefix ? prefix + '/' + entry.name : entry.name);
    if (entry.isSymbolicLink()) throw new Error('Asset archive refuses symbolic links');
    if (entry.isDirectory()) result.push(...await collect(join(directory, entry.name), relative));
    else if (entry.isFile() && !isEntryHtml(relative)) result.push(relative);
    else if (!entry.isFile()) throw new Error('Asset source must be a regular file');
  }
  return result;
}

async function publishImmutable(destination, contents) {
  await noSymlinks(destination, true);
  await mkdir(dirname(destination), { recursive: true, mode: 0o700 });
  await noSymlinks(dirname(destination));
  await chmod(dirname(destination), 0o700);
  const temporary = destination + '.tmp-' + randomUUID();
  const file = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW || 0), 0o600);
  try {
    try { await file.writeFile(contents); await file.sync(); }
    finally { await file.close(); }
    try {
      // A hard link publishes the complete file atomically without replacing
      // an existing immutable filename, including concurrent build writers.
      await link(temporary, destination);
      return true;
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      if (!(await readRegular(destination)).equals(contents)) {
        throw new Error('Immutable asset conflict: same filename has different bytes');
      }
      return false;
    }
  } finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}

async function archiveFiles(source, files, archive) {
  if (!files.length) return { copied: 0, existing: 0 };
  await noSymlinks(source);
  await noSymlinks(archive, true);
  const contents = await Promise.all(files.map(async relative => [relative, await readRegular(join(source, relative))]));
  await mkdir(archive, { recursive: true, mode: 0o700 });
  await noSymlinks(archive);
  await chmod(archive, 0o700);
  const result = { copied: 0, existing: 0 };
  for (const [relative, bytes] of contents) {
    if (await publishImmutable(join(archive, relative), bytes)) result.copied++;
    else result.existing++;
  }
  return result;
}

/** Only pass a generated assets directory, never an arbitrary release backup. */
export async function archiveAssetDirectory(sourceAssets, archive = FRONTEND_ASSET_ARCHIVE) {
  await noSymlinks(sourceAssets, true);
  try {
    if (!(await lstat(sourceAssets)).isDirectory()) throw new Error('Asset source must be a directory');
  } catch (error) {
    if (error.code === 'ENOENT') return { copied: 0, existing: 0 };
    throw error;
  }
  return archiveFiles(resolve(sourceAssets), await collect(sourceAssets), resolve(archive));
}

/** Archive the exact generated graph even when Vite writes a fresh --outDir. */
export async function archiveBundleAssets(outputDirectory, bundle, archive = FRONTEND_ASSET_ARCHIVE) {
  const files = [];
  for (const fileName of Object.keys(bundle)) {
    relativeAssetPath(fileName);
    if (!fileName.startsWith('assets/')) continue;
    const relative = relativeAssetPath(fileName.slice('assets/'.length));
    if (!isEntryHtml(relative)) files.push(relative);
  }
  return archiveFiles(resolve(outputDirectory, 'assets'), files, resolve(archive));
}

export function assetArchivePlugin() {
  return {
    name: 'easel-immutable-asset-archive',
    apply: 'build',
    async writeBundle(options, bundle) {
      if (!options.dir) throw new Error('The frontend asset archive requires an output directory');
      await archiveAssetDirectory(join(frontendRoot, 'dist/assets'));
      await archiveBundleAssets(options.dir, bundle);
    },
  };
}
