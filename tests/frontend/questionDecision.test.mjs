import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const base = { id: 'ask-1', questions: [{ questionId: 'style', header: '风格', question: '你希望什么风格？',
  options: [{ label: '简洁' }, { label: '详细' }] }] };

function render(question) {
  const QuestionCards = componentModule('components/QuestionCards.tsx').default;
  return renderToStaticMarkup(React.createElement(QuestionCards, { questions: [question], sessionId: 'session-a' }));
}

test('ordinary unanswered preference shows the actual scheduled default without browser-side submission', () => {
  const html = render({ ...base, kind: 'preference', defaultAnswers: { style: ['简洁'] }, autoResolveAtMs: Date.now() + 30000 });
  assert.match(html, /默认/);
  assert.match(html, /简洁/);
  assert.match(html, /继续/);
});

test('resolved default decisions remain readable with the actual answer instead of disappearing', () => {
  const html = render({ ...base, status: 'defaulted', resolvedBy: 'default', answers: { style: ['详细'] } });
  assert.match(html, /详细/);
  assert.match(html, /默认/);
  assert.doesNotMatch(html, /question-card__submit/);
});

test('resolved human decisions preserve the accepted answer and never offer resubmission', () => {
  const html = render({ ...base, status: 'answered', resolvedBy: 'human', answers: { style: ['简洁'] } });
  assert.match(html, /简洁/);
  assert.match(html, /已答复/);
  assert.doesNotMatch(html, /question-card__submit/);
});

test('approval cards never advertise default approval even when a malformed payload offers it', () => {
  const html = render({ ...base, kind: 'approval', defaultAnswers: { style: ['允许'] }, autoResolveAtMs: Date.now() + 30000 });
  assert.match(html, /确认/);
  assert.doesNotMatch(html, /将按默认/);
});


test('a default deadline is a server settlement time, not an expired manual question', () => {
  const html = render({ ...base, kind: 'preference', defaultAnswers: { style: ['简洁'] },
    expiresAtMs: Date.now() - 10, autoResolveAtMs: Date.now() - 10 });
  assert.match(html, /等待服务端/);
  assert.doesNotMatch(html, /已过期或关闭/);
});

test('requiresHuman suppresses autonomous fallback even if kind is inconsistent', () => {
  const html = render({ ...base, kind: 'preference', requiresHuman: true,
    defaultAnswers: { style: ['允许'] }, autoResolveAtMs: Date.now() + 30000 });
  assert.match(html, /未答复不会自动授权/);
  assert.doesNotMatch(html, /将按默认/);
});

test('request drafts survive remount without crossing session, IP or user boundaries', () => {
  const values = new Map();
  const module = () => componentModule('vendor/deepseek-harness/message/question-drafts.ts',
    { user: 'u1', project: 'ip1', storageValues: values });
  module().createQuestionDraftStore('one').replace('request', { index: 0,
    drafts: [{ selected: ['简洁'], custom: '补充说明', skipped: false }] });
  const restored = module().createQuestionDraftStore('one').get('request');
  assert.equal(restored.drafts[0].custom, '补充说明');
  assert.equal(module().createQuestionDraftStore('two').get('request'), undefined);
  for (const [user, project] of [['u2', 'ip1'], ['u1', 'ip2']]) {
    const other = componentModule('vendor/deepseek-harness/message/question-drafts.ts', { user, project, storageValues: values });
    assert.equal(other.createQuestionDraftStore('one').get('request'), undefined);
  }
  module().createQuestionDraftStore('one').clear('request');
  assert.equal(module().createQuestionDraftStore('one').get('request'), undefined);
});

test('ordinary default preferences offer one explicit continue action while approvals never do', () => {
  const defaults = { style: ['简洁'] };
  const ordinary = render({ ...base, kind: 'preference', defaultAnswers: defaults, autoResolveAtMs: Date.now() + 30000 });
  assert.match(ordinary, /<button[^>]*class="question-card__default-continue(?: [^"]*)?"[^>]*>按默认答案继续<\/button>/);
  for (const kind of [{ kind: 'approval' }, { kind: 'preference', requiresHuman: true }]) {
    const approval = render({ ...base, ...kind, defaultAnswers: defaults, autoResolveAtMs: Date.now() + 30000 });
    assert.doesNotMatch(approval, /question-card__default-continue/);
  }
});

// Exercise the actual submit closure with its service boundary controlled. This
// catches defaults being ignored when no manual choice has been selected yet.
async function submitFixture(override) {
  const { readFileSync } = await import('node:fs');
  const { runInNewContext } = await import('node:vm');
  const ts = require('typescript');
  const source = readFileSync(new URL('../../web/frontend/src/components/QuestionCards.tsx', import.meta.url), 'utf8');
  const parsed = ts.createSourceFile('QuestionCards.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const card = parsed.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'QuestionCard');
  const statement = card.body.statements.find(node => ts.isVariableStatement(node)
    && node.declarationList.declarations.some(declaration => declaration.name.getText(parsed) === 'submit'));
  const code = ts.transpileModule(statement.getText(parsed) + '\nsubmit;', {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const received = [];
  let accepted;
  const submit = runInNewContext(code, {
    locked: false, items: base.questions, question: base, sessionId: 'legacy', defaultScheduled: true,
    submission: { current: null }, received: { current: false }, originalStatus: { current: undefined },
    mounted: { current: true }, valsOf: () => [], changePhase: () => {}, setError: () => {}, setReconcile: () => {},
    applyReceipt: value => { accepted = value; return true; },
    answerQuestion: async payload => {
      received.push(JSON.parse(JSON.stringify(payload)));
      return { ok: true, receipt: { id: payload.questionId, status: 'resolved', answers: payload.answers, resolvedBy: 'human' } };
    },
  });
  await submit(override);
  return { received, accepted };
}

test('choosing defaults submits every offered answer once as a human action without manual selection', async () => {
  const result = await submitFixture({ style: ['简洁'] });
  assert.deepEqual(result.received, [{ questionId: 'ask-1', answers: { style: ['简洁'] } }]);
  assert.equal(result.accepted.resolvedBy, 'human');
});

test('incomplete default batches never reach the answer API', async () => {
  const result = await submitFixture({});
  assert.deepEqual(result.received, []);
});
