import type { ChatSession, StreamState } from './store';
import { loadPromptAttempt } from './promptAdmission';
import { orderByRecency, pinCurrentBlank, sectionMembers } from '../vendor/deepseek-harness/navigation/order';
import { formatSessionMarkdown } from '../vendor/deepseek-harness/navigation/markdown';
import type { SessionEvent } from '../vendor/deepseek-harness/navigation/markdown-types';

export type ArchiveFilter = 'default' | 'show' | 'only';
type NavigableSession = ChatSession & { pinned?: boolean; updated?: number };

/** Easel's scoped catalog projected onto the upstream recency/pin ordering. */
export function navigationSessions(sessions: readonly NavigableSession[], query: string, filter: ArchiveFilter, activeId: string | null): NavigableSession[] {
  const normalized = query.trim().toLowerCase();
  const byId = Object.fromEntries(sessions.map(session => [session.id, session]));
  const summaries = Object.fromEntries(sessions.map(session => [session.id, {
    id: session.id, blank: !session.messages.length, updatedAt: session.updated || session.created || 0,
  }]));
  const members = sessions.filter(session => {
    if (!session.messages.length && (normalized || session.id !== activeId)
        && !session.pendingTurnId && !session.queued?.length && !loadPromptAttempt(session.id)) return false;
    if (filter === 'only' && !session.archived) return false;
    if (filter === 'default' && session.archived) return false;
    return !normalized || [session.title, session.id, session.persona || ''].some(value => value.toLowerCase().includes(normalized));
  });
  const ids = orderByRecency(members.map(session => session.id), summaries);
  const currentBlank = activeId && byId[activeId]?.messages.length === 0 && members.some(session => session.id === activeId) ? activeId : undefined;
  const ordered = pinCurrentBlank(ids, currentBlank).map(id => summaries[id]);
  return sectionMembers(ordered, new Set(sessions.filter(session => session.pinned).map(session => session.id)), new Set(sessions.filter(session => session.archived).map(session => session.id)))
    .map(summary => byId[summary.id]);
}

/** A display window never edits storage; current/working rows extend its watermark. */
export function visibleHistoryCount(sessions: readonly ChatSession[], requested: number, activeId: string | null, states: Readonly<Record<string, string>>): number {
  let count = requested;
  sessions.forEach((session, index) => {
    if (session.id === activeId || /^(running|streaming|recovering|stopping|stop_failed|failed|waiting|approval|question|unread|completed-unread)$/.test(states[session.id] || '')) count = Math.max(count, index + 1);
  });
  return Math.min(sessions.length, count);
}

/** Project the same waiting/active/completed facts used by the upstream workspace rows. */
export function sessionAttentionState(session: ChatSession, stream?: StreamState): string {
  if (stream) {
    if (stream.lifecycle === 'stopping' || stream.lifecycle === 'stop_failed') return stream.lifecycle;
    const pending = stream.questions.filter(question => !question.status || question.status === 'pending');
    if (pending.some(question => question.kind === 'approval' || question.publication)) return 'approval';
    if (pending.length) return 'question';
    return stream.lifecycle === 'recovering' ? 'recovering' : 'running';
  }
  if (session.pendingTurnId) return 'recovering';
  if (session.messages.at(-1)?.status === 'failed') return 'failed';
  return session.unreadCompletion ? 'completed-unread' : '';
}

function exportText(value: string): string {
  return value
    .replace(/\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b/g, '[已隐藏凭据]')
    .replace(/\b(Bearer\s+)[A-Za-z0-9._~+/-]{16,}/gi, '$1[已隐藏凭据]')
    .replace(/\b((?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+/gi, '$1[已隐藏凭据]');
}

/** Export only the visible transcript. Never include injected prompts or tool/auth payloads. */
export function sessionMarkdown(session: ChatSession): string {
  const events: SessionEvent[] = [{ type: 'session/title', data: { title: exportText(session.title) } }];
  for (const message of session.messages) {
    const text = exportText(message.content || '');
    if (message.role === 'user') {
      const attachmentNames = (message.attachments || []).map(file => `附件：${file.name.replace(/[\r\n]/g, ' ')}`);
      events.push({ type: 'turn/start' }, { type: 'user/message', surfaceOp: 'append', data: {
        source: { kind: 'user' }, content: [{ type: 'text', text: [text, ...attachmentNames].filter(Boolean).join('\n\n') }],
      } });
    } else {
      events.push({ type: 'assistant/message', surfaceOp: 'append', data: { message: { content: [{ type: 'text', text }] } } }, { type: 'turn/end' });
    }
  }
  return formatSessionMarkdown(events);
}

export function downloadSessionMarkdown(session: ChatSession): void {
  const url = URL.createObjectURL(new Blob([sessionMarkdown(session)], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${(session.title || '对话').replace(/[<>:"/\\|?*\p{Cc}]/gu, '_').slice(0, 100)}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
