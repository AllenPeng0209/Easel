import { projectScope, runWithProjectScope } from './projectScopeHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
});

/**
 * streamChat 回归：首发 POST 5xx → jobs 流 404 必须**终结**并触发 onError。
 *
 * 旧行为（bug）：404 分支依赖 onRecoveryUnavailable（新发送不传），不传时静默落入
 * 无限 1s 重试（最长 130 分钟），onError 永不触发，会话卡死在「自动续接」。
 */
function loadApi(fetchImpl) {
  const calls = { fetch: 0, post: 0, jobs: 0, onError: [], onDone: 0, onRecovery: 0, onInterrupted: 0 };
  const module = { exports: {} };
  const sandbox = {
    exports: module.exports,
    require: (name) => name === './projectScope' ? projectScope : require(name),
    console,
    window: { location: { pathname: '/' } },
    AbortController,
    TextDecoder,
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms ?? 0, 2)),
    clearTimeout,
    fetch: (...args) => {
      calls.fetch += 1;
      return fetchImpl(...args);
    },
  };
  runWithProjectScope(outputText, sandbox);
  return { api: module.exports, calls };
}

function jsonResponse(status, detail) {
  return {
    ok: status < 400,
    status,
    json: async () => ({ detail }),
  };
}

function sseStream(status = 200) {
  // 最小可读流：立即结束（consume 只在 !ok 时读 json）
  return { ok: status < 400, status, json: async () => ({ detail: 'noop' }), body: null };
}

test('streamChat: POST 5xx 后 jobs 404 终结并报错（不再无限重试）', async () => {
  const script = [];
  const { api, calls } = loadApi((url, init) => {
    const u = String(url);
    if (init?.method === 'POST' || u.includes('/api/chat/stream')) {
      calls.post += 1;
      script.length === 0 && script.push('post-502');
      return Promise.resolve(jsonResponse(502, '上游网关过载'));
    }
    calls.jobs += 1;
    return Promise.resolve(jsonResponse(404, '任务记录不存在'));
  });

  const events = { error: [], done: 0, interrupted: 0 };
  const controller = api.streamChat(
    'hi', undefined, 'sess-1',
    () => {},                                  // onToken
    () => { events.done += 1; },               // onDone
    (err) => { events.error.push(err.message); },
    undefined, undefined,                       // onThinking / onActivity
    () => { events.interrupted += 1; },         // onInterrupted
    'turn-fixed-1',                            // turnId
    false,                                     // resumeOnly
    undefined,                                 // onRecoveryUnavailable（新发送不传）
  );

  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(calls.post, 1, '首发 POST 只发一次');
  assert.equal(calls.jobs, 1, 'jobs 404 后必须终结，不得继续重试');
  assert.equal(events.error.length, 1, 'onError 必须触发一次');
  assert.match(events.error[0], /任务记录不存在|失效/);
  assert.equal(events.done, 0);
  assert.equal(events.interrupted, 1, '首次 5xx 后应提示中断（续接状态）');
});

test('streamChat: 提供 onRecoveryUnavailable 时仍走回调（恢复语义不变）', async () => {
  const { api, calls } = loadApi((url, init) => {
    const u = String(url);
    if (init?.method === 'POST' || u.includes('/api/chat/stream')) {
      return Promise.resolve(jsonResponse(502, '上游网关过载'));
    }
    return Promise.resolve(jsonResponse(404, '任务记录不存在'));
  });

  const seen = { recovered: 0, error: 0 };
  api.streamChat(
    'hi', undefined, 'sess-2',
    () => {}, () => {},
    () => { seen.error += 1; },
    undefined, undefined, () => {},
    'turn-fixed-2', true,                      // resumeOnly（恢复路径）
    () => { seen.recovered += 1; },            // onRecoveryUnavailable
  );

  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(seen.recovered, 1, '恢复回调必须被调用');
  assert.equal(seen.error, 0, '有回调时不应再走 onError 兜底');
  assert.equal(calls.fetch, 1, '只发一次 jobs 请求后终结');
});

test('terminal SSE preserves the host outcome alongside the existing session key', async () => {
  const { api } = loadApi(() => new Response(
    'event: done\ndata: {"sessionKey":"saved-chat","outcome":"unconfirmed"}\n\n',
    { headers: { 'Content-Type': 'text/event-stream' } },
  ));
  const result = await new Promise((resolve, reject) => {
    api.streamChat('hello', undefined, 'chat', () => {}, (key, outcome) => resolve({ key, outcome }), reject);
  });
  assert.deepEqual(result, { key: 'saved-chat', outcome: 'unconfirmed' });
});

test('queue admission accepts only a receipt for the requested session and turn', async () => {
  const events = [
    { turnId: 'old-turn', sessionId: 'chat' },
    { turnId: 'queue-q1', sessionId: 'other' },
    { turnId: 'queue-q1', sessionId: 'chat' },
  ];
  const { api } = loadApi(() => new Response(
    events.map(data => `event: admitted\ndata: ${JSON.stringify(data)}\n\n`).join('') +
      'event: done\ndata: {"sessionKey":"chat"}\n\n',
  ));
  const received = [];
  await new Promise((resolve, reject) => {
    const args = Array(20).fill(undefined);
    args.splice(0, 6, 'next task', undefined, 'chat', () => {}, resolve, reject);
    args[9] = 'queue-q1';
    args[19] = receipt => received.push(JSON.parse(JSON.stringify(receipt)));
    api.streamChat(...args);
  });
  assert.deepEqual(received, [events[2]]);
});

test('chat renders only validated public summary snapshots from SSE and last-turn recovery', async () => {
  const expected = { text: '引擎公开摘要', truncated: true };
  const encoder = new TextEncoder();
  const events = [
    ['thinking', 'private trace'],
    ['reasoning_summary', { text: 'malformed', truncated: 'yes' }],
    ['reasoning_summary', { ...expected, raw: 'private payload' }],
    ['done', { sessionKey: 'native-session', outcome: 'completed' }],
  ].map(([kind, data]) => `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`).join('');
  const { api } = loadApi((_url, init) => {
    if (init?.method !== 'POST') {
      return { ok: true, json: async () => ({ status: 'done', text: '答复', reasoning_summary: { ...expected, raw: 'private payload' } }) };
    }
    let read = false;
    return { ok: true, body: { getReader: () => ({
      read: async () => read ? { done: true } : (read = true, { done: false, value: encoder.encode(events) }),
      cancel: async () => {},
    }) } };
  });
  const summaries = [];
  await new Promise((resolve, reject) => {
    api.streamChat('hi', undefined, 'summary-session', () => {}, resolve, reject, row => summaries.push(row));
  });
  assert.deepEqual(JSON.parse(JSON.stringify(summaries)), [expected]);
  const recovered = await api.fetchLastTurn('summary-session', 'turn');
  assert.deepEqual(JSON.parse(JSON.stringify(recovered.reasoning_summary)), expected);
});
