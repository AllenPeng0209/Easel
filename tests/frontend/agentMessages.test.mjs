import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';
import { randomUUID } from 'node:crypto';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const userMessage = (attachments, content = '') => ({ role: 'user', content, attachments });

test('attachment-only turns remain visible with their file identity after reopening', () => {
  const MessageBubble = componentModule('components/MessageBubble.tsx').default;
  const html = renderToStaticMarkup(React.createElement(MessageBubble, { message: userMessage([
    { id: 'one', name: '创作要求.pdf', path: '_inbox/opaque/batch/创作要求.pdf' },
  ]) }));
  assert.match(html, /创作要求\.pdf/);
  assert.match(html, /\/api\/media\/_inbox\/opaque\/batch\//);
  assert.doesNotMatch(html, /Attachment-only turns/);
});

test('text and attachments render together in selection order without exposing server paths as labels', () => {
  const MessageBubble = componentModule('components/MessageBubble.tsx', { user: 'user1', project: 'ip2' }).default;
  const html = renderToStaticMarkup(React.createElement(MessageBubble, { message: userMessage([
    { id: 'one', name: '第一张.png', path: '_inbox/u1/b/第一张.png' },
    { id: 'two', name: '稿件.txt', path: '_inbox/u1/b/稿件.txt' },
  ], '请参考这些素材') }));
  assert.match(html, /请参考这些素材/);
  assert.match(html, /第一张\.png/);
  assert.match(html, /稿件\.txt/);
  assert.ok(html.indexOf('第一张.png') < html.indexOf('稿件.txt'));
  assert.match(html, /\/api\/projects\/ip2\/media\//);
  assert.doesNotMatch(html, />_inbox\/u1\/b\//);
  assert.match(html, /<img[^>]+alt="第一张.png"/);
});

test('attachment references refuse external, absolute, traversing and control-character paths', () => {
  const { attachmentHref } = componentModule('vendor/deepseek-harness/message/attachment-reference.ts');
  for (const unsafe of ['https://evil.test/a.png', '/etc/passwd', '../secret.txt',
    '_inbox/../private.txt', '_inbox/a%2f..%2fprivate.txt', '_inbox/a\\private.txt', '_inbox/a\u0000.png']) {
    assert.equal(attachmentHref(unsafe), null, unsafe);
  }
  assert.equal(attachmentHref('_inbox/u/b/a b.pdf'), '/api/media/_inbox/u/b/a%20b.pdf');
});

test('tool lifecycle updates form one stable card, preserving result, exit code and parent identity', () => {
  const AgentEventPanel = componentModule('components/AgentEventPanel.tsx').default;
  const html = renderToStaticMarkup(React.createElement(AgentEventPanel, { expandedCallId: 'call-a', events: [
    { id: '1', kind: 'tool', callId: 'call-a', name: 'commandExecution', status: 'running', input: { command: 'pwd' }, parentCallId: 'parent' },
    { id: '2', kind: 'tool', callId: 'call-a', name: 'commandExecution', status: 'completed', output: 'done', exitCode: 0, parentCallId: 'parent' },
  ] }));
  assert.equal((html.match(/data-tool-call="call-a"/g) || []).length, 1);
  assert.match(html, /已完成/);
  assert.match(html, /pwd/);
  assert.match(html, /done/);
  assert.match(html, /退出码/);
  assert.match(html, /data-parent-call="parent"/);
});

test('tool result rendering escapes HTML and conceals sensitive object fields', () => {
  const syntheticCredential = randomUUID();
  const AgentEventPanel = componentModule('components/AgentEventPanel.tsx').default;
  const html = renderToStaticMarkup(React.createElement(AgentEventPanel, { expandedCallId: 'bad', events: [
    { id: '3', kind: 'tool', callId: 'bad', name: 'unknown', status: 'failed',
      input: { api_key: syntheticCredential, query: '<script>alert(1)</script>' },
      output: '<img src=x onerror=alert(1)>', exitCode: 1 },
  ] }));
  assert.match(html, /失败/);
  assert.ok(!html.includes(syntheticCredential));
  assert.doesNotMatch(html, /<script>|<img src=x/);
  assert.match(html, /&lt;img/);
});


test('a permission timeout is shown as unapproved instead of a fabricated human reply', () => {
  const AgentEventPanel = componentModule('components/AgentEventPanel.tsx').default;
  const html = renderToStaticMarkup(React.createElement(AgentEventPanel, { events: [
    { id: 'timeout', kind: 'decision', source: 'timeout', status: 'declined', answers: {} },
  ] }));
  assert.match(html, /未获授权/);
  assert.doesNotMatch(html, /已收到你的答复/);
});

test('clipboard success follows the actual asynchronous host result', async () => {
  let settle;
  const clipboard = componentModule('vendor/deepseek-harness/message/clipboard.ts', { globals: {
    navigator: { clipboard: { writeText: () => new Promise(resolve => { settle = resolve; }) } },
  } });
  let finished = false;
  const pending = clipboard.writeClipboard('稿件').then(result => { finished = true; return result; });
  await Promise.resolve();
  assert.equal(finished, false);
  settle();
  assert.equal(await pending, true);
  const rejected = componentModule('vendor/deepseek-harness/message/clipboard.ts', { globals: {
    navigator: { clipboard: { writeText: async () => { throw new Error('permission denied'); } } },
  } });
  assert.equal(await rejected.writeClipboard('稿件'), false);
});

test('a settled turn with no tool terminal event shows an unconfirmed result without inventing completion', () => {
  const AgentEventPanel = componentModule('components/AgentEventPanel.tsx').default;
  const html = renderToStaticMarkup(React.createElement(AgentEventPanel, { settled: true, expandedCallId: 'external', events: [
    { id: 'started', kind: 'tool', callId: 'external', name: 'render_media', status: 'running', output: '已提交 task-123' },
  ] }));
  assert.match(html, /未收到工具终态/);
  assert.match(html, /task-123/);
  assert.doesNotMatch(html, /data-shimmer="true"/);
  assert.doesNotMatch(html, /已完成|已取消|执行中/);
});

test('recorded plan snapshots retain a single checklist and auditable revision history after replay', () => {
  const MessageBubble = componentModule('components/MessageBubble.tsx', {
    globals: { DOMParser: class { parseFromString(html) { return { body: { innerHTML: html }, querySelectorAll: () => [] }; } } },
    externals: { dompurify: { sanitize: value => value } },
  }).default;
  const step = (step, status, assignee) => ({ step, status, assignee });
  const events = [
    { id: 'p1', kind: 'plan', steps: [step('选题', 'inProgress'), step('脚本', 'pending'), step('审核', 'pending')] },
    { id: 'p2', kind: 'plan', steps: [step('脚本', 'inProgress', '创作 Agent'), step('选题', 'completed'), step('成片', 'pending')] },
    { id: 'p3', kind: 'plan', truncated: true, steps: [step('成片', 'completed')] },
    { id: 'prose', kind: 'plan', callId: 'prose', status: 'completed', text: '任务都已完成' },
  ];
  const html = renderToStaticMarkup(React.createElement(MessageBubble, {
    message: { role: 'assistant', content: '成片已完成', events },
  }));
  assert.equal((html.match(/aria-label="任务清单"/g) || []).length, 1);
  assert.match(html, /1\/3 已完成/);
  assert.doesNotMatch(html, /创作 Agent/);
  assert.match(html, /执行者未报告/);
  assert.match(html, /变更记录/);
  for (const label of ['顺序调整', '状态变更', '新增', '移除', '清单更新不完整']) assert.ok(html.includes(label), label);
});

test('plan history never borrows baselines across turns or treats an empty snapshot as missing', () => {
  const Panel = componentModule('components/AgentEventPanel.tsx').default;
  const html = renderToStaticMarkup(React.createElement(Panel, { events: [
    { id: 'a', kind: 'plan', turnId: 'a', steps: [{ step: '旧任务', status: 'completed' }] },
    { id: 'b', kind: 'plan', turnId: 'b', steps: [{ step: '新任务', status: 'pending' }] },
    { id: 'c', kind: 'plan', turnId: 'b', steps: [] },
  ] }));
  assert.equal((html.match(/aria-label="任务清单"/g) || []).length, 2);
  assert.match(html, /清单已清空/);
  assert.match(html, /移除[^<]*新任务/);
  assert.doesNotMatch(html, /移除[^<]*旧任务/);
});
