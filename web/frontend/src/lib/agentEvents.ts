/** Easel transport adapter for the DeepSeek Harness tool/trajectory projection.
 * Upstream: deepseek-ai/deepseek-harness @ ce7073d (MIT), ui-tool / ui-trajectory.
 * Engine events stay structured across live delivery, replay and persisted chat.
 */
export interface AgentArtifact {
  artifactId: string;
  projectId: string;
  sessionId: string;
  turnId: string;
  path: string;
  name: string;
  mime: string;
  size: number;
  description?: string;
  status: 'available' | 'unavailable';
  createdAt: number;
  url: string;
  storageMode?: 'snapshot' | 'legacy-current';
  revision?: number | null;
  sha256?: string | null;
  bundleSha256?: string | null;
  reviewStatus?: 'unreviewed' | 'unknown';
}

export interface AgentSubagent {
  id: string;
  parentId: string | null;
  rootSessionId: string;
  originTurnId: string;
  title: string;
  status: string;
  createdAt: number;
  updatedAt: number;
}

export interface AgentEvent {
  id: string;
  kind: 'tool' | 'plan' | 'usage' | 'context' | 'decision' | 'artifact' | 'task' | 'publication' | 'business' | 'steer' | 'steer_capability' | 'subagent';
  runId?: string;
  turnId?: string;
  text?: string;
  business?: import('./businessObjects').BusinessObject;
  publication?: import('./publicationApproval').PublicationApprovalRecord;
  subagent?: AgentSubagent;
  callId?: string;
  parentCallId?: string;
  name?: string;
  status?: string;
  input?: unknown;
  output?: unknown;
  exitCode?: number | null;
  startedAt?: number;
  completedAt?: number;
  timestamp?: number;
  truncated?: boolean;
  questionId?: string;
  answers?: Record<string, string[]>;
  source?: string;
  artifacts?: AgentArtifact[];
  [key: string]: unknown;
}

const kinds = new Set(['tool', 'plan', 'usage', 'context', 'decision', 'artifact', 'task', 'publication', 'business', 'steer', 'subagent']);
const terminal = new Set(['completed', 'failed', 'interrupted', 'cancelled', 'stopped']);

function isSubagentHandle(value: unknown): value is string {
  return typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
}

export function isSubagentRouteSegment(value: unknown): value is string {
  return typeof value === 'string' && /^[a-zA-Z0-9_.-]{1,256}$/.test(value) && value !== '.' && value !== '..';
}

export function normalizeAgentSubagent(value: unknown): AgentSubagent | null {
  if (!value || typeof value !== 'object') return null;
  const { id, parentId, title, status, rootSessionId, originTurnId, createdAt, updatedAt } = value as Partial<AgentSubagent>;
  if (!isSubagentHandle(id) || typeof title !== 'string' || !title.trim() || typeof status !== 'string'
      || (parentId !== null && (!isSubagentHandle(parentId) || parentId === id))
      || !isSubagentRouteSegment(rootSessionId) || !isSubagentRouteSegment(originTurnId)
      || typeof createdAt !== 'number' || !Number.isSafeInteger(createdAt) || createdAt < 0
      || typeof updatedAt !== 'number' || !Number.isSafeInteger(updatedAt) || updatedAt < createdAt) return null;
  return { id, parentId, title: title.slice(0, 240), status: status.slice(0, 32),
    rootSessionId, originTurnId, createdAt, updatedAt };
}

export function mergeAgentSubagent(previous: AgentSubagent | undefined, next: AgentSubagent): AgentSubagent {
  if (!previous) return next;
  if (previous.rootSessionId !== next.rootSessionId || previous.originTurnId !== next.originTurnId
      || previous.parentId !== next.parentId || previous.createdAt !== next.createdAt
      || previous.updatedAt >= next.updatedAt) return previous;
  return next;
}

export function normalizeAgentEvent(value: unknown): AgentEvent | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.id !== 'string' || !candidate.id || candidate.id.length > 512
      || typeof candidate.kind !== 'string' || !kinds.has(candidate.kind)) return null;
  if (candidate.runId !== undefined && (typeof candidate.runId !== 'string'
    || !/^[A-Za-z0-9_-]{1,512}$/.test(candidate.runId))) return null;
  if (candidate.kind === 'tool' && (typeof candidate.callId !== 'string' || !candidate.callId)) return null;
  if (candidate.kind === 'subagent') {
    const subagent = normalizeAgentSubagent(candidate.subagent);
    if (!subagent || !isSubagentRouteSegment(candidate.turnId)) return null;
    return { id: candidate.id, kind: 'subagent', turnId: candidate.turnId, subagent,
      ...(typeof candidate.runId === 'string' ? { runId: candidate.runId } : {}) };
  }
  return candidate as AgentEvent;
}

export function agentEventKey(event: AgentEvent): string {
  return JSON.stringify([event.runId ?? null, event.id]);
}

export function agentToolKey(event: AgentEvent): string {
  return JSON.stringify([event.runId ?? null, event.callId || event.id]);
}

export function publicationEventKey(event: AgentEvent): string | null {
  const value = event.kind === 'publication' ? event.publication : undefined;
  return value && [value.id, value.actorId, value.projectId, value.payloadHash].every(part => typeof part === 'string')
    ? JSON.stringify([value.id, value.actorId, value.projectId, value.payloadHash, value.operationId || null]) : null;
}

export function businessEventKey(event: AgentEvent): string | null {
  const value = event.kind === 'business' && event.name === 'easel_business_objects' ? event.business : undefined;
  return value && event.callId && [value.actorId, value.projectId, value.kind, value.id].every(part => typeof part === 'string')
    ? JSON.stringify([event.runId ?? null, event.callId, value.actorId, value.projectId, value.kind, value.id]) : null;
}

/** Tool events are cumulative snapshots; older replay must not revive a finished call. */
export function reduceAgentEvents(previous: AgentEvent[], input: unknown): AgentEvent[] {
  const event = normalizeAgentEvent(input);
  if (!event) return previous;
  const business = businessEventKey(event);
  if (business) {
    const index = previous.findIndex(item => businessEventKey(item) === business);
    if (index >= 0) {
      if ((event.business?.observedAt || 0) <= (previous[index].business?.observedAt || 0)) return previous;
      const next = [...previous]; next[index] = event; return next;
    }
  }
  const receipt = publicationEventKey(event);
  if (receipt) {
    const index = previous.findIndex(item => publicationEventKey(item) === receipt);
    if (index >= 0) {
      if ((event.completedAt || 0) < (previous[index].completedAt || 0)) return previous;
      const next = [...previous]; next[index] = event; return next;
    }
  }
  const identity = agentEventKey(event);
  if (previous.some(item => agentEventKey(item) === identity)) return previous;
  if (event.kind === 'subagent' && event.subagent) {
    const index = previous.findIndex(item => item.kind === 'subagent' && item.runId === event.runId && item.turnId === event.turnId
      && item.subagent?.id === event.subagent?.id);
    if (index < 0) return [...previous, event];
    const old = previous[index].subagent;
    const subagent = mergeAgentSubagent(old, event.subagent);
    if (old === subagent) return previous;
    const next = [...previous];
    next[index] = { ...event, subagent };
    return next;
  }
  const index = event.kind === 'tool'
    ? previous.findIndex(item => item.kind === 'tool' && item.runId === event.runId && item.callId === event.callId) : -1;
  if (index < 0) return [...previous, event];
  const old = previous[index];
  if (terminal.has(old.status || '') && !terminal.has(event.status || '')) return previous;
  const next = [...previous];
  next[index] = { ...old, ...event };
  return next;
}
