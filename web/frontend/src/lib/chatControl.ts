import type { ChatSession, QueuedMessage } from './store';

export async function confirmChatStop(stop: () => Promise<{ stopped: boolean; status?: string }>): Promise<'stopped' | 'already_finished'> {
  const receipt = await stop();
  if (receipt.stopped && (!receipt.status || receipt.status === 'stopped')) return 'stopped';
  if (receipt.status === 'already_finished') return 'already_finished';
  throw new Error('停止尚未确认，任务可能仍在运行；可再次尝试停止。');
}

export function enqueueMessage(session: ChatSession, message: QueuedMessage): ChatSession {
  if (session.queued?.some(item => item.id === message.id)) return session;
  return { ...session, queued: [...(session.queued || []), message], updated: Math.max(Date.now(), (session.updated ?? session.created ?? 0) + 1) };
}

export function nextQueuedMessage(session: ChatSession, running: boolean): QueuedMessage | null {
  const next = session.queued?.[0];
  return running || session.queuePaused || session.archived || next?.delivery === 'sending' ? null : next || null;
}
