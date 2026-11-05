import { projectScope, runWithProjectScope } from './projectScopeHarness.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const ts = require('typescript');
const source = readFileSync(new URL('../../web/frontend/src/lib/api.ts', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('api.ts', source, ts.ScriptTarget.Latest, true);
const declaration = parsed.statements.find(node =>
  ts.isFunctionDeclaration(node) && node.name?.text === 'streamChat');
assert.ok(declaration);
// Exercise the real API helpers and the stream's complete parser dependencies.
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
    BASE: '',
    require: (name) => name === './projectScope' ? projectScope : require(name),
    console,
    window: { location: { pathname: '/' } },
    AbortController,
    TextDecoder,
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms ?? 0, 2)),
    clearTimeout,
    fetch: (...args) => {
      calls.fetch += 1;
      return Promise.resolve(fetchImpl(...args)).then(response => {
        if (!response.headers) response.headers = new Headers();
        return response;
      });
    },
  };
  runWithProjectScope(outputText, sandbox);
  return { api: module.exports, calls };
}

function jsonResponse(status, detail) {
  return new Response(JSON.stringify({ detail }), { status });
}

function sseStream(status = 200) {
  // 最小可读流：立即结束（consume 只在 !ok 时读 json）
  return new Response(null, { status });
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
    args[18] = receipt => received.push(JSON.parse(JSON.stringify(receipt)));
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

test('explicit final answer evidence arrives independently of turn completion', async () => {
  const encoder = new TextEncoder();
  const receipt = { itemId: 'final-item-1' };
  const events = [
    ['token', '正文'],
    ['answer_completed', { itemId: 123 }],
    ['answer_completed', { ...receipt, text: 'strip unrelated fields' }],
    ['done', { sessionKey: 'native-session', outcome: 'completed' }],
  ].map(([kind, data]) => `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`).join('');
  const { api } = loadApi((_url, init) => {
    if (init?.method !== 'POST') {
      return { ok: true, json: async () => ({ status: 'done', text: '正文', answer_completed: { ...receipt, text: 'strip' } }) };
    }
    let read = false;
    return { ok: true, body: { getReader: () => ({
      read: async () => read ? { done: true } : (read = true, { done: false, value: encoder.encode(events) }),
      cancel: async () => {},
    }) } };
  });
  const observations = [];
  await new Promise((resolve, reject) => {
    const args = ['hi', undefined, 'answer-session', () => observations.push('token'),
      () => { observations.push('turn-done'); resolve(); }, reject];
    args[23] = value => observations.push(JSON.parse(JSON.stringify(value)));
    api.streamChat(...args);
  });
  assert.deepEqual(observations, ['token', receipt, 'turn-done']);
  const recovered = await api.fetchLastTurn('answer-session', 'turn');
  assert.deepEqual(JSON.parse(JSON.stringify(recovered.answer_completed)), receipt);
});

test('live errors and last-turn recovery expose only the allowlisted failure contract', async () => {
  const expected = { code: 'authentication', retryable: false, phase: 'execution' };
  const rawProviderText = 'provider diagnostic with hidden account credential';
  for (const payload of [{ ...expected, message: rawProviderText }, rawProviderText]) {
    const encoder = new TextEncoder();
    const events = `event: error\ndata: ${JSON.stringify(payload)}\n\n`;
    const { api } = loadApi((_url, init) => {
      if (init?.method !== 'POST') {
        return { ok: true, json: async () => ({ status: 'done', text: '', error: payload }) };
      }
      let read = false;
      return { ok: true, body: { getReader: () => ({
        read: async () => read ? { done: true } : (read = true, { done: false, value: encoder.encode(events) }),
        cancel: async () => {},
      }) } };
    });
    const err = await new Promise((resolve, reject) => {
      api.streamChat('hi', undefined, 'failure-session', () => {}, reject, resolve);
    });
    const failure = payload === rawProviderText ? { code: 'unknown', retryable: false, phase: 'execution' } : expected;
    assert.deepEqual(JSON.parse(JSON.stringify(err.failure)), failure);
    assert.doesNotMatch(err.message, /provider diagnostic/);
    const recovered = await api.fetchLastTurn('failure-session', 'turn');
    assert.deepEqual(JSON.parse(JSON.stringify(recovered.error)), failure);
  }
});

test('a failed stream retains later scoped receipts before reporting its terminal error', async () => {
  const receipt = { id: 'publication-receipt', kind: 'publication' };
  const rows = [
    ['error', { code: 'network', retryable: false, phase: 'execution' }],
    ['agent_event', receipt],
    ['done', { outcome: 'failed' }],
  ].map(([event, data], i) => `id: ${i + 1}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  const encoder = new TextEncoder();
  let next = 0;
  const { api } = loadApi(() => ({ ok: true, body: { getReader: () => ({
    read: async () => next < rows.length ? { done: false, value: encoder.encode(rows[next++]) } : { done: true },
  }) } }));
  const observed = [];
  const error = await new Promise((resolve, reject) => {
    const args = ['hi', undefined, 'receipt-session', () => {}, reject, resolve];
    args[16] = row => observed.push(row.id);
    api.streamChat(...args);
  });
  assert.equal(error.failure.code, 'network');
  assert.deepEqual(observed, [receipt.id]);
});

test('chat admission rejection keeps HTTP status but discards provider diagnostic text', async () => {
  const { api } = loadApi(() => jsonResponse(400, 'private upstream diagnostic'));
  const error = await new Promise((resolve, reject) => {
    api.streamChat('hi', undefined, 'receipt-session', () => {}, reject, resolve);
  });
  assert.equal(error.status, 400);
  assert.doesNotMatch(error.message, /private upstream diagnostic/);
});

test('approval answer success requires the exact authoritative server receipt', async () => {
  const request = { questionId: 'exact-question', sessionId: 'chat-1', answers: { approval: ['approve'] } };
  for (const data of [
    { ok: true },
    { ok: true, receipt: { id: 'different-question', status: 'resolved' } },
    { ok: true, receipt: { id: 'exact-question', status: 'pending' } },
  ]) {
    const { api } = loadApi(() => ({ ok: true, status: 200, json: async () => data }));
    assert.equal((await api.answerQuestion(request)).ok, false);
  }
  const receipt = { id: 'exact-question', status: 'resolved', answers: { approval: ['decline'] }, resolvedBy: 'human' };
  const { api } = loadApi(() => ({ ok: true, status: 200, json: async () => ({ ok: false, receipt }) }));
  const result = await api.answerQuestion(request);
  assert.equal(result.ok, false);
  assert.deepEqual(JSON.parse(JSON.stringify(result.receipt)), receipt);
});

test('delete confirms only an exact catalog receipt and preserves the native-history boundary', async () => {
  const id = 'session-1';
  for (const body of [{ ok: true }, { ok: true, sessionId: 'wrong', catalogDeleted: true, nativeHistoryDeleted: false }]) {
    const { api } = loadApi(() => ({ ok: true, json: async () => body }));
    await assert.rejects(api.deleteChatSession(id));
  }
  const body = { ok: true, sessionId: id, catalogDeleted: true, nativeHistoryDeleted: false };
  const { api } = loadApi(() => ({ ok: true, json: async () => body }));
  assert.deepEqual(JSON.parse(JSON.stringify(await api.deleteChatSession(id))), body);
});
