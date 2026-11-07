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
    const suffix = saved ? session.messages.slice(-saved.messages.length) : [];
    const visibleMessage = (value: ChatSession['messages'][number]) => Object.entries(value)
      .filter(([key]) => key !== 'events').sort(([a], [b]) => a.localeCompare(b));
    const sameLegacyMessages = saved && !session.historyWindow && suffix.length === saved.messages.length
      && suffix.every((message, index) => JSON.stringify(visibleMessage(message)) === JSON.stringify(visibleMessage(saved.messages[index])));
    if (saved && !session.pendingTurnId && saved.historyWindow
      && (session.historyWindow?.base === saved.historyWindow.base || sameLegacyMessages)
      && (session.updated ?? session.created) === (saved.updated ?? saved.created)) {
      const metadata = (value: ChatSession) => Object.entries(value)
        .filter(([key]) => key !== 'messages' && key !== 'historyWindow').sort(([a], [b]) => a.localeCompare(b));
      if (JSON.stringify(metadata(session)) === JSON.stringify(metadata(saved))) continue;
    }
    if (!saved || session.pendingTurnId || (session.updated ?? session.created ?? 0) >= (saved.updated ?? saved.created ?? 0)) {
      byId.set(session.id, session);
    }
  }
  return [...byId.values()].sort((a, b) => (b.updated ?? b.created ?? 0) - (a.updated ?? a.created ?? 0));
}


/** Keep local edits made during a PUT and advance the write base only for the acknowledged snapshot. */
export function mergeWindowedCatalog(
  local: ChatSession[], remote: ChatSession[], deletedIds: string[] = [], submitted?: ChatSession[],
): ChatSession[] {
  const server = new Map(remote.map(session => [session.id, session]));
  const sent = new Map(submitted?.map(session => [session.id, session]));
  return mergeSessionCatalog(local, remote, deletedIds).map(session => {
    const saved = server.get(session.id);
    const window = saved?.historyWindow;
    if (!saved || !window || session === saved || session.historyWindow?.base === window.base) return session;
    const base = sent.get(session.id) ?? session;
    const start = base.historyWindow?.start ?? 0;
    const offset = window.start - start;
    if (saved.updated !== base.updated || offset < 0
        || JSON.stringify(saved.messages) !== JSON.stringify(base.messages.slice(offset))) return session;
    if (session === base && !session.pendingTurnId && (!submitted || !session.historyWindow)) return saved;
    if (offset === 0 && (session.historyWindow?.start ?? 0) === start
        && session.historyWindow?.base === base.historyWindow?.base) {
      return { ...session, historyWindow: window };
    }
    return session;
  });
}
