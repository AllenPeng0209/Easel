import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = fileURLToPath(new URL('../../web/frontend/', import.meta.url));
const require = createRequire(path.join(root, 'package.json'));
const ts = require('typescript');

// Load the actual TSX component tree. CSS-module names are retained for markup
// assertions; no component or state reducer is replaced with a test double.
export function componentModule(relative, { user = '', project = 'default', storageValues, globals = {}, externals = {} } = {}) {
  const cache = new Map();
  const values = storageValues || new Map();
  values.set('easel_authenticated_user', user);
  values.set(user ? `easel_user:${user}:easel_project` : 'easel_project', project);
  const storage = { getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  const load = filename => {
    if (filename.endsWith('.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => key }) };
    const candidate = ['', '.ts', '.tsx', '.js'].map(ext => filename + ext).find(existsSync);
    if (!candidate) throw new Error(`Missing component module: ${filename}`);
    if (cache.has(candidate)) return cache.get(candidate).exports;
    const module = { exports: {} };
    cache.set(candidate, module);
    const code = ts.transpileModule(readFileSync(candidate, 'utf8'), { compilerOptions: {
      target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    vm.runInNewContext(code, {
      module, exports: module.exports, console, Date, Math, JSON, URL,
      localStorage: storage, sessionStorage: storage,
      window: { location: { pathname: "/", origin: "http://127.0.0.1:7860" } },
      setTimeout, clearTimeout, setInterval, clearInterval, ...globals,
      require: name => name.startsWith('.') ? load(path.resolve(path.dirname(candidate), name))
        : Object.hasOwn(externals, name) ? externals[name] : require(name),
    }, { filename: candidate });
    return module.exports;
  };
  return load(path.join(root, 'src', relative));
}
