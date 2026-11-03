import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { parseHTML } = require('linkedom');

const show = (budget, extra = {}) => {
  const Meter = componentModule('components/ContextBudgetMeter.tsx').default;
  return parseHTML(renderToStaticMarkup(React.createElement(Meter, { budget, ...extra }))).document;
};

test('shows exact adapter usage, capacity and source in an accessible meter', () => {
  const document = show({ usedTokens: 24_000, contextWindow: 100_000, percent: 24, source: 'codex', freshness: 'live' });
  const meter = document.querySelector('[role="meter"]');
  assert.equal(meter.getAttribute('aria-valuenow'), '24');
  assert.match(meter.getAttribute('aria-valuetext'), /24,000.*100,000/);
  assert.match(document.querySelector('[data-context-budget]').textContent, /24,000.*100,000.*Codex.*本轮/);
  assert.match(document.querySelector('[data-context-window-remainder]').textContent, /76,000.*未计当前草稿/);
  assert.equal(document.querySelector('[data-context-new-chat]'), null);
});

test('shows unknown independently without inventing percentage or remaining tokens', () => {
  const partial = show({ usedTokens: 560, source: 'acp', freshness: 'settled' });
  assert.equal(partial.querySelector('[role="meter"]'), null);
  assert.equal(partial.querySelector('[data-context-window-remainder]'), null);
  assert.match(partial.querySelector('[data-context-budget]').textContent, /560.*容量未知.*ACP.*最近/);
  const missing = show({});
  assert.match(missing.querySelector('[data-context-budget]').textContent, /用量未知.*容量未知/);
  assert.equal(missing.querySelector('[data-context-new-chat]'), null);
});

test('high occupancy suggests an explicit new chat without claiming compaction', () => {
  const document = show({ usedTokens: 96, contextWindow: 100, percent: 96, source: 'codex', freshness: 'settled' },
    { onNewChat() {} });
  assert.match(document.querySelector('[data-context-budget-warning]').textContent, /接近上限/);
  assert.match(document.querySelector('[data-context-new-chat]').textContent, /新建对话/);
  assert.equal(document.querySelector('[data-context-compact]'), null);
});

test('a percentage rounded to 100 does not claim the exact window is exhausted', () => {
  const document = show({ usedTokens: 996, contextWindow: 1000, percent: 100, source: 'codex' });
  assert.match(document.querySelector('[data-context-budget-warning]').textContent, /接近上限/);
  assert.match(document.querySelector('[data-context-window-remainder]').textContent, /4 tokens/);
});
