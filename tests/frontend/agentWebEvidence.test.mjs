import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const { renderToStaticMarkup } = require('react-dom/server');
const markup = fields => renderToStaticMarkup(React.createElement(
  componentModule('components/AgentEventPanel.tsx').default, {
    expandedCallId: 'web', events: [{ id: 'event', callId: 'web', kind: 'tool', name: 'web_search',
      nativeWebSearch: true, status: 'completed', ...fields }],
  },
));

test('Codex multi-query display preserves request order and distinguishes absent evidence', () => {
  const html = markup({ input: { query: 'main', action: { type: 'search', query: 'one', queries: ['two', 'three'] } } });
  const { document } = parseHTML(html);
  const block = document.querySelector('[data-web-action=search]');
  assert.equal(block.dataset.webEvidence, 'absent');
  assert.deepEqual([...block.querySelectorAll('[data-web-query]')].map(node => node.textContent), ['main', 'one', 'two', 'three']);
  assert.match(block.textContent, /未提供结果证据/);
  assert.doesNotMatch(block.textContent, /命中 0|抓取时间/);
});

test('explicit empty, failed and opaque native results do not invent counts or successful hits', () => {
  const fields = { input: { action: { type: 'search', query: 'hot topic' } } };
  assert.match(markup({ ...fields, output: [] }), /data-web-evidence="empty"/);
  assert.match(markup({ ...fields, output: [], status: 'failed' }), /data-web-evidence="failed"/);
  const hidden = 'untrusted-token';
  const opaque = markup({ ...fields, output: [{ summary: 'record', url: 'https://example.test/opaque', api_key: hidden }] });
  assert.match(opaque, /data-web-evidence="observed"/);
  assert.match(opaque, /引擎结果记录/);
  assert.equal(parseHTML(opaque).document.querySelector('[data-web-evidence] a'), null);
  assert.equal(opaque.includes(hidden), false);
  assert.doesNotMatch(opaque, /命中 1/);
  assert.match(markup({ ...fields, output: null }), /data-web-evidence="absent"/);
  const legacy = markup({ ...fields, nativeWebSearch: undefined, output: [] });
  assert.doesNotMatch(legacy, /data-web-action/);
  assert.match(legacy, /data-tool-value-kind/);
});

test('open and find actions preserve URLs and patterns, linking only safe HTTP(S)', () => {
  const opened = parseHTML(markup({ input: { action: { type: 'openPage', url: 'https://example.test/source?q=word' } } })).document;
  const link = opened.querySelector('[data-web-action=openPage] a');
  assert.equal(link.getAttribute('href'), 'https://example.test/source?q=word');
  assert.equal(link.getAttribute('target'), '_blank');
  assert.match(link.getAttribute('rel'), /noopener/);
  const fixtureUserInfo = 'secret';
  const fixtureQueryValue = 'private-fixture';
  for (const url of ['javascript:alert(1)', `https://alice:${fixtureUserInfo}@example.test/`,
    `https://example.test/?token=${fixtureQueryValue}`, `https://example.test/?access%5Ftoken=${fixtureQueryValue}`,
    'https:////example.test/', 'https://example.test/[REDACTED]']) {
    const html = markup({ input: { action: { type: 'findInPage', url, pattern: '<script>term</script>' } } });
    const { document } = parseHTML(html);
    const block = document.querySelector('[data-web-action=findInPage]');
    assert.match(block.textContent, /<script>term<\/script>/);
    assert.equal(block.querySelector('a'), null);
    assert.equal(block.querySelector('script'), null);
    assert.equal(html.includes(`alice:${fixtureUserInfo}`) || html.includes(fixtureQueryValue), false);
  }
});

test('large stored query arrays stay bounded and report truncation', () => {
  const html = markup({ input: { action: { type: 'search',
    queries: Array.from({ length: 1000 }, (_, index) => `query-${index}-${'x'.repeat(3000)}`),
  } } });
  const { document } = parseHTML(html);
  const queries = [...document.querySelectorAll('[data-web-query]')];
  assert.equal(queries.length, 20);
  assert.ok(queries.every(row => row.textContent.length <= 2000));
  assert.match(html, /引擎记录已截断/);
});
