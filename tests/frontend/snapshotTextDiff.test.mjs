import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { parseHTML } = require('linkedom');

test('snapshot diff pairs changed lines and leaves unmatched before lines empty on the other side', () => {
  const View = componentModule('components/SnapshotTextDiff.tsx').default;
  const { projectUnifiedDiff } = componentModule('vendor/deepseek-harness/tool/file-diff.ts', { globals: { TextEncoder } });
  const projection = projectUnifiedDiff('@@ -1,4 +1,3 @@\n shared\n-第一段 <script>\n-第二段\n+新段落\n end\n');
  const document = parseHTML(renderToStaticMarkup(React.createElement(View, { projection }))).document;
  assert.equal(document.querySelector('script'), null);
  assert.match(document.querySelector('[data-diff-side="before"][data-diff-line="del"]').textContent, /第一段 <script>/);
  assert.equal(document.querySelector('[data-diff-side="after"][data-diff-line="add"]').textContent.trim(), '2+新段落');
  assert.equal(document.querySelectorAll('[data-diff-side="after"][data-diff-empty]').length, 1);
  assert.ok(document.querySelector('[data-copy-file-diff]'));
});
