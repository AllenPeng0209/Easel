import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const question = (turnId = 'turn-one') => ({ id: 'harness_same', turnId, kind: 'approval', requiresHuman: true,
  questions: [{ questionId: 'choice', question: `确认 ${turnId}`, options: [{ label: '允许' }, { label: '拒绝' }] }] });

async function fixture(run) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const calls = [];
  const done = [];
  try {
    const Cards = componentModule('components/QuestionCards.tsx', { globals: {
      window, document, Headers, Response, fetch: (url, options) => new Promise(resolve => calls.push({ url, options, resolve })),
    } }).default;
    const update = q => React.act(async () => root.render(React.createElement(Cards, {
      questions: [q], sessionId: 'session', onDone: receipt => done.push(receipt),
    })));
    const click = selector => React.act(async () => document.querySelector(selector).click());
    const answer = () => calls.filter(call => call.url.endsWith('/question/answer'));
    const status = () => calls.filter(call => call.url.endsWith('/question/status'));
    await run({ document, update, click, answer, status, done });
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
}

test('duplicate activation dispatches once and an unconfirmed response cannot close the card', async () => {
  await fixture(async ({ document, update, click, answer, done }) => {
    await update(question());
    await click('.question-card__option');
    const submit = document.querySelector('.question-card__submit');
    await React.act(async () => { submit.click(); submit.click(); });
    assert.equal(answer().length, 1);
    assert.deepEqual(JSON.parse(answer()[0].options.body), {
      questionId: 'harness_same', sessionId: 'session', turnId: 'turn-one', answers: { choice: ['允许'] },
    });
    assert.match(document.querySelector('.question-card').textContent, /提交中/);
    await React.act(async () => answer()[0].resolve(Response.json({ ok: true, status: 'pending' })));
    assert.deepEqual(done, []);
    assert.ok(document.querySelector('.question-card'));
    assert.equal(document.querySelector('.question-card__submit').hasAttribute('disabled'), true);
    assert.match(document.querySelector('.question-card').textContent, /核对/);
  });
});

test('only a matching terminal receipt updates the original request, including another browser winning', async () => {
  await fixture(async ({ document, update, click, answer, done }) => {
    await update(question());
    await click('.question-card__option');
    await click('.question-card__submit');
    const previous = answer()[0];
    await update(question('turn-two'));
    await React.act(async () => previous.resolve(Response.json({ ok: true, receipt: {
      id: 'harness_same', status: 'resolved', resolvedBy: 'human', answers: { choice: ['允许'] },
    } })));
    assert.deepEqual(done, []);
    assert.match(document.querySelector('.question-card').textContent, /turn-two/);
    await click('.question-card__option');
    await click('.question-card__submit');
    const winning = { id: 'harness_same', status: 'declined', resolvedBy: 'human', answers: { choice: ['拒绝'] } };
    await React.act(async () => answer()[1].resolve(Response.json({ ok: false, status: 'declined', receipt: winning })));
    assert.deepEqual(JSON.parse(JSON.stringify(done)), [winning]);
    assert.match(document.querySelector('.question-resolution').textContent, /未获授权/);
  });
});


test('a pending status read unlocks a retry, while a revoked receipt closes only its exact card', async () => {
  await fixture(async ({ document, update, click, answer, status, done }) => {
    await update(question());
    await click('.question-card__option');
    await click('.question-card__submit');
    await React.act(async () => answer()[0].resolve(Response.json({ ok: false, status: 'unknown' })));
    assert.equal(document.querySelector('.question-card').dataset.questionPhase, 'checking');
    await React.act(async () => status().at(-1).resolve(Response.json({ questions: { harness_same: { status: 'pending' } } })));
    assert.equal(document.querySelector('.question-card__submit').hasAttribute('disabled'), false);
    await click('.question-card__submit');
    await React.act(async () => answer()[1].resolve(Response.json({ ok: false, status: 'revoked',
      receipt: { id: 'other', status: 'revoked' } })));
    assert.equal(done.length, 0);
    assert.equal(document.querySelector('.question-card').dataset.questionPhase, 'checking');
    await React.act(async () => status().at(-1).resolve(Response.json({ questions: { harness_same: { status: 'revoked' } } })));
    assert.equal(document.querySelector('.question-card').dataset.questionPhase, 'revoked');
    assert.match(document.querySelector('.question-card').textContent, /权限已撤销/);
    assert.deepEqual(JSON.parse(JSON.stringify(done)), [{ id: 'harness_same', status: 'revoked' }]);
  });
});
