import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const frame = (id, event, value) => `id: ${id}\r\nevent: ${event}\r\ndata: ${JSON.stringify(value)}\r\n\r\n`;
const admission = frame(1, 'admitted', { turnId: 'exact-turn', sessionId: 'session' });
const done = frame(3, 'done', { outcome: 'completed' });

async function scenario(responses, { requireAdmission = true, resumeOnly = false, shortAdmissionTimeout = false, references, attachments = [] } = {}) {
  const requests = [];
  const observed = [];
  let finish;
  const terminal = new Promise(resolve => { finish = resolve; });
  const { streamChat } = componentModule('lib/api.ts', { globals: {
    TextDecoder,
    setTimeout: (fn, ms) => setTimeout(fn, ms >= 30000 && !shortAdmissionTimeout ? ms : Math.min(ms, 5)),
    clearTimeout,
    fetch: async (url, options = {}) => {
      requests.push({ url, method: options.method || 'GET', body: options.body });
      const response = responses.shift();
      if (!response) throw new Error('Unexpected reconnect');
      return typeof response === 'function' ? response(options)
        : typeof response === 'string' ? new Response(response) : response;
    },
  } });
  const controller = streamChat('original draft', undefined, 'session',
    token => observed.push(['token', token]), (_sessionKey, outcome) => { observed.push(['done', outcome]); finish(); },
    error => { observed.push(['error', error.status, error.message]); finish(); }, undefined, undefined,
    undefined, 'exact-turn', resumeOnly, undefined, attachments, undefined, undefined, undefined, undefined,
    undefined, undefined, receipt => observed.push(['admitted', receipt.turnId, receipt.sessionId]), requireAdmission, references);
  let deadline;
  try {
    await Promise.race([terminal, new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error('No terminal event')), 250); })]);
  } finally {
    clearTimeout(deadline);
    controller.abort();
  }
  return { requests, observed };
}

test('truncated admission frame leaves cursor at zero and recovers only by GET for the original turn', async () => {
  // Even a syntactically complete data line does not commit a frame at EOF.
  const sliced = admission.slice(0, -2);
  const { requests, observed } = await scenario([sliced, admission + frame(2, 'token', 'once') + done]);
  assert.deepEqual(requests.map(request => [request.method, request.url]), [
    ['POST', '/api/chat/stream'], ['GET', '/api/chat/jobs/exact-turn/stream?after=0'],
  ]);
  assert.equal(JSON.parse(requests[0].body).turnId, 'exact-turn');
  assert.deepEqual(observed, [['admitted', 'exact-turn', 'session'], ['token', 'once'], ['done', 'completed']]);
});

test('wrong admission does not consume later tokens, done or cursor until matched replay', async () => {
  const wrong = frame(1, 'admitted', { turnId: 'other-turn', sessionId: 'session' });
  const { requests, observed } = await scenario([
    wrong + frame(2, 'token', 'must not display') + done,
    admission + frame(2, 'token', 'matched') + done,
  ]);
  assert.equal(requests[1].url, '/api/chat/jobs/exact-turn/stream?after=0');
  assert.deepEqual(observed, [['admitted', 'exact-turn', 'session'], ['token', 'matched'], ['done', 'completed']]);
});

test('cursor advances through complete frames only and deduplicates replayed admission', async () => {
  const partial = frame(3, 'token', 'incomplete').slice(0, -2);
  const { requests, observed } = await scenario([
    admission + frame(2, 'token', 'first') + partial,
    admission + frame(2, 'token', 'first') + frame(3, 'token', 'second') + frame(4, 'done', { outcome: 'completed' }),
  ]);
  assert.equal(requests[1].url, '/api/chat/jobs/exact-turn/stream?after=2');
  assert.deepEqual(observed, [
    ['admitted', 'exact-turn', 'session'], ['token', 'first'], ['token', 'second'], ['done', 'completed'],
  ]);
});

test('a replay terminal without durable receipt is unconfirmed for new drafts but old replay still completes', async () => {
  const missing = await scenario([done, done]);
  assert.equal(missing.requests.length, 2);
  assert.equal(missing.observed[0][0], 'error');
  assert.equal(missing.observed[0][1], undefined);
  assert.match(missing.observed[0][2], /接收|确认/);
  const legacy = await scenario([frame(2, 'token', 'legacy') + done], { requireAdmission: false, resumeOnly: true });
  assert.deepEqual(legacy.observed, [['token', 'legacy'], ['done', 'completed']]);
});

test('initial HTTP rejections preserve status while a lost POST and replay 404 stay statusless', async () => {
  for (const status of [422, 409]) {
    const result = await scenario([Response.json({ detail: 'Host response' }, { status })]);
    assert.equal(result.requests.length, 1);
    assert.deepEqual(result.observed, [['error', status, 'Host response']]);
  }
  const lost = await scenario([
    new Response('', { status: 502 }),
    Response.json({ detail: 'Journal not visible' }, { status: 404 }),
  ]);
  assert.deepEqual(lost.requests.map(request => request.method), ['POST', 'GET']);
  assert.equal(lost.observed[0][0], 'error');
  assert.equal(lost.observed[0][1], undefined);
});


test('a hung initial POST reaches a bounded unconfirmed state without replacing its turn', async () => {
  let requestSignal;
  const result = await scenario([({ signal }) => new Promise((_resolve, reject) => {
    requestSignal = signal;
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
  })], { shortAdmissionTimeout: true });
  assert.equal(result.requests.length, 1);
  assert.equal(JSON.parse(result.requests[0].body).turnId, 'exact-turn');
  assert.equal(requestSignal.aborted, true);
  assert.equal(result.observed[0][0], 'error');
  assert.equal(result.observed[0][1], undefined);
  assert.match(result.observed[0][2], /接收|确认/);
});

test('a late fetch result cannot commit admission after the timeout', async () => {
  const result = await scenario([() => new Promise(resolve => {
    setTimeout(() => resolve(new Response(admission + done)), 25);
  })], { shortAdmissionTimeout: true });
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(result.observed.length, 1);
  assert.equal(result.observed[0][0], 'error');
});

test('receiving admission cancels the short wait and leaves the running turn attached', async () => {
  const result = await scenario([() => new Response(new ReadableStream({
    start(stream) {
      const encoder = new TextEncoder();
      stream.enqueue(encoder.encode(admission));
      setTimeout(() => {
        stream.enqueue(encoder.encode(done));
        stream.close();
      }, 25);
    },
  }))], { shortAdmissionTimeout: true });
  assert.deepEqual(result.observed, [['admitted', 'exact-turn', 'session'], ['done', 'completed']]);
  assert.equal(result.requests.length, 1);
});

test('ordered typed inline occurrences are carried on POST, with old request bodies unchanged', async () => {
  const references = [{ start: 1, end: 4, attachmentId: 'file' }];
  const attachments = [{ id: 'file', name: 'a', path: '_inbox/user/batch/a' }];
  const result = await scenario([admission + done], { references, attachments });
  assert.deepEqual(JSON.parse(result.requests[0].body).references, references);
  const plain = await scenario([admission + done], { references: [] });
  assert.ok(!Object.hasOwn(JSON.parse(plain.requests[0].body), 'references'));
});
