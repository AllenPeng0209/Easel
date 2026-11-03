import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const path = 'vendor/deepseek-harness/markdown/incremental.ts';

test('freezes completed blocks with stable identities and only sanitizes the frontier on append', () => {
  const { IncrementalMarkdownRenderer } = componentModule(path);
  const rendered = [];
  const renderer = new IncrementalMarkdownRenderer({ sanitize: html => { rendered.push(html); return html; } });
  const initial = renderer.update('First paragraph\r\n\r\n- first\r\n\r\nGrowing');
  const heading = initial[0];
  rendered.length = 0;
  const next = renderer.update('First paragraph\r\n\r\n- first\r\n- second\r\n\r\nGrowing\r\n\r\n| A | B |\r\n| - | - |\r\n| 1 | 2 |');
  // This is a non-append edit: it must not reuse any old stable keys.
  assert.notEqual(next[0].key, heading.key);
  const stable = next[0];
  rendered.length = 0;
  const later = renderer.update('First paragraph\r\n\r\n- first\r\n- second\r\n\r\nGrowing\r\n\r\n| A | B |\r\n| - | - |\r\n| 1 | 2 |\r\n| 3 | 4 |\r\n\r\n```js\r\nconst x = 1\r\n```');
  assert.equal(later[0], stable);
  assert.ok(rendered.every(html => !html.includes('First paragraph')));
  assert.match(later.map(block => block.html).join(''), /<li>second<\/li>[\s\S]+<td>4<\/td>[\s\S]+const x = 1/);
});

test('prior definitions persist into appended links, and final parse repairs a late definition', () => {
  const { IncrementalMarkdownRenderer } = componentModule(path);
  const renderer = new IncrementalMarkdownRenderer({ sanitize: html => html });
  const prefix = '[early]: https://example.org/early\n\n[Late][late]\n\nA\n\nB';
  assert.match(renderer.update(prefix)[0].html, /\[Late\]\[late\]/);
  const source = prefix + '\n\n[late]: https://example.org/late\n\n[Early][early]';
  const incremental = renderer.update(source);
  assert.match(incremental.at(-1).html, /href="https:\/\/example.org\/early"/);
  assert.doesNotMatch(incremental[0].html, /href=/);
  const settled = renderer.update(source, true);
  assert.match(settled[0].html, /href="https:\/\/example.org\/late"/);
  assert.equal(settled[0].key, incremental[0].key);
});

test('raw HTML is sent through the sanitizer in one mutable tail', () => {
  const { IncrementalMarkdownRenderer } = componentModule(path);
  const inputs = [];
  const renderer = new IncrementalMarkdownRenderer({ sanitize: html => { inputs.push(html); return '[clean]'; } });
  const result = renderer.update('A\n\n<div>\n\ninside\n\n</div>');
  assert.equal(result.length, 1);
  assert.equal(result[0].html, '[clean]');
  assert.match(inputs[0], /<div>[\s\S]+inside[\s\S]+<\/div>/);
});


test('settled link rewriting keeps identical prefix nodes at their original offsets', () => {
  const { IncrementalMarkdownRenderer } = componentModule(path);
  const renderer = new IncrementalMarkdownRenderer({ sanitize: html => html });
  const before = renderer.update('A\n\nB\n\noutput.txt');
  const settled = renderer.update('A\n\nB\n\n[output.txt](https://example.org/)', true);
  assert.equal(settled[0], before[0]);
  assert.equal(settled[1], before[1]);
  assert.match(settled[2].html, /<a href=/);
});

test('keeps inline HTML tokens in one sanitized slice when tags can span blocks', () => {
  const { IncrementalMarkdownRenderer } = componentModule(path);
  const renderer = new IncrementalMarkdownRenderer({ sanitize: html => html.replace(/<script[^>]*>[\s\S]*?<\/script>/giu, '') });
  const source = 'First <b>\n\nSecond</b>\n\nThird';
  const live = renderer.update(source);
  assert.equal(live.length, 1);
  assert.match(live[0].html, /<b>[\s\S]+Second<\/b>/);
  const settled = renderer.update(source + '\n\n<script>alert(1)</script>', true);
  assert.equal(settled.length, 1);
  assert.doesNotMatch(settled[0].html, /script|alert/);
});
