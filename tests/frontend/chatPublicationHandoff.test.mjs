import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const React = require('react');
const { parseHTML } = require('linkedom');
const actor = 'cat-editor';
const media = (overrides = {}) => ({ artifactId: 'a'.repeat(32), projectId: 'default',
  sessionId: 'cat-chat', turnId: 'cat-turn', path: 'cat/final.mp4', name: 'final.mp4',
  description: '猫咪打工日记', mime: 'video/mp4', size: 42, status: 'available', createdAt: 100,
  storageMode: 'snapshot', revision: 2, sha256: 'b'.repeat(64), bundleSha256: null,
  url: `/api/chat/artifacts/${'a'.repeat(32)}/content`, ...overrides });
const publication = (overrides = {}) => ({ id: 'cat-approval', payloadHash: 'c'.repeat(64),
  actorId: actor, projectId: 'default', state: 'pending', createdAt: 100, expiresAt: 200,
  operationId: null, result: null,
  subject: { schemaVersion: 1, project: { id: 'default', name: '猫咪 IP' },
    platform: { id: 'douyin', name: '抖音' }, account: { id: 'cat-account', name: '猫咪账号' },
    title: '猫咪打工日记', body: '先伸个懒腰。', tags: '猫咪', persona: '',
    mode: 'publish_now', scheduledAt: null, audience: 'public', media: [] }, ...overrides });
const question = (overrides = {}) => ({ id: 'cat-question', kind: 'approval', status: 'pending',
  publication: publication(), questions: [{ questionId: 'confirm', question: '核对后发布此视频',
    options: [{ label: '允许本次' }, { label: '拒绝' }] }], ...overrides });

async function mounted(relative, props, callback, { user = actor } = {}) {
  const { window, document } = parseHTML('<html><body><div id="root"></div></body></html>');
  window.location = { pathname: '/', origin: 'http://127.0.0.1:7860' };
  const events = [], requests = [];
  window.addEventListener('easel:publish-handoff', event => events.push(event.detail));
  const previous = Object.fromEntries(['window', 'document', 'IS_REACT_ACT_ENVIRONMENT'].map(
    key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, { window: { value: window, configurable: true },
    document: { value: document, configurable: true }, IS_REACT_ACT_ENVIRONMENT: { value: true, configurable: true } });
  const root = require('react-dom/client').createRoot(document.getElementById('root'));
  try {
    const Component = componentModule(relative, { user, globals: { window, document, Response,
      fetch: async (url, init) => {
        requests.push({ url, method: init?.method || 'GET' });
        return new Response(JSON.stringify({ questions: {}, states: {} }), { headers: { 'Content-Type': 'application/json' } });
      } } }).default;
    await React.act(async () => root.render(React.createElement(Component, props)));
    const button = label => [...document.querySelectorAll('button')].find(item => item.textContent === label);
    await callback({ window, document, events, requests, button });
  } finally {
    await React.act(async () => root.unmount());
    for (const [key, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  }
}

test('chat video delivery opens a scoped publish draft with the exact immutable version and title', async () => {
  await mounted('components/DeliverableCards.tsx', { artifacts: [media()] }, async ({ events, requests, button }) => {
    const publish = button('用于发布');
    assert.ok(publish, 'video delivery needs a publication entry');
    await React.act(async () => publish.click());
    assert.equal(events.length, 1);
    assert.equal(events[0].kind, 'media'); assert.equal(events[0].actorId, actor);
    assert.equal(events[0].projectId, 'default'); assert.equal(events[0].copy.title, 'final');
    assert.equal(events[0].copy.platform, undefined);
    assert.equal(events[0].media[0].artifactId, 'a'.repeat(32));
    assert.equal(events[0].media[0].revision, 2); assert.equal(events[0].media[0].sha256, 'b'.repeat(64));
    assert.deepEqual(requests, [], 'opening the draft must not prepare, approve or submit');
  });
});

test('the real delivery-card action carries a display title and never publishes its technical presentation note as the title', async () => {
  const video = media({ name: 'render.mp4', displayName: '猫咪打工日记.MP4',
    description: '成片待确认｜720×1280｜12秒｜画面与声音已核验｜未发布' });
  await mounted('components/DeliverableCards.tsx', { artifacts: [video] }, async ({ events, requests, button }) => {
    await React.act(async () => button('用于发布').click());
    assert.equal(events[0].copy.title, '猫咪打工日记');
    assert.equal(events[0].media[0].artifactId, video.artifactId);
    assert.equal(events[0].media[0].sha256, video.sha256);
    assert.deepEqual(requests, []);
  });
});

test('mixed delivery overview exposes only usable image/video snapshots for publication', async () => {
  const image = media({ artifactId: 'd'.repeat(32), path: 'cat/cover.png', name: 'cover.png', mime: 'image/png',
    url: `/api/chat/artifacts/${'d'.repeat(32)}/content` });
  const unavailable = media({ artifactId: 'e'.repeat(32), path: 'cat/missing.mp4', name: 'missing.mp4', status: 'unavailable',
    url: `/api/chat/artifacts/${'e'.repeat(32)}/content` });
  const legacy = media({ artifactId: 'f'.repeat(32), path: 'cat/old.mp4', name: 'old.mp4', storageMode: 'legacy-current',
    url: `/api/chat/artifacts/${'f'.repeat(32)}/content`, revision: null, sha256: null });
  const text = media({ artifactId: '1'.repeat(32), path: 'cat/script.txt', name: 'script.txt', mime: 'text/plain',
    url: `/api/chat/artifacts/${'1'.repeat(32)}/content` });
  await mounted('components/DeliverableCards.tsx', { artifacts: [media(), image, unavailable, legacy, text] },
    async ({ document, events }) => {
      const overview = document.querySelector('[data-deliverable-overview]');
      for (const [item, expected] of [[media(), true], [image, true], [unavailable, false], [legacy, false], [text, false]]) {
        const row = overview.querySelector(`[data-deliverable-overview-id="${item.artifactId}"]`);
        const publish = [...row.querySelectorAll('button')].find(button => button.textContent === '用于发布');
        assert.equal(!!publish, expected, item.name);
        if (item === image) await React.act(async () => publish.click());
      }
      assert.equal(events.length, 1); assert.equal(events[0].media[0].artifactId, 'd'.repeat(32));
    });
});

test('signed-out and foreign IP media have no publication action', async () => {
  for (const [artifacts, options] of [[[media()], { user: '' }], [[media({ projectId: 'other-ip' })], {}]]) {
    await mounted('components/DeliverableCards.tsx', { artifacts }, async ({ button, events }) => {
      assert.equal(button('用于发布'), undefined); assert.equal(events.length, 0);
    }, options);
  }
});

test('pending and settled publication questions navigate to their original approval without deciding', async () => {
  for (const value of [question(), question({ status: 'answered', resolvedBy: 'publication_host', answers: { confirm: ['允许本次'] } })]) {
    await mounted('components/QuestionCards.tsx', { questions: [value], sessionId: 'cat-chat' },
      async ({ button, events, requests, document }) => {
        const review = button('到发布中心核对');
        assert.ok(review, `${value.status} publication should keep its review entry`);
        const before = requests.length;
        const submit = document.querySelector('.question-card__submit');
        if (submit) assert.equal(submit.disabled, true);
        await React.act(async () => review.click());
        assert.equal(events.length, 1); assert.equal(events[0].kind, 'approval');
        assert.equal(events[0].approvalId, 'cat-approval'); assert.equal(events[0].payloadHash, 'c'.repeat(64));
        assert.equal(requests.length, before, 'navigation must not answer or recreate the approval');
        if (submit) assert.equal(submit.disabled, true);
        if (value.resolvedBy === 'publication_host') assert.match(document.body.textContent, /发布中心的决定已同步/);
      });
  }
});

test('a decision synchronized from the publication Host is not labeled as a chat answer', async () => {
  await mounted('components/QuestionCards.tsx', { questions: [question({ status: 'answered',
    resolvedBy: 'publication_host', answers: { confirm: ['允许本次'] } })], sessionId: 'cat-chat' },
  async ({ document }) => {
    assert.match(document.querySelector('.question-resolution__status').textContent, /发布中心的决定已同步/);
  });
});

test('expired publication receipt can be viewed without execution, while foreign receipts and questions cannot navigate', async () => {
  await mounted('components/PublicationReceiptCard.tsx', { record: publication({ state: 'expired' }) },
    async ({ button, events, requests }) => {
      const review = button('到发布中心核对'); assert.ok(review);
      await React.act(async () => review.click());
      assert.equal(events.length, 1); assert.equal(events[0].approvalId, 'cat-approval');
      assert.deepEqual(requests, []);
    });
  for (const record of [publication({ actorId: 'other-user' }), publication({ projectId: 'other-ip' })]) {
    for (const [component, props] of [['components/PublicationReceiptCard.tsx', { record }],
      ['components/QuestionCards.tsx', { questions: [question({ publication: record })], sessionId: 'cat-chat' }]]) {
      await mounted(component, props, async ({ button, events }) => {
        assert.equal(button('到发布中心核对'), undefined); assert.equal(events.length, 0);
      });
    }
  }
});
