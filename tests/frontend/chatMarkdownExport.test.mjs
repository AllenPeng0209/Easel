import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

test('Markdown export uses literal attachment labels and omits protected paths and tool payloads', () => {
  const { sessionMarkdown } = componentModule('lib/sessionNavigation.ts');
  const markdown = sessionMarkdown({
    title: '测试会话',
    messages: [
      { role: 'user', content: '核对附件', attachments: [
        { name: '[报告](https://example.invalid/collect)\n第二行', path: '/private/only' },
        { name: 'password=' + 'fixture-value', path: '/private/other' },
      ], agentContent: 'injected prompt' },
      { role: 'assistant', content: '已完成', events: [{ kind: 'tool', output: 'private tool output' }] },
    ],
  });
  assert.match(markdown, /附件：`\[报告\]\(https:\/\/example\.invalid\/collect\) 第二行`/);
  assert.match(markdown, /password=\[已隐藏凭据\]/);
  assert.match(markdown, /已完成/);
  assert.doesNotMatch(markdown, /fixture-value|\/private\/|injected prompt|private tool output/);
});
