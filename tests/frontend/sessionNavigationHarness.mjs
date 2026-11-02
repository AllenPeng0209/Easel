// Evaluate the real navigation adapter and vendored pure converters in VM tests.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const cache = new Map();
function load(path) {
  if (cache.has(path)) return cache.get(path);
  const exports = {};
  cache.set(path, exports);
  const code = ts.transpileModule(readFileSync(path, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2023,
  } }).outputText;
  runInNewContext(code, { exports, require: name => load(resolve(dirname(path), `${name}.ts`)) });
  return exports;
}
export const sessionNavigation = load(fileURLToPath(new URL('../../web/frontend/src/lib/sessionNavigation.ts', import.meta.url)));
