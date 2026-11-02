import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';
const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const id = 'a'.repeat(32);
const subject = overrides => ({ schemaVersion: 1, project: { id: 'default', name: '教育 IP' },
  platform: { id: 'xiaohongshu', name: '小红书' }, account: { id: 'real-account-12845', name: '小彭教育' },
  mode: 'publish_now', scheduledAt: null, audience: 'public', title: '给家长的一句话', body: '先听孩子说完。\n再一起想办法。',
  tags: '家庭教育,亲子沟通', persona: '温和、清楚的教育创作者',
  media: [{ artifactId: id, path: 'reports/cover.png', name: 'cover.png', mime: 'image/png', revision: 2,
    sha256: 'b'.repeat(64), bundleSha256: null, size: 100, url: `/api/chat/artifacts/${id}/content` }], ...overrides });
const approval = overrides => ({ id: 'approval-one', payloadHash: 'c'.repeat(64), subject: subject(), state: 'pending',
  createdAt: Date.now() - 1000, expiresAt: Date.now() + 60000, actorId: 'operator-one', projectId: 'default', operationId: null, result: null, ...overrides });

async function dialogDOM(props, callback) {
  const { parseHTML } = require('linkedom');
  const { window } = parseHTML('<html><head></head><body><button id="opener">发布</button><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  let active = window.document.body;
  Object.defineProperty(window.document, 'activeElement', { get: () => active, configurable: true });
  const matches = window.HTMLElement.prototype.matches;
  const focus = window.HTMLElement.prototype.focus;
  window.HTMLElement.prototype.focus = function () { active = this; };
  window.HTMLElement.prototype.matches = function (selector) { return selector === ':focus' ? active === this : matches.call(this, selector); };
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true, writable: true }, document: { value: window.document, configurable: true, writable: true },
    IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true, writable: true } });
  const root = require('react-dom/client').createRoot(window.document.getElementById('root'));
  try {
    const exports = componentModule('components/PublicationApprovalDialog.tsx', { globals: { window, document: window.document,
      HTMLElement: window.HTMLElement, navigator: { userAgent: 'test desktop', platform: 'MacIntel', maxTouchPoints: 0 }, crypto,
    } });
    const Component = exports.default;
    const render = async next => React.act(async () => root.render(React.createElement(Component, next)));
    const opener = window.document.getElementById('opener'); opener.focus();
    await render(props);
    const key = name => {
      const event = new window.Event('keydown', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'key', { value: name }); window.document.dispatchEvent(event); return event;
    };
    await callback({ window, root, render, key, opener, exports });
  } finally {
    await React.act(async () => root.unmount());
    window.HTMLElement.prototype.matches = matches; window.HTMLElement.prototype.focus = focus;
    for (const [name, descriptor] of Object.entries(previous)) { if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; }
  }
}

test('shared readonly subject exposes exact IP, platform, real account, caption and fixed media version', () => {
  const { PublicationSubjectView } = componentModule('components/PublicationApprovalDialog.tsx');
  const html = renderToStaticMarkup(React.createElement(PublicationSubjectView, { subject: subject() }));
  for (const text of ['教育 IP', 'default', '小红书', 'xiaohongshu', 'real-account-12845', '小彭教育', '公开', '给家长的一句话',
    '先听孩子说完。', '再一起想办法。', '家庭教育', '亲子沟通', '温和、清楚的教育创作者', 'V2', 'b'.repeat(64)]) assert.ok(html.includes(text), text);
  assert.match(html, /\/api\/chat\/artifacts\/a{32}\/content/);
  assert.doesNotMatch(html, /\/api\/media\//);
});

test('unsafe preview URLs remain visible as subject metadata without loading an external file', () => {
  const { PublicationSubjectView } = componentModule('components/PublicationApprovalDialog.tsx');
  const value = subject(); value.media[0].url = 'https://evil.test/secret.png';
  const html = renderToStaticMarkup(React.createElement(PublicationSubjectView, { subject: value }));
  assert.match(html, /cover.png/); assert.match(html, /不可预览/);
  assert.doesNotMatch(html, /src="https:\/\/evil|href="https:\/\/evil/);
});

test('each platform sends only its current record identity and payload hash, with immediate duplicate locking', async () => {
  const received = [];
  let finish;
  const one = approval();
  const two = approval({ id: 'approval-two', payloadHash: 'd'.repeat(64), subject: subject({ platform: { id: 'weixin_mp', name: '微信公众号' },
    account: { id: 'wx-real-9721', name: '教育公众号' }, mode: 'create_draft', audience: 'platform_draft' }) });
  const props = { approvals: [one, two], busy: false, error: '', onApprove: (...args) => { received.push(args); return new Promise(resolve => { finish = resolve; }); }, onReject: () => {}, onClose: () => {} };
  await dialogDOM(props, async ({ window }) => {
    const card = window.document.querySelector('[data-approval-id="approval-two"]');
    const button = card.querySelector('[data-decision="approve"]');
    assert.match(button.textContent, /批准此版本并创建草稿/);
    await React.act(async () => { button.click(); button.click(); });
    assert.deepEqual(received, [['approval-two', 'd'.repeat(64)]]);
    assert.equal(button.disabled, true);
    assert.doesNotMatch(card.textContent, /已批准/);
    await React.act(async () => finish());
  });
});

test('terminal approval states and elapsed pending deadlines remain readonly and never default-approve', async () => {
  const calls = [];
  const records = ['approved', 'rejected', 'expired', 'invalidated'].map((state, index) => approval({ id: `record-${index}`, state }));
  records.push(approval({ id: 'past', expiresAt: Date.now() - 1 }));
  await dialogDOM({ approvals: records, busy: false, onApprove: (...args) => calls.push(args), onReject: (...args) => calls.push(args), onClose: () => {} }, async ({ window }) => {
    for (const button of window.document.querySelectorAll('[data-decision]')) {
      assert.equal(button.disabled, true); await React.act(async () => button.click());
    }
    assert.equal(calls.length, 0);
    assert.match(window.document.body.textContent, /未批准|已过期|截止/);
  });
});

test('busy requests disable both decisions; Escape only closes and blank-area Enter never approves', async () => {
  const actions = [];
  let closed = 0;
  const props = { approvals: [approval()], busy: true, onApprove: () => actions.push('approve'), onReject: () => actions.push('reject'), onClose: () => { closed++; } };
  await dialogDOM(props, async ({ window, key, root, opener }) => {
    for (const button of window.document.querySelectorAll('[data-decision]')) { assert.equal(button.disabled, true); await React.act(async () => button.click()); }
    await React.act(async () => key('Enter'));
    assert.equal(actions.length, 0);
    await React.act(async () => key('Escape'));
    assert.equal(closed, 1); assert.equal(actions.length, 0);
    await React.act(async () => root.render(null));
    assert.equal(window.document.activeElement, opener);
  });
});

test('an approved draft result is not presented as a public publication', async () => {
  await dialogDOM({ approvals: [approval({ state: 'approved', operationId: 'operation-draft', result: { status: 'draft_created' },
    subject: subject({ mode: 'create_draft', audience: 'platform_draft', platform: { id: 'weixin_mp', name: '微信公众号' } }) })],
    onApprove: () => {}, onReject: () => {}, onClose: () => {} }, async ({ window }) => {
    assert.match(window.document.body.textContent, /草稿已创建|已创建草稿/);
    assert.doesNotMatch(window.document.body.textContent, /已公开发布|发布成功/);
    assert.equal(window.document.querySelectorAll('[data-decision]:not(:disabled)').length, 0);
  });
});

test('caption tags and persona retain the original string payload rather than splitting or normalizing it', () => {
  const { PublicationSubjectView } = componentModule('components/PublicationApprovalDialog.tsx');
  const tags = '  教育,亲子；\n#原样标签  ';
  const html = renderToStaticMarkup(React.createElement(PublicationSubjectView, { subject: subject({ tags, persona: '' }) }));
  assert.ok(html.includes(tags)); assert.match(html, /通用模式/);
  const { validPublicationSubject } = componentModule('lib/publicationApproval.ts');
  assert.equal(validPublicationSubject(subject({ tags, persona: '' })), true);
  assert.equal(validPublicationSubject(subject({ tags: ['教育'] })), false);
});

test('a failed Host decision can be retried and a reject sends only its record id and hash', async () => {
  const approves = []; const rejects = [];
  const props = { approvals: [approval()], onApprove: (...args) => { approves.push(args); return Promise.reject(new Error('network unavailable')); },
    onReject: (...args) => { rejects.push(args); return Promise.resolve(); }, onClose: () => {} };
  await dialogDOM(props, async ({ window }) => {
    const approve = window.document.querySelector('[data-decision="approve"]');
    await React.act(async () => approve.click());
    assert.equal(approve.disabled, false); assert.ok(window.document.querySelector('[role="alert"]'));
    await React.act(async () => window.document.querySelector('[data-decision="reject"]').click());
    assert.deepEqual(approves, [['approval-one', 'c'.repeat(64)]]);
    assert.deepEqual(rejects, [['approval-one', 'c'.repeat(64)]]);
    assert.match(window.document.querySelector('[data-approval-id="approval-one"]').textContent, /等待服务端确认/);
    assert.doesNotMatch(window.document.querySelector('[data-approval-id="approval-one"]').textContent, /已拒绝/);
  });
});

test('a late failed decision for an old id/hash cannot unlock the replacement approval card', async () => {
  let failOld;
  const records = [];
  const send = (id, hash) => {
    records.push([id, hash]);
    return id === 'approval-one' ? new Promise((_resolve, reject) => { failOld = reject; }) : Promise.resolve();
  };
  const props = { approvals: [approval()], onApprove: send, onReject: () => {}, onClose: () => {} };
  await dialogDOM(props, async ({ window, render }) => {
    await React.act(async () => window.document.querySelector('[data-decision="approve"]').click());
    const next = approval({ id: 'approval-new', payloadHash: 'e'.repeat(64), subject: subject({ body: '已经修改的精确文案' }) });
    await render({ ...props, approvals: [next] });
    const approve = window.document.querySelector('[data-approval-id="approval-new"] [data-decision="approve"]');
    assert.equal(approve.disabled, false);
    await React.act(async () => approve.click());
    await React.act(async () => failOld(new Error('old failed response')));
    assert.equal(approve.disabled, true);
    assert.deepEqual(records, [['approval-one', 'c'.repeat(64)], ['approval-new', 'e'.repeat(64)]]);
  });
});

test('the migrated modal traps Tab and respects composition before Escape returns focus', async () => {
  let closed = 0;
  await dialogDOM({ approvals: [approval()], onApprove: () => {}, onReject: () => {}, onClose: () => { closed++; } }, async ({ window, key }) => {
    const first = window.document.querySelector('[aria-label="关闭发布确认"]');
    const last = window.document.querySelector('[data-decision="approve"]');
    last.focus(); await React.act(async () => key('Tab'));
    assert.equal(window.document.activeElement, first);
    const shiftTab = new window.Event('keydown', { bubbles: true, cancelable: true });
    Object.defineProperties(shiftTab, { key: { value: 'Tab' }, shiftKey: { value: true } });
    await React.act(async () => window.document.dispatchEvent(shiftTab));
    assert.equal(window.document.activeElement, last);
    window.document.dispatchEvent(new window.Event('compositionstart', { bubbles: true }));
    await React.act(async () => key('Escape')); assert.equal(closed, 0);
    window.document.dispatchEvent(new window.Event('compositionend', { bubbles: true }));
    await React.act(async () => key('Escape')); assert.equal(closed, 0);
    window.document.dispatchEvent(new window.Event('keyup', { bubbles: true }));
    await React.act(async () => key('Escape')); assert.equal(closed, 1);
  });
});

test('cross-IP or unsafe fixed media cannot enable approval even if a pending record is supplied', async () => {
  const calls = [];
  const invalidMedia = subject(); invalidMedia.media[0].url = 'https://evil.test/other.png';
  await dialogDOM({ approvals: [approval({ id: 'bad-media', subject: invalidMedia }), approval({ id: 'other-ip', projectId: 'other' })],
    onApprove: (...args) => calls.push(args), onReject: () => {}, onClose: () => {} }, async ({ window }) => {
    for (const button of window.document.querySelectorAll('[data-decision="approve"]')) {
      assert.equal(button.disabled, true); await React.act(async () => button.click());
    }
    assert.equal(calls.length, 0);
  });
});

test('a Host terminal update clears submission busy semantics without locally inventing approval', async () => {
  const record = approval();
  const props = { approvals: [record], onApprove: () => Promise.resolve(), onReject: () => {}, onClose: () => {} };
  await dialogDOM(props, async ({ window, render }) => {
    await React.act(async () => window.document.querySelector('[data-decision="approve"]').click());
    assert.equal(window.document.querySelector('[data-approval-id="approval-one"]').getAttribute('aria-busy'), 'true');
    await render({ ...props, approvals: [{ ...record, state: 'approved', result: { status: 'confirmed' } }] });
    assert.equal(window.document.querySelector('[data-approval-id="approval-one"]').getAttribute('aria-busy'), 'false');
    assert.match(window.document.body.textContent, /平台回执已确认/);
  });
});
