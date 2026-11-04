import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { parseHTML } = require('linkedom');

const tool = (name, fields = {}) => ({ id: name, callId: name, kind: 'tool', runId: 'run', status: 'completed', name, ...fields });
const patch = tool('apply_patch', { fileChangeApplied: true,
  input: { changes: [{ path: 'notes.md', diff: '@@ -1 +1 @@\n-before\n+after\n' }] } });
const command = tool('bash', { outputStream: 'merged', input: { command: 'printf ok' }, output: 'ok' });
const web = tool('web_search', { nativeWebSearch: true, input: { action: { type: 'search', query: 'example' } } });
const panel = options => componentModule('components/AgentEventPanel.tsx', options).default;

test('only exact registry keys with their validated data can select specialized tool views', () => {
  const Panel = panel({ globals: { TextEncoder, TextDecoder } });
  const { document } = parseHTML(renderToStaticMarkup(React.createElement(Panel, { events: [
    command, patch, web,
    tool('shell-alias', { ...command, id: 'wrong', callId: 'wrong', name: 'shell-alias' }),
    tool('bash', { ...command, id: 'malformed', callId: 'malformed', input: { command: 5 } }),
    tool('forged-web', { nativeWebSearch: true, input: web.input }),
    tool('imported', { name: { not: 'a string' } }),
    tool('__proto__'), tool('constructor'), tool('easel_publish'), tool('easel_business_objects'),
  ] })));
  const byCall = callId => document.querySelector(`[data-tool-call="${callId}"]`);
  for (const id of ['bash', 'apply_patch', 'web_search']) assert.equal(byCall(id).dataset.toolRenderer, id);
  for (const id of ['wrong', 'malformed', 'forged-web', 'imported', '__proto__', 'constructor', 'easel_publish', 'easel_business_objects']) {
    assert.equal(byCall(id).dataset.toolRenderer, 'generic');
    assert.ok(byCall(id).dataset.chatAnchorKey);
  }
});

test('a failed registered projection falls back without accepting other names or event kinds', () => {
  const { createToolRendererRegistry } = componentModule('components/toolRendererRegistry.ts');
  const project = () => { throw new Error('projection exception'); };
  const resolve = createToolRendererRegistry([{ key: 'test', names: ['test'], project }]);
  assert.equal(resolve(tool('test'), false).failed, true);
  assert.equal(resolve(tool('test', { kind: 'business' }), false).failed, false);
  assert.equal(resolve(tool('Test'), false).failed, false);
});

test('a failing specialized card keeps the row, nested child and siblings mounted, then retries on a new observation', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const caught = [];
  const root = require('react-dom/client').createRoot(document.getElementById('root'), { onCaughtError: error => caught.push(error) });
  try {
    // The parser genuinely throws in this isolated renderer; other row bodies use no TextEncoder.
    const Panel = panel();
    const child = tool('child', { parentCallId: patch.callId });
    const render = event => React.act(() => root.render(React.createElement(Panel, {
      events: [event, child, command], expandedCallId: patch.callId,
    })));
    await render(patch);
    const card = document.querySelector('[data-tool-call=apply_patch]');
    assert.ok(card.querySelector('[data-tool-renderer-fallback]'));
    assert.match(card.textContent, /notes\.md/);
    assert.ok(card.querySelector('[data-tool-call=child]'));
    assert.equal(document.querySelector('[data-tool-call=bash]').dataset.toolRenderer, 'bash');
    assert.ok(caught.length >= 1);
    await render({ ...patch, id: 'next-observation', input: { changes: [{ path: 'notes.md' }] } });
    assert.equal(document.querySelector('[data-tool-call=apply_patch]'), card);
    assert.equal(card.querySelector('[data-tool-renderer-fallback]'), null);
    assert.match(card.textContent, /未提供差异/);
  } finally {
    await React.act(() => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
