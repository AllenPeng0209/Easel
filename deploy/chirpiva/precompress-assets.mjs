#!/usr/bin/env node
/** Precompress public build output; original immutable files stay byte-identical. */
import { readdir, readFile, realpath, lstat, writeFile, rename, unlink } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { brotliCompressSync, constants, gzipSync } from 'node:zlib';

let files = 0, sourceBytes = 0, brotliBytes = 0;
async function atomic(path, data) {
  try { if ((await lstat(path)).isSymbolicLink()) throw new Error('Refusing compressed asset symlink'); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  const temporary = path + '.tmp-' + process.pid;
  try { await writeFile(temporary, data, { flag: 'wx', mode: 0o644 }); await rename(temporary, path); }
  finally { await unlink(temporary).catch(error => { if (error.code !== 'ENOENT') throw error; }); }
}
async function walk(directory) {
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, item.name);
    if (item.isSymbolicLink()) throw new Error('Refusing public asset symlink');
    if (item.isDirectory()) { await walk(path); continue; }
    if (!item.isFile() || !/\.(?:js|css|svg|json|txt)$/.test(item.name)) continue;
    const info = await lstat(path);
    if (info.size < 512 || info.size > 8 * 1024 * 1024) continue;
    const original = await readFile(path);
    const gzip = gzipSync(original, { level: 9 });
    const brotli = brotliCompressSync(original, { params: { [constants.BROTLI_PARAM_QUALITY]: 9 } });
    await atomic(path + '.gz', gzip); await atomic(path + '.br', brotli);
    files++; sourceBytes += original.length; brotliBytes += brotli.length;
  }
}
if (!process.argv.slice(2).length) throw new Error('Provide a public build output directory');
for (const directory of process.argv.slice(2)) await walk(await realpath(resolve(directory)));
process.stdout.write(JSON.stringify({ files, sourceBytes, brotliBytes }) + '\n');
