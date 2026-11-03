// Evaluate the real scope module for existing VM-based component/API tests.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
function compile(relative) {
  const source = readFileSync(new URL(relative, import.meta.url), 'utf8');
  return ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
}
const outputText = compile('../../web/frontend/src/lib/projectScope.ts');
const sessionSyncText = compile('../../web/frontend/src/lib/sessionSync.ts');
export const projectScope = {};
runInNewContext(outputText, { exports: projectScope });

export function runWithProjectScope(code, context) {
  const scoped = {};
  const sessionSync = {};
  runInNewContext(sessionSyncText, { ...context, exports: sessionSync });
  runInNewContext(outputText, { ...context, exports: scoped });
  return runInNewContext(code, { ...context, ...scoped,
    require: name => name === './sessionSync' ? sessionSync : name.endsWith('projectScope') ? scoped : context.require?.(name),
  });
}
