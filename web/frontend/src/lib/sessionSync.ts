import type { ChatSession } from './store';

export function nextSessionRevision(session: Pick<ChatSession, 'updated' | 'created'>): number {
  return Math.max(Date.now(), (session.updated ?? session.created ?? 0) + 1);
}

/** Browser drafts are local; committed conversation records are recovered from the scoped server. */
export function mergeSessionCatalog(local: ChatSession[], remote: ChatSession[], deletedIds: string[] = []): ChatSession[] {
  const byId = new Map<string, ChatSession>();
  const deleted = new Set(deletedIds);
  for (const session of remote) if (session && typeof session.id === 'string' && !deleted.has(session.id) && Array.isArray(session.messages)) byId.set(session.id, session);
  for (const session of local) {
    if (deleted.has(session.id)) continue;
    const saved = byId.get(session.id);
    if (!saved || session.pendingTurnId || (session.updated ?? session.created ?? 0) >= (saved.updated ?? saved.created ?? 0)) {
      byId.set(session.id, session);
    }
  }
  return [...byId.values()].sort((a, b) => (b.updated ?? b.created ?? 0) - (a.updated ?? a.created ?? 0));
}
