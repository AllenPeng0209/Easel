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

export interface AgentEvent {
  id: string;
  kind: 'tool' | 'plan' | 'usage' | 'context' | 'decision' | 'artifact' | 'task' | 'publication' | 'business';
  business?: import('./businessObjects').BusinessObject;
  publication?: import('./publicationApproval').PublicationApprovalRecord;
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

const kinds = new Set(['tool', 'plan', 'usage', 'context', 'decision', 'artifact', 'task', 'publication', 'business']);
const terminal = new Set(['completed', 'failed', 'interrupted', 'cancelled', 'stopped']);

export function normalizeAgentEvent(value: unknown): AgentEvent | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.id !== 'string' || !candidate.id || candidate.id.length > 512
      || typeof candidate.kind !== 'string' || !kinds.has(candidate.kind)) return null;
  if (candidate.kind === 'tool' && (typeof candidate.callId !== 'string' || !candidate.callId)) return null;
  return candidate as AgentEvent;
}

export function publicationEventKey(event: AgentEvent): string | null {
  const value = event.kind === 'publication' ? event.publication : undefined;
  return value && [value.id, value.actorId, value.projectId, value.payloadHash].every(part => typeof part === 'string')
    ? JSON.stringify([value.id, value.actorId, value.projectId, value.payloadHash, value.operationId || null]) : null;
}

export function businessEventKey(event: AgentEvent): string | null {
  const value = event.kind === 'business' && event.name === 'easel_business_objects' ? event.business : undefined;
  return value && event.callId && [value.actorId, value.projectId, value.kind, value.id].every(part => typeof part === 'string')
    ? JSON.stringify([event.callId, value.actorId, value.projectId, value.kind, value.id]) : null;
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
  if (previous.some(item => item.id === event.id)) return previous;
  const index = event.kind === 'tool'
    ? previous.findIndex(item => item.kind === 'tool' && item.callId === event.callId) : -1;
  if (index < 0) return [...previous, event];
  const old = previous[index];
  if (terminal.has(old.status || '') && !terminal.has(event.status || '')) return previous;
  const next = [...previous];
  next[index] = { ...old, ...event };
  return next;
}
