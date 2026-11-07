import type { ChatSession } from './store';

function fingerprint(session: ChatSession): string {
  // A refreshed history cursor is a write base, never a local edit.
  const { historyWindow: _cursor, ...content } = session;
  return JSON.stringify(content);
}

export class CatalogSyncTracker {
  private acknowledged = new Map<string, string>();

  observe(sessions: readonly ChatSession[]): void {
    for (const session of sessions) this.acknowledged.set(session.id, fingerprint(session));
  }

  pending(sessions: readonly ChatSession[]): ChatSession[] {
    return sessions.filter(session => {
      const meaningful = session.messages.length || session.pendingTurnId || session.queued?.length
        || session.pinned || session.archived || session.title && session.title !== '新对话';
      return meaningful && this.acknowledged.get(session.id) !== fingerprint(session);
    });
  }
}
