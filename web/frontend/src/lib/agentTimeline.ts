// Adapted from DeepSeek Harness ui-chat's ordered conversation timeline.
// Keep observation metadata separate from the latest result snapshots.
import { agentToolKey, commandStatus, isCommandExecution, isConfirmedToolStatus, normalizeAgentEvent } from './agentEvents';

const kinds = ['tool', 'decision', 'plan', 'context', 'subagent', 'artifact', 'business', 'publication', 'steer'] as const;
const MAX_OBSERVATIONS = 256;

export interface AgentTimelineObservation {
  id: string;
  runId?: string;
  turnId?: string;
  kind: typeof kinds[number];
  callId?: string;
  questionId?: string;
  name?: string;
  status?: string;
  resolvedBy?: 'human' | 'default' | 'timeout';
  observedAt?: number;
  earlierObservationsOmitted?: true;
}

function text(value: unknown, max: number): string | undefined {
  return typeof value === 'string' && value.length > 0 && value.length <= max && !/\p{Cc}/u.test(value)
    ? value : undefined;
}

function append(previous: AgentTimelineObservation[], row: AgentTimelineObservation): AgentTimelineObservation[] {
  if (previous.some(item => agentToolKey(item, item.id) === agentToolKey(row, row.id))) return previous;
  if (row.kind === 'tool') {
    const last = previous.findLast(item => item.kind === 'tool' && agentToolKey(item) === agentToolKey(row));
    if (last?.status && row.status && (last.status === row.status || isConfirmedToolStatus(last.status)
        || last.status === 'unconfirmed' && !isConfirmedToolStatus(row.status)
        || last.status === 'running' && row.status === 'preparing')) return previous;
  }
  if (previous.length < MAX_OBSERVATIONS) return [...previous, row];
  const retained = previous.slice(-(MAX_OBSERVATIONS - 1));
  retained[0] = { ...retained[0], earlierObservationsOmitted: true };
  return [...retained, row];
}

export function observeAgentTimeline(previous: AgentTimelineObservation[], value: unknown): AgentTimelineObservation[] {
  const event = normalizeAgentEvent(value);
  const id = text(event?.id, 512);
  if (!event || !id || event.kind === 'usage' || event.kind === 'task' || event.kind === 'steer_capability') return previous;
  const callId = text(event.callId, 256);
  const reported = text(event.subagent?.status ?? event.publication?.result?.status ?? event.publication?.state ?? event.status, 32);
  const status = event.kind === 'tool' && reported && isCommandExecution(event) ? commandStatus(event, reported) : reported;
  if (event.kind === 'tool' && (!callId || !status)) return previous;
  const name = text(event.name, 120);
  const turnId = text(event.turnId, 256);
  const questionId = text(event.questionId, 128);
  const source = event.kind === 'decision' ? event.resolvedBy ?? event.source : undefined;
  const time = event.subagent?.updatedAt ?? event.completedAt ?? event.startedAt ?? event.timestamp;
  return append(previous, {
    id, kind: event.kind,
    ...(event.runId ? { runId: event.runId } : {}),
    ...(turnId ? { turnId } : {}),
    ...(callId ? { callId } : {}),
    ...(questionId ? { questionId } : {}),
    ...(name ? { name } : {}),
    ...(status ? { status } : {}),
    ...(source === 'human' || source === 'default' || source === 'timeout' ? { resolvedBy: source } : {}),
    ...(typeof time === 'number' && Number.isSafeInteger(time) && time >= 0 ? { observedAt: time } : {}),
  });
}

/** A server snapshot preserves original journal order. Retain any newer live-only observations. */
export function recoverAgentTimeline(events: readonly unknown[] | undefined,
  live: AgentTimelineObservation[] = []): AgentTimelineObservation[] {
  if (!events?.length) return live;
  const observedIds = new Set<string>();
  let observed: AgentTimelineObservation[] = [];
  for (const value of events) {
    const event = normalizeAgentEvent(value);
    if (!event) continue;
    observedIds.add(agentToolKey(event, event.id));
    observed = observeAgentTimeline(observed, event);
  }
  return live.reduce((rows, row) => observedIds.has(agentToolKey(row, row.id)) ? rows : append(rows, row), observed);
}
