/** Easel transport adapter for the DeepSeek Harness tool/trajectory projection.
 * Upstream: deepseek-ai/deepseek-harness @ ce7073d (MIT), ui-tool / ui-trajectory.
 * Engine events stay structured across live delivery, replay and persisted chat.
 */
export interface AgentEvent {
  id: string;
  kind: 'tool' | 'plan' | 'usage' | 'context' | 'decision' | 'artifact' | 'task';
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
  [key: string]: unknown;
}

const kinds = new Set(['tool', 'plan', 'usage', 'context', 'decision', 'artifact', 'task']);
const terminal = new Set(['completed', 'failed', 'interrupted', 'cancelled', 'stopped']);

export function normalizeAgentEvent(value: unknown): AgentEvent | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.id !== 'string' || !candidate.id || candidate.id.length > 512
      || typeof candidate.kind !== 'string' || !kinds.has(candidate.kind)) return null;
  if (candidate.kind === 'tool' && (typeof candidate.callId !== 'string' || !candidate.callId)) return null;
  return candidate as AgentEvent;
}

/** Tool events are cumulative snapshots; older replay must not revive a finished call. */
export function reduceAgentEvents(previous: AgentEvent[], input: unknown): AgentEvent[] {
  const event = normalizeAgentEvent(input);
  if (!event || previous.some(item => item.id === event.id)) return previous;
  const index = event.kind === 'tool'
    ? previous.findIndex(item => item.kind === 'tool' && item.callId === event.callId) : -1;
  if (index < 0) return [...previous, event];
  const old = previous[index];
  if (terminal.has(old.status || '') && !terminal.has(event.status || '')) return previous;
  const next = [...previous];
  next[index] = { ...old, ...event };
  return next;
}
