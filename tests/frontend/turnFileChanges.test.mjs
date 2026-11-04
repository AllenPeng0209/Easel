import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { parseHTML } = require('linkedom');

const observed = {
  total: 3, coverage: 'partial', warnings: ['shared_workspace', 'scan_limit'],
  files: [
    { path: 'notes/script.md', kind: 'modified', diffStatus: 'text', added: 8, deleted: 3 },
    { path: 'notes/cover.png', kind: 'added', diffStatus: 'binary' },
    { path: 'notes/large.txt', kind: 'deleted', diffStatus: 'oversized' },
  ],
};

const show = (changes, status = 'completed') => {
  const View = componentModule('components/TurnFileChanges.tsx').default;
  return parseHTML(renderToStaticMarkup(React.createElement(View, { changes, status }))).document;
};

test('turn-tail summary distinguishes exact text counts from binary and oversized files', () => {
  const document = show(observed, 'stopped');
  const card = document.querySelector('[data-turn-file-changes]');
  assert.match(card.textContent, /观察到 3 个文件变更/);
  assert.match(card.querySelector('[data-change-turn-outcome]').textContent, /已停止/);
  assert.match(card.querySelector('[data-change-coverage]').textContent, /部分.*扫描.*限制/s);
  assert.match(card.textContent, /共享工作区/);
  const rows = [...card.querySelectorAll('[data-changed-file]')];
  assert.deepEqual(rows.map(row => row.dataset.changeKind), ['modified', 'added', 'deleted']);
  assert.match(rows[0].textContent, /script\.md.*修改.*\+8.*−3/s);
  assert.match(rows[1].textContent, /二进制.*不计行数/);
  assert.match(rows[2].textContent, /超出统计限制/);
  assert.equal(card.querySelector('a[href]'), null);
});

test('complete empty observation renders nothing; partial empty explains missing evidence', () => {
  assert.equal(show({ total: 0, files: [], coverage: 'complete', warnings: [] })
    .querySelector('[data-turn-file-changes]'), null);
  const document = show({ total: 0, files: [], coverage: 'partial', warnings: ['unreadable'] }, 'unconfirmed');
  assert.equal(document.querySelectorAll('[data-changed-file]').length, 0);
  assert.match(document.querySelector('[data-turn-file-changes]').textContent, /无法确认文件变更.*未确认/s);
  assert.doesNotMatch(document.querySelector('[data-turn-file-changes]').textContent, /0 个文件变更/);
});

test('assistant renders only the settled typed snapshot after the reply, without a generic tool row', () => {
  const { window, document } = parseHTML('<html><body></body></html>');
  const Bubble = componentModule('components/MessageBubble.tsx', {
    externals: { dompurify: { sanitize: text => text } },
    globals: { window: { ...window, location: { pathname: '/', origin: 'http://127.0.0.1:7860' } },
      document, DOMParser: class { parseFromString(html) { return parseHTML(`<html><body>${html}</body></html>`).document; } },
    },
  }).default;
  const event = { id: 'change-event', runId: 'run-a', turnId: 'turn-a',
    kind: 'changes', source: 'outputs_snapshot', status: 'completed', changes: observed };
  const markup = live => renderToStaticMarkup(React.createElement(Bubble, {
    message: { role: 'assistant', content: '本轮回答正文', events: [event], status: 'completed' },
    events: [event], isStreaming: live,
  }));
  const final = markup(false);
  assert.match(final, /本轮回答正文[\s\S]+data-turn-file-changes/);
  assert.equal(parseHTML(final).document.querySelectorAll('[data-turn-file-changes]').length, 1);
  assert.doesNotMatch(final, /任务状态/);
  assert.doesNotMatch(markup(true), /data-turn-file-changes/);
});
