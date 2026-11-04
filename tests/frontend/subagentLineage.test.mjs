import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');

const handle = id => id === '../invalid' ? id : createHash('sha256').update(id).digest('hex').slice(0, 32);
const event = (id, status, parentId, turnId = 'turn-1', seq = 1, rootSessionId = 'session-1') => ({
  id: 'event-' + id + '-' + seq, kind: 'subagent', turnId,
  subagent: { id: handle(id), parentId: parentId ? handle(parentId) : null, title: 'Worker ' + id,
    status, rootSessionId, originTurnId: 'turn-1', createdAt: 1, updatedAt: seq },
});

test('live event reduction accepts scoped snapshots without regressing observed child state', () => {
  const { reduceAgentEvents } = componentModule('lib/agentEvents.ts');
  const running = event('child', 'running', 'root');
  const completed = event('child', 'completed', 'root', 'turn-1', 3);
  let state = reduceAgentEvents([], running);
  state = reduceAgentEvents(state, completed);
  assert.equal(state.length, 1);
  assert.equal(state[0].subagent.status, 'completed');
  assert.equal(reduceAgentEvents(state, event('child', 'failed', 'root', 'turn-1', 2)), state);
  assert.equal(reduceAgentEvents(state, event('child', 'running', 'root', 'turn-1', 2)), state);
  assert.equal(reduceAgentEvents(state, event('child', 'running', 'root', 'turn-1', 4))[0].subagent.status, 'running');
  assert.equal(reduceAgentEvents(state, event('../invalid', 'running')), state);
  assert.equal(reduceAgentEvents(state, event('child', 'failed', 'different-parent', 'turn-1', 5)), state);
  const later = reduceAgentEvents(state, event('child', 'running', 'root', 'turn-2', 6));
  assert.equal(later.length, 2);
});

async function view(fetch, verify) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, {
    window: { value: window, configurable: true, writable: true },
    document: { value: document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true },
  });
  const parent = document.getElementById('root');
  const root = require('react-dom/client').createRoot(parent);
  try {
    const options = { user: 'owner', project: 'ip-one', globals: { fetch, Headers } };
    const Panel = componentModule('components/AgentEventPanel.tsx', options).default;
    const { SubagentTranscript } = componentModule('components/AgentSubagentLineage.tsx', options);
    const render = (events, sessionId = 'session-1', settled = false, onOpenSubagent) => React.act(async () =>
      root.render(React.createElement(Panel, { events, sessionId, settled, onOpenSubagent })));
    const transcript = (sessionId, child, onOpenSubagent) => React.act(async () =>
      root.render(React.createElement(SubagentTranscript, { sessionId, child, onOpenSubagent })));
    const click = node => React.act(async () => {
      assert.ok(node);
      node.click();
    });
    const row = id => parent.querySelector('[data-subagent-id="' + handle(id) + '"]');
    const expand = id => click(row(id)?.querySelector('[aria-expanded]'));
    await verify({ render, transcript, parent, row, expand, click });
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
}

test('lineage deduplicates replay, distinguishes terminal evidence, and reads only the scoped child', async () => {
  const calls = [];
  await view(async (url, options) => {
    calls.push({ url, options });
    return Response.json({ messages: [{ role: 'assistant', content: '<script>literal</script>真实子会话' }] });
  }, async ({ render, parent, row, expand }) => {
    await render([event('root', 'running'), event('child', 'running', 'root'), event('root', 'completed', undefined, 'turn-1', 3),
      event('root', 'failed', undefined, 'turn-1', 2),
      event('../invalid', 'running'), event('other', 'running', undefined, 'turn-1', 1, 'another-session')], 'session-1', true);
    assert.equal(parent.querySelectorAll('[data-subagent-id]').length, 2);
    assert.ok(row('root').contains(row('child')));
    assert.equal(row('root').getAttribute('data-subagent-status'), 'completed');
    assert.equal(row('child').getAttribute('data-subagent-status'), 'unconfirmed');
    assert.equal(calls.length, 0);
    await expand('child');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/projects/ip-one/chat/subagents/session-1/' + handle('child'));
    assert.equal(calls[0].options.headers.get('X-Easel-User'), 'owner');
    assert.match(row('child').textContent, /真实子会话/);
    assert.ok(!row('child').querySelector('script, textarea, form'));
  });
});

test('a replaced session aborts and ignores the old response', async () => {
  const calls = [];
  await view((url, options) => new Promise(resolve => calls.push({ url, signal: options.signal, resolve })), async ui => {
    await ui.render([event('same', 'running')]);
    await ui.expand('same');
    await ui.render([event('same', 'running', undefined, 'turn-1', 1, 'session-2')], 'session-2');
    assert.equal(calls[0].signal.aborted, true);
    await ui.expand('same');
    await React.act(async () => {
      calls[1].resolve(Response.json({ messages: [{ role: 'assistant', content: '第二会话' }] }));
      calls[0].resolve(Response.json({ messages: [{ role: 'assistant', content: '旧会话不应出现' }] }));
    });
    assert.match(ui.parent.textContent, /第二会话/);
    assert.doesNotMatch(ui.parent.textContent, /旧会话不应出现/);
  });
});

test('a failed child read can be retried without running or editing the child', async () => {
  let reads = 0;
  await view(async (_url, { method }) => {
    assert.equal(method, 'GET');
    return ++reads === 1 ? new Response('', { status: 404 }) : Response.json({ messages: [] });
  }, async ui => {
    await ui.render([event('child', 'running')]);
    assert.match(ui.parent.textContent, /1 个运行中/);
    await ui.expand('child');
    assert.match(ui.parent.textContent, /无法读取子会话/);
    await ui.click(Array.from(ui.parent.querySelectorAll('button')).find(button => button.textContent === '重试'));
    assert.match(ui.parent.textContent, /暂无消息/);
    assert.equal(reads, 2);
  });
});

test('side pane hands focus ownership to the caller and lists only children bound to the current branch', async () => {
  const opened = [];
  const calls = [];
  const child = event('child', 'completed').subagent;
  await view(async url => {
    calls.push(url);
    return Response.json({
      messages: [{ role: 'assistant', content: '原生消息' }], hasMore: true,
      children: [event('nested', 'queued', 'child').subagent,
        event('foreign', 'completed', 'child', 'turn-1', 1, 'foreign-session').subagent,
        event('wrong-parent', 'completed', 'other').subagent],
    });
  }, async ui => {
    const onOpenSubagent = (selected, trigger) => opened.push({ selected, trigger });
    await ui.render([event('child', 'completed')], 'session-1', true, onOpenSubagent);
    const trigger = ui.row('child').querySelector('button[aria-label^="查看子会话"]');
    await ui.click(trigger);
    assert.equal(opened[0].selected.id, child.id);
    assert.equal(opened[0].trigger, trigger);
    assert.equal(calls.length, 0);
    await ui.transcript('foreign-session', child, onOpenSubagent);
    assert.equal(calls.length, 0);
    await ui.transcript('session-1', child, onOpenSubagent);
    assert.equal(calls.length, 1);
    assert.match(ui.parent.textContent, /当前仅显示最近的消息/);
    assert.match(ui.parent.textContent, /原生消息/);
    assert.ok(ui.row('nested'));
    assert.ok(!ui.row('foreign'));
    assert.ok(!ui.row('wrong-parent'));
  });
});
