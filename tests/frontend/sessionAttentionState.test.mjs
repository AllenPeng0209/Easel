import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const { sessionAttentionState, visibleHistoryCount } = componentModule('lib/sessionNavigation.ts', { user: 'reader' });
const session = { id: 'task', title: 'Task', messages: [], created: 1 };
const stream = { content: '', activity: '', thinking: '', questions: [], lifecycle: 'running' };

test('task attention separates active approval, question, stop failure and recovery', () => {
  const question = { id: 'question', status: 'pending', questions: [] };
  const approval = { id: 'approval', kind: 'approval', status: 'pending', questions: [] };
  const pending = { ...stream, questions: [question, approval] };
  assert.equal(sessionAttentionState(session, pending), 'approval');
  assert.equal(sessionAttentionState(session, { ...pending, questions: [question, { ...approval, status: 'answered' }] }), 'question');
  assert.equal(sessionAttentionState(session, { ...pending, lifecycle: 'stop_failed' }), 'stop_failed');
  assert.equal(sessionAttentionState(session, { ...pending, lifecycle: 'stopping' }), 'stopping');
  assert.equal(sessionAttentionState(session, { ...stream, lifecycle: 'recovering' }), 'recovering');
  assert.equal(sessionAttentionState({ ...session, pendingTurnId: 'previous' }), 'recovering');
  assert.equal(sessionAttentionState(session, stream), 'running');
});

test('persisted completion and failures remain visible beyond the initial history window', () => {
  const unread = { ...session, unreadCompletion: true };
  const failed = { ...session, id: 'failed', messages: [{ role: 'assistant', status: 'failed' }] };
  assert.equal(sessionAttentionState(unread), 'completed-unread');
  assert.equal(sessionAttentionState({ ...unread, unreadCompletion: false }), '');
  assert.equal(sessionAttentionState(failed), 'failed');
  assert.equal(visibleHistoryCount([session, failed], 1, null, { failed: 'failed' }), 2);
});
