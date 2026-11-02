// Evaluate the real scope module for existing VM-based component/API tests.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/lib/projectScope.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} });
export const projectScope = {};
runInNewContext(outputText, { exports: projectScope });

export function runWithProjectScope(code, context) {
  const scoped = {};
  runInNewContext(outputText, { ...context, exports: scoped });
  return runInNewContext(code, { ...context, ...scoped,
    require: name => name.endsWith('projectScope') ? scoped : context.require?.(name),
  });
}
