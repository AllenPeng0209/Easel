/**
 * Copyright (c) 2026 DeepSeek. MIT license; see ./LICENSE.
 * Extracted verbatim from deepseek-ai/deepseek-harness @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Source: packages/client/ui-workspace/src/client/tree.ts
 * Adaptation: package-only metadata types are projected by Easel's sessionNavigation adapter.
 */
type SessionId = string
export interface SessionSummary { id: string; blank?: boolean; updatedAt: number }
interface SessionListState { byId: Record<string, SessionSummary | undefined> }

export function orderByRecency(
  sessionIds: readonly SessionId[],
  summaries: SessionListState['byId'],
): SessionId[] {
  return sessionIds.flatMap((id) => {
    const summary = summaries[id]
    if (summary === undefined) return []
    return [{ id, rank: summary.updatedAt }]
  })
    .sort((a, b) => {
      if (a.rank !== b.rank) return b.rank - a.rank
      return a.id < b.id ? -1 : 1
    })
    .map(member => member.id)
}

export function pinCurrentBlank(
  order: readonly SessionId[],
  currentBlank: SessionId | undefined,
): SessionId[] {
  if (currentBlank === undefined) return [...order]
  return [currentBlank, ...order.filter(id => id !== currentBlank)]
}

export function sectionMembers(
  members: readonly SessionSummary[],
  pinned: ReadonlySet<SessionId>,
  archived: ReadonlySet<SessionId>,
): SessionSummary[] {
  const placeholders: SessionSummary[] = []
  const leading: SessionSummary[] = []
  const rest: SessionSummary[] = []
  for (const member of members) {
    if (member.blank) placeholders.push(member)
    else if (!archived.has(member.id) && pinned.has(member.id)) leading.push(member)
    else rest.push(member)
  }
  return [...placeholders, ...leading, ...rest]
}
