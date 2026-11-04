import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { projectScope, runWithProjectScope } from './projectScopeHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
const ast = ts.createSourceFile('api.ts', source, ts.ScriptTarget.Latest, true);
const fn = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'streamChat');
const { outputText } = ts.transpileModule(fn.getText(ast).replace('export ', '') + '\nstreamChat;', { compilerOptions: { target: ts.ScriptTarget.ES2022 } });
for (const resume of [false, true]) {
  test(`structured tool and decision events survive ${resume ? 'replay' : 'live'} SSE`, async () => {
    const observed = [];
    let resolve;
    const done = new Promise(r => { resolve = r; });
    const frames = 'id: 1\nevent: agent_event\ndata: {"id":"a1","kind":"tool","callId":"call","name":"shell","status":"completed","output":"ok","exitCode":0}\n\n' +
      'id: 2\nevent: agent_event\ndata: {"id":"a2","kind":"decision","questionId":"q1","source":"default","answers":{"format":["竖版"]}}\n\n' +
      'id: 3\nevent: done\ndata: {}\n\n';
    const stream = runWithProjectScope(outputText, { projectUrl: projectScope.projectUrl, BASE: '', AbortController, TextDecoder, Date, Error, setTimeout,
      fetch: async () => new Response(new ReadableStream({ start(controller) {
        const bytes = new TextEncoder().encode(frames);
        for (let n = 0; n < bytes.length; n += 5) controller.enqueue(bytes.slice(n, n + 5));
        controller.close();
      } })),
    });
    stream('test', undefined, 's', () => {}, resolve, err => { throw err; }, undefined, undefined, undefined, 'turn', resume,
      undefined, [], undefined, undefined, undefined, event => observed.push(event));
    await done;
    assert.equal(observed.length, 2);
    assert.equal(observed[0].exitCode, 0);
    assert.equal(observed[0].output, 'ok');
    assert.equal(observed[1].answers.format[0], '竖版');
  });
}
