import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = ts.createSourceFile('App.tsx', readFileSync(new URL('../../web/frontend/src/App.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const app = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'App');

test('App accepts scoped publish navigation, rejects stale/foreign requests and passes the same intent to its publish route', () => {
  const effect = app.body.statements.find(node => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)
    && node.expression.expression.getText(source) === 'useEffect' && node.getText(source).includes('PUBLICATION_HANDOFF_EVENT'));
  assert.ok(effect, 'missing scoped AI → publish navigation listener');
  const helper = componentModule('lib/publicationHandoff.ts', { user: 'operator-one' });
  const drafts = componentModule('lib/publicationDraft.ts', { user: 'operator-one' });
  let saved; let allowSave = true; const order = [];
  let listener; let removed = false; let cleanup;
  const environment = { exports: {}, canOperate: true, isPlatformAdmin: true, publicationHandoff: null, currentPage: 'chat', selectedPersona: 'persona-one',
    PUBLICATION_HANDOFF_EVENT: helper.PUBLICATION_HANDOFF_EVENT, normalizePublicationHandoff: helper.normalizePublicationHandoff,
    applyPublicationHandoff: drafts.applyPublicationHandoff,
    loadPublishDraft: () => saved || { title: 'draft', body: '', platforms: [], overrides: {}, tags: '' },
    savePublishDraft: value => { order.push('save'); if (allowSave) saved = value; return allowSave; },
    setBusinessNotice: value => { environment.notice = value; },
    window: { addEventListener: (_, value) => { listener = value; }, removeEventListener: (_, value) => { removed = value === listener; } },
    useEffect: callback => { cleanup = callback(); },
    setPublicationHandoff: value => { environment.publicationHandoff = value; },
    setCurrentPage: value => { order.push('navigate'); environment.currentPage = value; },
    PublishPage: 'publish-page', consumePublicationHandoff() {}, require: () => ({ jsx: (type, props) => ({ type, props }) }) };
  const run = text => runInNewContext(ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, environment);
  run(effect.getText(source));
  const now = Date.now();
  const handoff = { schemaVersion: 1, actorId: 'operator-one', projectId: 'default', createdAt: now, expiresAt: now + 60000,
    kind: 'approval', approvalId: 'same-approval', payloadHash: 'a'.repeat(64) };
  listener({ detail: { ...handoff, projectId: 'other-ip' } }); assert.equal(environment.currentPage, 'chat');
  listener({ detail: { ...handoff, actorId: 'other-actor' } }); assert.equal(environment.currentPage, 'chat');
  listener({ detail: { ...handoff, expiresAt: now - 1 } }); assert.equal(environment.currentPage, 'chat');
  allowSave = false; listener({ detail: handoff });
  assert.equal(environment.currentPage, 'chat'); assert.ok(environment.notice);
  allowSave = true; order.length = 0;
  listener({ detail: handoff }); assert.equal(environment.currentPage, 'publish'); assert.equal(environment.publicationHandoff, handoff);
  assert.deepEqual(order, ['save', 'navigate']); assert.equal(saved.approvalReference.approvalId, 'same-approval');
  const declaration = app.body.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(item => item.name.getText(source) === 'renderPage'));
  const rendered = run(declaration.getText(source) + '\nrenderPage();');
  assert.equal(rendered.type, 'publish-page'); assert.equal(rendered.props.handoff, handoff);
  cleanup(); assert.equal(removed, true);
});
