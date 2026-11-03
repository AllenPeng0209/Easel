import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { parseHTML } = require('linkedom');

const tool = (id, callId, fields = {}) => ({ id, callId, kind: 'tool', name: callId, runId: 'run-a', ...fields });
const render = events => parseHTML(renderToStaticMarkup(React.createElement(
  componentModule('components/AgentEventPanel.tsx').default, { events },
))).document;
const rows = document => [...document.querySelectorAll('[data-tool-call]')];
const find = (document, callId, runId) => rows(document).find(row =>
  row.dataset.toolCall === callId && (runId === undefined || row.dataset.runId === runId));
const parent = row => row.parentElement.closest('[data-tool-call]');

test('child-before-parent and nested siblings retain scope, order and independent statuses', () => {
  const document = render([
    tool('child-first', 'voice', { parentCallId: 'media', status: 'running' }),
    tool('foreign', 'media', { runId: 'run-b' }),
    { id: 'note', kind: 'task' },
    tool('root', 'media', { status: 'running' }),
    tool('subtitles', 'subtitle', { parentCallId: 'media', status: 'running' }),
    tool('nested', 'mix', { parentCallId: 'voice', status: 'running' }),
    tool('voice-done', 'voice', { status: 'completed' }),
  ]);
  const media = find(document, 'media', 'run-a');
  const voice = find(document, 'voice');
  const subtitles = find(document, 'subtitle');
  assert.equal(parent(voice), media);
  assert.equal(parent(subtitles), media);
  assert.equal(parent(find(document, 'mix')), voice);
  assert.equal(parent(find(document, 'media', 'run-b')), null);
  assert.equal(voice.dataset.toolStatus, 'completed');
  assert.equal(subtitles.dataset.toolStatus, 'running');
  assert.deepEqual(rows(document).map(row => row.dataset.toolCall), ['media', 'media', 'voice', 'mix', 'subtitle']);
  assert.equal(new Set(rows(document).map(row => row.dataset.chatAnchorKey)).size, 5);
});

test('mixed live and persisted event order preserves terminal state and fills missing parent', () => {
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const events = [
    tool('end', 'voice', { status: 'completed', output: 'voiceover' }),
    tool('parent', 'media', { status: 'running' }),
    tool('start', 'voice', { status: 'running', parentCallId: 'media', input: 'script' }),
  ];
  const live = events.reduce(reduceAgentEvents, []);
  assert.equal(live[0].parentCallId, 'media');
  assert.equal(live[0].status, 'completed');
  assert.equal(live[0].input, 'script');
  const restored = render(events);
  const current = render(live);
  assert.equal(parent(find(current, 'voice')).dataset.toolCall, 'media');
  assert.equal(parent(find(restored, 'voice')).dataset.toolCall, 'media');
  assert.equal(find(current, 'voice').dataset.chatAnchorKey, find(restored, 'voice').dataset.chatAnchorKey);
});

test('legacy turn boundaries, self edges, cycles and missing parents do not hide or misattribute calls', () => {
  const legacy = (id, callId, turnId, extra = {}) => tool(id, callId, { runId: undefined, turnId, ...extra });
  const document = render([
    legacy('same', 'parent', 'legacy-a'),
    legacy('same', 'parent', 'legacy-b'),
    legacy('child', 'voice', 'legacy-a', { parentCallId: 'parent' }),
    tool('orphan', 'orphan', { runId: 'unrelated', parentCallId: 'parent' }),
    tool('a', 'a', { parentCallId: 'b' }),
    tool('b', 'b', { parentCallId: 'a' }),
    tool('self', 'self', { parentCallId: 'self' }),
  ]);
  assert.equal(rows(document).length, 7);
  const [legacyA, legacyB] = rows(document).filter(row => row.dataset.toolCall === 'parent');
  assert.equal(parent(find(document, 'voice')), legacyA);
  assert.notEqual(legacyA.dataset.chatAnchorKey, legacyB.dataset.chatAnchorKey);
  assert.equal(parent(find(document, 'orphan')), null);
  assert.equal(parent(find(document, 'self')), null);
  assert.equal(parent(find(document, 'a')), find(document, 'b'));
  assert.equal(parent(find(document, 'b')), null);
});

test('a chain past the pinned depth limit splits into roots without dropping entries', () => {
  const { projectToolCallTree } = componentModule('vendor/deepseek-harness/tool/tool-call-tree.ts');
  const { calls, parents } = projectToolCallTree(Array.from({ length: 258 }, (_, index) => ({
    key: String(index), parentKey: index ? String(index - 1) : undefined, value: index,
  })));
  assert.equal(calls.size, 258);
  assert.equal(parents.get('255'), '254');
  assert.equal(parents.get('256'), undefined);
  assert.equal(parents.get('257'), '256');
});

test('modern event IDs still deduplicate conflicting tool calls within the same run', () => {
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const state = [tool('same', 'first')];
  assert.equal(reduceAgentEvents(state, tool('same', 'conflicting')), state);
});

test('an existing row opens for late children and honors a subsequent explicit collapse', async () => {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const descriptors = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const Panel = componentModule('components/AgentEventPanel.tsx').default;
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  const update = events => React.act(() => root.render(React.createElement(Panel, { events })));
  const media = tool('root', 'media', { status: 'running' });
  const voice = tool('voice', 'voice', { parentCallId: 'media', status: 'running' });
  const toggle = () => find(document, 'media').querySelector('[data-disclosure-row]');
  try {
    await update([media]);
    assert.equal(toggle().getAttribute('aria-expanded'), 'false');
    await update([media, voice]);
    assert.equal(toggle().getAttribute('aria-expanded'), 'true');
    assert.equal(parent(find(document, 'voice')).dataset.toolCall, 'media');
    await React.act(() => toggle().dispatchEvent(new window.Event('click', { bubbles: true })));
    await update([media, { ...voice, status: 'completed' }]);
    assert.equal(toggle().getAttribute('aria-expanded'), 'false');
    assert.equal(find(document, 'voice'), undefined);
    await React.act(() => toggle().dispatchEvent(new window.Event('click', { bubbles: true })));
    assert.equal(find(document, 'voice').dataset.toolStatus, 'completed');
  } finally {
    await React.act(() => root.unmount());
    for (const [name, descriptor] of Object.entries(descriptors)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
