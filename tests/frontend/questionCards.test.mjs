import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { componentModule } from './agentMessagesHarness.mjs';
import { test } from 'node:test';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const component = { exports: componentModule('components/QuestionCards.tsx') };
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

test('approval cards preserve long multiline commands and both permission choices', () => {
  const command = `Codex 请求额外权限（仅本次）：python /data/project/Easel/${'long_segment_'.repeat(200)}/render.py\n第二行 <script>alert(1)</script>`;
  const rendered = renderToStaticMarkup(React.createElement(component.exports.default, {
    questions: [{
      id: 'codex-approval',
      questions: [{
        questionId: 'permission',
        header: '执行授权',
        question: command,
        options: [{ label: '允许本次' }, { label: '拒绝' }],
      }],
    }],
  }));
  assert.ok(rendered.includes('long_segment_'.repeat(200)));
  assert.ok(rendered.includes('\n第二行 &lt;script&gt;alert(1)&lt;/script&gt;'));
  assert.match(rendered, /<strong>允许本次<\/strong>/);
  assert.match(rendered, /<strong>拒绝<\/strong>/);
  assert.match(rendered, /disabled="">提交<\/button>/);
});

test('approval card grids can shrink and wrap commands without clipping their content', () => {
  const styles = readFileSync(new URL('../../web/frontend/src/styles/index.css', import.meta.url), 'utf8');
  const rule = selector => {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const matched = styles.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`));
    assert.ok(matched, selector);
    return matched[1];
  };
  for (const selector of ['.question-card', '.question-card__item', '.question-card__option']) {
    assert.match(rule(selector), /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
    assert.match(rule(selector), /min-width:\s*0/);
  }
  assert.match(rule('.question-card'), /overflow-wrap:\s*anywhere/);
  assert.match(rule('.question-card__title'), /white-space:\s*pre-wrap/);
  assert.match(rule('.question-card__options'), /minmax\(min\(150px,\s*100%\),\s*1fr\)/);
  assert.match(rule('.question-card__custom input'), /min-width:\s*0/);
});

for (const status of ['expired', 'cancelled', 'not_found', 'closed']) {
  test(`closed question ${status} remains readable but cannot be answered`, () => {
    const rendered = renderToStaticMarkup(React.createElement(component.exports.default, {
      questions: [{ id: 'old-question', status,
        questions: [{ questionId: 'choice', question: '原来的问题', options: [{ label: '原选项' }] }] }],
    }));
    assert.match(rendered, /原来的问题/);
    assert.match(rendered, /已过期或关闭/);
    assert.match(rendered, /disabled="">提交<\/button>/);
    assert.match(rendered, /disabled=""><strong>原选项/);
  });
}

test('expired timestamp prevents submission without inventing a hook deadline', () => {
  const rendered = renderToStaticMarkup(React.createElement(component.exports.default, {
    questions: [{ id: 'old-question', expiresAtMs: 1,
      questions: [{ questionId: 'choice', question: '过期问题', options: [{ label: '原选项' }] }] }],
  }));
  assert.match(rendered, /已过期或关闭/);
  assert.match(rendered, /disabled="">提交<\/button>/);
});

test('answered historical questions retain their accepted answer without reopening the form', () => {
  const rendered = renderToStaticMarkup(React.createElement(component.exports.default, {
    questions: [{ id: 'old-question', status: 'answered', resolvedBy: 'human', answers: { choice: ['已选答案'] },
      questions: [{ questionId: 'choice', question: '已回答问题' }] }],
  }));
  assert.match(rendered, /已回答问题/);
  assert.match(rendered, /已选答案/);
  assert.doesNotMatch(rendered, /question-card__submit/);
});

test('settled execution approvals expose only the result, even when old command and answers are stored', () => {
  for (const status of ['resolved', 'declined', 'blocked', 'revoked', 'expired', 'replaced', 'cancelled', 'closed', 'not_found']) {
    const rendered = renderToStaticMarkup(React.createElement(component.exports.default, {
      questions: [{ id: 'old-exec', kind: 'approval', status, answers: { permission: ['private-answer'] },
        questions: [{ questionId: 'permission', question: 'private-command', options: [{ label: '原选项' }] }] }],
    }));
    assert.ok(rendered.includes(`data-question-phase="${status}"`));
    assert.doesNotMatch(rendered, /private-command|private-answer|原选项|question-card__submit|正在同步记录/);
  }
});

test('pending historical execution approvals cannot display or submit an old command', () => {
  const question = { id: 'old-exec', status: 'pending',
    questions: [{ questionId: 'permission', header: '执行授权', question: 'private-command',
      options: [{ label: '允许本次' }] }] };
  const render = historical => renderToStaticMarkup(React.createElement(component.exports.default, {
    questions: [question], historical,
  }));
  assert.match(render(true), /历史授权请求/);
  assert.doesNotMatch(render(true), /private-command|允许本次|question-card__submit/);
  assert.match(render(false), /private-command/);
});

test('historical publication approval still shows the publication and its form', () => {
  for (const status of ['pending', 'expired']) {
    const rendered = renderToStaticMarkup(React.createElement(component.exports.default, {
      historical: true,
      questions: [{ id: 'publish', kind: 'approval', status,
        publication: { id: 'a'.repeat(32), payloadHash: 'b'.repeat(64),
          subject: { title: '发布标题', mode: 'publish_now', audience: 'public', media: [] } },
        questions: [{ questionId: 'publish', question: '确认发布', options: [{ label: '允许本次' }] }] }],
    }));
    assert.match(rendered, /发布标题/);
    assert.match(rendered, /question-card__submit/);
  }
});
