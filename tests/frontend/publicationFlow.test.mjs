import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const mediaId = 'a'.repeat(32);
const approvalId = 'approval-flow-one';
const payloadHash = 'b'.repeat(64);
const platforms = { douyin: '抖音', 'wechat-oa': '微信公众号', xiaohongshu: '小红书', zhihu: '知乎' };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

function record(platform = 'douyin', overrides = {}) {
  return { id: approvalId, payloadHash, state: 'pending', createdAt: Date.now() - 1000, expiresAt: Date.now() + 60000,
    actorId: 'operator-one', projectId: 'default', operationId: null, result: null,
    subject: { schemaVersion: 1, project: { id: 'default', name: '测试教育 IP' }, platform: { id: platform, name: platforms[platform] },
      account: { id: `real-${platform}-account`, name: '明确账号' }, mode: platform === 'wechat-oa' ? 'create_draft' : 'publish_now',
      scheduledAt: null, audience: platform === 'wechat-oa' ? 'platform_draft' : 'public', title: 'Host 冻结标题', body: 'Host 冻结正文 V1',
      tags: 'Host原始,标签', persona: 'persona-one', media: [{ artifactId: mediaId, path: platform === 'wechat-oa' ? 'drafts/cover.png' : 'drafts/input.mp4', name: platform === 'wechat-oa' ? 'cover.png' : 'input.mp4', mime: platform === 'wechat-oa' ? 'image/png' : 'video/mp4',
        revision: 2, sha256: 'c'.repeat(64), bundleSha256: null, size: 12, url: `/api/chat/artifacts/${mediaId}/content` }] },
    ...overrides };
}

async function flow(options, verify) {
  const platform = options.platform || 'douyin';
  const { window } = parseHTML('<html><head></head><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  // Linkedom omits native input-feature and focus bookkeeping; install those
  // browser contracts while keeping the real React controls/event handlers.
  window.document.oninput = null;
  let focused = window.document.body;
  Object.defineProperty(window.document, 'activeElement', { configurable: true, get: () => focused });
  const nativeFocus = window.HTMLElement.prototype.focus;
  const nativeMatches = window.HTMLElement.prototype.matches;
  const inputType = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'type');
  Object.defineProperty(window.HTMLInputElement.prototype, 'type', { ...inputType, get() { return inputType.get.call(this) || 'text'; } });
  window.HTMLElement.prototype.focus = function () { focused = this; };
  window.HTMLElement.prototype.matches = function (selector) { return selector === ':focus' ? focused === this : nativeMatches.call(this, selector); };
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true, writable: true }, document: { value: window.document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true } });
  const root = require('react-dom/client').createRoot(window.document.getElementById('root'));
  const requests = []; const unexpected = [];
  const intervals = new Map(); const timeouts = new Map(); let timerId = 0;
  const draft = { title: '页面未批准的标题', body: '页面未批准的母版', platforms: [platform], overrides: { [platform]: '页面未批准的平台文案' }, tags: '页面标签,原样' };
  const storageValues = new Map([['easel_publish_draft', JSON.stringify(draft)]]);
  const routes = new Map([
    ['GET /api/accounts', () => json([{ platform, name: platforms[platform], backend: 'web', supported: true, loggedIn: true, note: '' }])],
    ['GET /api/outputs', () => json([{ type: 'file', name: platform === 'wechat-oa' ? 'cover.png' : 'input.mp4', path: platform === 'wechat-oa' ? 'drafts/cover.png' : 'drafts/input.mp4', kind: platform === 'wechat-oa' ? 'image' : 'video', mtime: 100 },
      { type: 'file', name: 'second.mp4', path: 'drafts/second.mp4', kind: 'video', mtime: 90 }])],
    [`POST /api/publish/${platform}/prepare`, options.prepare || (() => json(record(platform)))],
    [`POST /api/publication-approvals/${approvalId}/decision`, options.decision || (() => json(record(platform, { state: 'approved' })))],
    [`POST /api/publish/${platform}`, options.execute || (() => json(record(platform, { state: 'approved', operationId: 'operation-one', result: { status: 'submitted' } })))],
    [`GET /api/publication-approvals/${approvalId}`, options.status || (() => json(record(platform, { state: 'approved', operationId: 'operation-one', result: { status: 'confirmed' } })))],
    [`POST /api/publish/${platform}/sms?approvalId=${approvalId}`, options.sms || (() => json({ ok: true }))],
  ]);
  const fetch = async (value, init = {}) => {
    const url = new URL(String(value), window.location.origin);
    const key = `${init.method || 'GET'} ${url.pathname}${url.search}`;
    const handler = url.origin === window.location.origin && routes.get(key);
    if (!handler) { unexpected.push(key); throw new Error(`UNRECOGNIZED NETWORK: ${key}`); }
    const request = { key, method: init.method || 'GET', url: String(value), body: init.body ? JSON.parse(init.body) : undefined };
    requests.push(request);
    return handler(request, requests);
  };
  try {
    const Page = componentModule('components/PublishPage.tsx', { storageValues, externals: { dompurify: require('dompurify')(window) },
      globals: { window, document: window.document, HTMLElement: window.HTMLElement, navigator: { userAgent: 'test', platform: 'MacIntel', maxTouchPoints: 0 },
        crypto, fetch, Headers, Response,
        setInterval: callback => { const id = ++timerId; intervals.set(id, callback); return id; }, clearInterval: id => intervals.delete(id),
        setTimeout: callback => { const id = ++timerId; timeouts.set(id, callback); return id; }, clearTimeout: id => timeouts.delete(id),
      },
    }).default;
    await React.act(async () => root.render(React.createElement(Page, { persona: 'persona-one' })));
    const click = async text => {
      const button = Array.from(window.document.querySelectorAll('button')).find(node => node.textContent.trim() === text);
      assert.ok(button, `button: ${text}`); await React.act(async () => button.click()); return button;
    };
    const selectMedia = async (path = platform === 'wechat-oa' ? 'drafts/cover.png' : 'drafts/input.mp4') => {
      if (!window.document.querySelector('.media-grid')) await click('选择媒体');
      const cell = window.document.querySelector(`.media-cell[title="${path}"]`);
      assert.ok(cell, path); await React.act(async () => cell.click());
    };
    const input = async (node, value) => {
      assert.ok(node);
      let prototype = Object.getPrototypeOf(node);
      while (prototype && !Object.getOwnPropertyDescriptor(prototype, 'value')?.set) prototype = Object.getPrototypeOf(prototype);
      const setter = prototype && Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
      await React.act(async () => {
        if (setter) setter.call(node, value); else node.value = value;
        node.dispatchEvent(new window.Event('input', { bubbles: true }));
        node.dispatchEvent(new window.Event('change', { bubbles: true }));
      });
    };
    const tick = async () => React.act(async () => { for (const callback of [...intervals.values()]) await callback(); });
    const approve = async () => { const button = window.document.querySelector('[data-decision="approve"]'); assert.ok(button); await React.act(async () => button.click()); };
    await verify({ window, root, requests, unexpected, click, selectMedia, input, tick, approve, draft });
    assert.deepEqual(unexpected, [], 'all network must be explicitly whitelisted');
  } finally {
    await React.act(async () => root.unmount());
    intervals.clear(); timeouts.clear();
    window.HTMLElement.prototype.focus = nativeFocus; window.HTMLElement.prototype.matches = nativeMatches;
    Object.defineProperty(window.HTMLInputElement.prototype, 'type', inputType);
    for (const [key, descriptor] of Object.entries(previous)) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
}

const writes = requests => requests.filter(request => request.method === 'POST');

test('review prepares a subject without dispatch; explicit approval executes the same frozen id/hash/body/artifact IDs', async () => {
  await flow({}, async ({ window, requests, click, selectMedia, approve, draft }) => {
    await selectMedia(); await click('核对并发布');
    assert.deepEqual(writes(requests).map(item => item.key), ['POST /api/publish/douyin/prepare']);
    assert.deepEqual(writes(requests)[0].body, { title: draft.title, body: draft.overrides.douyin, media: ['drafts/input.mp4'], tags: draft.tags, persona: 'persona-one' });
    assert.match(window.document.body.textContent, /Host 冻结正文 V1/);
    await approve();
    assert.deepEqual(writes(requests).map(item => item.key), ['POST /api/publish/douyin/prepare', `POST /api/publication-approvals/${approvalId}/decision`, 'POST /api/publish/douyin']);
    assert.deepEqual(writes(requests)[1].body, { approved: true, payloadHash });
    const subject = record().subject;
    assert.deepEqual(writes(requests)[2].body, { title: subject.title, body: subject.body, tags: subject.tags, persona: subject.persona,
      media: [], artifactIds: [mediaId], approvalId, payloadHash });
  });
});

test('editing draft text removes the old approval dialog and cannot execute its stale DOM control', async () => {
  await flow({}, async ({ window, requests, click, selectMedia, input }) => {
    await selectMedia(); await click('核对并发布');
    const oldApprove = window.document.querySelector('[data-decision="approve"]'); assert.ok(oldApprove);
    await input(window.document.querySelector('textarea'), '已经修改的母版');
    assert.equal(window.document.querySelector('[data-decision="approve"]'), null);
    await React.act(async () => oldApprove.click());
    assert.equal(writes(requests).length, 1);
  });
});

test('a late prepare response cannot reopen approval after media changes', async () => {
  const pending = deferred();
  await flow({ prepare: () => pending.promise }, async ({ window, requests, click, selectMedia }) => {
    await selectMedia(); await click('核对并发布');
    await selectMedia('drafts/second.mp4');
    await React.act(async () => pending.resolve(json(record())));
    assert.equal(window.document.querySelector('[data-decision="approve"]'), null);
    assert.deepEqual(writes(requests).map(item => item.key), ['POST /api/publish/douyin/prepare']);
  });
});

test('a lost execute response can resume the same approved record without preparing or approving again', async () => {
  let started = false;
  let externalStarts = 0;
  const finished = record('douyin', { state: 'approved', operationId: 'operation-one', result: { status: 'confirmed' } });
  await flow({
    execute: () => {
      if (!started) { started = true; externalStarts++; throw new Error('execute response lost after operation accepted'); }
      return json(finished); // Host claim replay: the same operation, never another dispatch.
    },
    status: () => json(finished),
  }, async ({ window, requests, click, selectMedia, approve, tick }) => {
    await selectMedia(); await click('核对并发布'); await approve();
    assert.ok(window.document.querySelector('[role="alert"]'), 'the transport failure must remain visible');
    assert.equal(window.document.querySelector('[data-decision="approve"]'), null, 'approved must not be forged back to pending');
    const resume = Array.from(window.document.querySelectorAll('button')).find(button => /核对并继续此批准版本|继续此批准版本|核对此批准版本|核对执行结果/.test(button.textContent));
    assert.ok(resume, `approved record lost its recovery action after: ${writes(requests).map(item => item.key).join(' -> ')}`);
    await React.act(async () => resume.click()); await tick();
    assert.equal(externalStarts, 1);
    assert.equal(requests.filter(item => item.key.endsWith('/prepare')).length, 1);
    assert.equal(requests.filter(item => item.key.endsWith('/decision')).length, 1);
    for (const execute of requests.filter(item => item.key === 'POST /api/publish/douyin')) {
      assert.equal(execute.body.approvalId, approvalId); assert.equal(execute.body.payloadHash, payloadHash);
      assert.deepEqual(execute.body.artifactIds, [mediaId]); assert.equal(execute.body.body, record().subject.body);
    }
    assert.match(window.document.body.textContent, /平台回执已确认/);
  });
});

test('changing selected media invalidates a visible approval rather than reusing its old bytes', async () => {
  await flow({}, async ({ window, requests, click, selectMedia }) => {
    await selectMedia(); await click('核对并发布');
    assert.ok(window.document.querySelector('[data-decision="approve"]'));
    await selectMedia('drafts/second.mp4');
    assert.equal(window.document.querySelector('[data-decision="approve"]'), null);
    assert.deepEqual(writes(requests).map(item => item.key), ['POST /api/publish/douyin/prepare']);
  });
});

test('a late prepare response cannot revive approval after caption editing', async () => {
  const pending = deferred();
  await flow({ prepare: () => pending.promise }, async ({ window, requests, click, selectMedia, input }) => {
    await selectMedia(); await click('核对并发布');
    await input(window.document.querySelector('textarea'), '准备中又编辑了正文');
    await React.act(async () => pending.resolve(json(record())));
    assert.equal(window.document.querySelector('[data-decision="approve"]'), null);
    assert.equal(writes(requests).length, 1);
  });
});

for (const [platform, status, message] of [
  ['douyin', 'submitted', '已提交，等待核对平台回执'],
  ['wechat-oa', 'draft_created', '草稿已创建，尚未公开发布'],
]) test(`${status} is shown as a pending receipt or draft, never confirmed public success`, async () => {
  await flow({ platform, execute: () => json(record(platform, { state: 'approved', operationId: 'operation-one', result: { status } })) },
    async ({ window, click, selectMedia, approve }) => {
      await selectMedia(); await click('核对并发布'); await approve();
      const state = window.document.querySelector('.pv-pubstate');
      assert.ok(state); assert.equal(state.classList.contains('ok'), false); assert.match(state.textContent, new RegExp(message));
      assert.doesNotMatch(window.document.body.textContent, /已公开发布|发布成功/);
    });
});

test('SMS verification stays bound to the exact executing approval ID and never dispatches another publication', async () => {
  let smsSubmitted = false;
  await flow({ execute: () => json(record('douyin', { state: 'approved', operationId: 'operation-one', result: { status: 'executing' } })),
    status: () => json(record('douyin', { state: 'approved', operationId: 'operation-one', result: { status: smsSubmitted ? 'confirmed' : 'executing' },
      runnerStatus: { state: smsSubmitted ? 'success' : 'sms_required', message: '请输入平台验证码' } })),
    sms: () => { smsSubmitted = true; return json({ ok: true }); },
  }, async ({ window, requests, click, selectMedia, approve, tick, input }) => {
    await selectMedia(); await click('核对并发布'); await approve(); await tick();
    const code = window.document.querySelector('[placeholder="请输入手机收到的验证码"]'); assert.ok(code);
    await input(code, '12 34-56'); assert.equal(code.value, '123456'); await click('提交验证码');
    const sms = requests.filter(item => item.key.includes('/sms?'));
    assert.equal(sms.length, 1); assert.equal(sms[0].key, `POST /api/publish/douyin/sms?approvalId=${approvalId}`);
    assert.deepEqual(sms[0].body, { code: '123456' });
    assert.equal(requests.filter(item => item.key === 'POST /api/publish/douyin').length, 1);
    await tick(); assert.equal(window.document.querySelector('[placeholder="请输入手机收到的验证码"]'), null);
  });
});

test('an unknown terminal result stays readonly and cannot expose a dispatch retry', async () => {
  await flow({ execute: () => json(record('douyin', { state: 'approved', operationId: 'operation-one', result: { status: 'unknown' } })) },
    async ({ window, requests, click, selectMedia, approve, tick }) => {
      await selectMedia(); await click('核对并发布'); await approve(); await tick();
      assert.match(window.document.body.textContent, /结果待核实/);
      assert.equal(window.document.querySelector('[data-decision="approve"]'), null);
      assert.equal(Array.from(window.document.querySelectorAll('button')).some(button => /继续此批准版本/.test(button.textContent)), false);
      assert.equal(requests.filter(item => item.key === 'POST /api/publish/douyin').length, 1);
    });
});

for (const [platform, status] of [['douyin', 'submitted'], ['wechat-oa', 'draft_created']]) {
  test(`${status} does not elevate an unverified runner success message into public publication`, async () => {
    await flow({ platform, execute: () => json(record(platform, { state: 'approved', operationId: 'operation-one',
      result: { status, message: '已公开发布，发布成功' } })) }, async ({ window, click, selectMedia, approve }) => {
      await selectMedia(); await click('核对并发布'); await approve();
      assert.equal(window.document.querySelector('.pv-pubstate').classList.contains('ok'), false);
      assert.doesNotMatch(window.document.querySelector('.pv-pubstate').textContent, /已公开发布|发布成功/);
    });
  });
}

test('a pre-dispatch network failure can claim only the already approved frozen version on recovery', async () => {
  let attempts = 0;
  await flow({
    execute: () => { attempts++; if (attempts === 1) throw new Error('transport never reached server');
      return json(record('douyin', { state: 'approved', operationId: 'operation-one', result: { status: 'confirmed' } })); },
    status: () => json(record('douyin', { state: 'approved', operationId: null, result: null })),
  }, async ({ window, requests, click, selectMedia, approve }) => {
    await selectMedia(); await click('核对并发布'); await approve();
    const resume = Array.from(window.document.querySelectorAll('button')).find(button => /核对并继续此批准版本/.test(button.textContent)); assert.ok(resume);
    await React.act(async () => resume.click());
    assert.equal(requests.filter(item => item.key.endsWith('/prepare')).length, 1);
    assert.equal(requests.filter(item => item.key.endsWith('/decision')).length, 1);
    const executed = requests.filter(item => item.key === 'POST /api/publish/douyin');
    assert.equal(executed.length, 2);
    for (const request of executed) {
      assert.equal(request.body.approvalId, approvalId); assert.equal(request.body.payloadHash, payloadHash);
      assert.equal(request.body.body, record().subject.body); assert.deepEqual(request.body.artifactIds, [mediaId]);
    }
    assert.match(window.document.querySelector('.pv-pubstate').textContent, /平台回执已确认/);
  });
});

test('recovery refuses a changed approval hash instead of executing stale or replacement content', async () => {
  await flow({ execute: () => { throw new Error('response lost'); },
    status: () => json(record('douyin', { state: 'approved', payloadHash: 'e'.repeat(64), operationId: null, result: null })),
  }, async ({ window, requests, click, selectMedia, approve }) => {
    await selectMedia(); await click('核对并发布'); await approve();
    const resume = Array.from(window.document.querySelectorAll('button')).find(button => /核对并继续此批准版本/.test(button.textContent)); assert.ok(resume);
    await React.act(async () => resume.click());
    assert.equal(requests.filter(item => item.key === 'POST /api/publish/douyin').length, 1);
    assert.match(window.document.body.textContent, /批准状态已变化/);
  });
});
