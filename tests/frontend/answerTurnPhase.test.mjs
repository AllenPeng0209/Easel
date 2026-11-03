import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');
const Bubble = componentModule('components/MessageBubble.tsx', {
  globals: { DOMParser: class { parseFromString(html) {
    return { body: { innerHTML: html }, querySelectorAll: () => [] };
  } } },
  externals: { dompurify: { sanitize: text => text } },
}).default;
const show = (fields, props = {}) => parseHTML(renderToStaticMarkup(React.createElement(Bubble, {
  message: { role: 'assistant', content: '', ...fields }, ...props,
}))).document;
const answer = { itemId: 'native-final-answer' };

test('explicit model answer completion can coexist with an active turn and running tool', () => {
  const document = show({ content: '模型最终文字' }, { isStreaming: true, turnLifecycle: 'running', answerCompletion: answer,
    events: [{ id: 'tool', callId: 'tool', kind: 'tool', name: 'render_video', status: 'running' }] });
  assert.match(document.querySelector('[data-answer-phase=completed]').textContent, /答复已完成/);
  assert.match(document.querySelector('[data-turn-phase=running]').textContent, /本轮进行中/);
  assert.equal(document.querySelector('[data-tool-call=tool]').dataset.toolPhase, 'running');
  assert.match(document.querySelector('[data-assistant-answer]').textContent, /模型最终文字/);
  assert.equal(document.querySelector('.streaming-cursor'), null);
});

test('normal turn completion or answer text cannot invent an answer completion observation', () => {
  const plain = show({ content: '旧文字', status: 'completed' });
  assert.ok(plain.querySelector('[data-turn-phase=completed]'));
  assert.equal(plain.querySelector('[data-answer-phase]'), null);
  const malformed = show({ status: 'completed', answerCompletion: { itemId: 'bad\nidentifier' } });
  assert.equal(malformed.querySelector('[data-answer-phase]'), null);
  const plainLive = show({ content: '流式文字', answerCompletion: answer }, { isStreaming: true });
  assert.equal(plainLive.querySelector('[data-answer-phase]'), null, 'live phase ignores saved evidence');
  assert.ok(plainLive.querySelector('.streaming-cursor'));
});

test('failed or stopped turn states can retain an independently completed model answer', () => {
  for (const status of ['failed', 'stopped', 'unconfirmed']) {
    const document = show({ status, answerCompletion: answer });
    assert.ok(document.querySelector('[data-answer-phase=completed]'));
    assert.ok(document.querySelector(`[data-turn-phase=${status}]`));
    assert.equal(document.querySelector('[data-turn-phase=completed]'), null);
  }
});

test('an explicit answer result does not turn awaiting input, stop or recovery into completion', () => {
  for (const phase of ['waiting_input', 'stopping', 'stop_failed', 'recovering']) {
    const document = show({}, { isStreaming: true, turnLifecycle: phase, answerCompletion: answer });
    assert.ok(document.querySelector('[data-answer-phase=completed]'));
    assert.ok(document.querySelector(`[data-turn-phase=${phase}]`));
    assert.equal(document.querySelector('[data-turn-phase=completed]'), null);
  }
});
