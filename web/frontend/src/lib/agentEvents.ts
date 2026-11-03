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
  outputStream?: 'merged';
  fileChangeApplied?: boolean;
  nativeWebSearch?: true;
  exitCode?: number | null;
  durationMs?: number;
  signal?: string;
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

export function fileMutationChanges(event: AgentEvent): unknown[] | undefined {
  if (event.name !== 'apply_patch' || typeof event.fileChangeApplied !== 'boolean'
      || !event.input || typeof event.input !== 'object') return undefined;
  const changes = (event.input as Record<string, unknown>).changes;
  return Array.isArray(changes) ? changes : undefined;
}

export const fileMutationLabel = {
  applied: '引擎报告已应用',
  attempted: '待确认应用结果',
  failed: '引擎报告应用失败',
  declined: '引擎报告已拒绝',
  unconfirmed: '应用结果未确认',
};

export type FileMutationState = keyof typeof fileMutationLabel;

export function fileMutationState(event: AgentEvent, settled: boolean): FileMutationState | undefined {
  if (fileMutationChanges(event) === undefined) return undefined;
  if (event.fileChangeApplied === true && event.status === 'completed') return 'applied';
  if (event.status === 'failed') return 'failed';
  if (event.status === 'declined' || event.status === 'denied') return 'declined';
  return !settled && ['running', 'preparing', 'pending', 'in_progress'].includes(event.status || '')
    ? 'attempted' : 'unconfirmed';
}

export function isCommandExecution(event: AgentEvent): boolean {
  return event.outputStream === 'merged' && (event.output === undefined || typeof event.output === 'string')
    && (event.input === undefined || (event.input !== null && typeof event.input === 'object'
      && typeof (event.input as Record<string, unknown>).command === 'string'));
}

export function commandSignal(event: AgentEvent): string | undefined {
  return typeof event.signal === 'string' && /^SIG[A-Z0-9]{1,16}$/.test(event.signal) ? event.signal : undefined;
}

export function commandExitCode(event: AgentEvent): number | null | undefined {
  return event.exitCode === null || (typeof event.exitCode === 'number' && Number.isSafeInteger(event.exitCode))
    ? event.exitCode : undefined;
}

/** Upstream bash-sample dispatches verified terminal calls and promotes nonzero exits to failure. */
export function commandStatus(event: AgentEvent, status: string): string {
  const code = commandExitCode(event);
  return status === 'completed' && (commandSignal(event) || (typeof code === 'number' && code !== 0))
    ? 'failed' : status;
}

const kinds = new Set(['tool', 'plan', 'usage', 'context', 'decision', 'artifact', 'task', 'publication', 'business', 'steer', 'subagent']);
const terminalStatuses = ['completed', 'failed', 'interrupted', 'cancelled', 'stopped', 'denied', 'declined'] as const;
type ObservedTerminalStatus = typeof terminalStatuses[number];
const terminal = new Set<string>([...terminalStatuses, 'unconfirmed']);
type ToolStatus = ObservedTerminalStatus | 'preparing' | 'pending' | 'running' | 'unconfirmed';
type ToolPhase = 'preparing' | 'running' | 'terminal' | 'unconfirmed';

function knownToolStatus(value: unknown): ToolStatus | undefined {
  if (value === 'in_progress') return 'running';
  if (value === 'preparing' || value === 'pending' || value === 'running') return value;
  return typeof value === 'string' && terminal.has(value) ? value as ToolStatus : undefined;
}

/** DeepSeek ToolRow's preparing/start/result split, using Easel's observed Host statuses. */
export function toolLifecycle(value: unknown, settled: boolean): {
  status: ToolStatus; phase: ToolPhase; awaitingTerminal: boolean;
} {
  const status = knownToolStatus(value) ?? 'unconfirmed';
  const phase = status === 'preparing' || status === 'pending' ? 'preparing'
    : status === 'running' ? 'running' : status === 'unconfirmed' ? 'unconfirmed' : 'terminal';
  const awaitingTerminal = settled && (phase === 'preparing' || phase === 'running');
  return { status, phase: awaitingTerminal ? 'unconfirmed' : phase, awaitingTerminal };
}

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
  return event.kind === 'tool' && !event.runId && event.turnId
    ? JSON.stringify([null, event.turnId, event.id])
    : JSON.stringify([event.runId ?? null, event.id]);
}

export function agentToolKey(event: AgentEvent, callId = event.callId || event.id): string {
  const scope = event.runId ? ['run', event.runId] : ['turn', event.turnId ?? null];
  return JSON.stringify([...scope, callId]);
}

export function mergeToolSnapshots(previous: AgentEvent, incoming: AgentEvent): AgentEvent {
  const parentCallId = previous.parentCallId || incoming.parentCallId;
  const previousStatus = knownToolStatus(previous.status);
  const incomingStatus = knownToolStatus(incoming.status);
  const previousTerminal = terminal.has(previousStatus ?? '');
  const incomingTerminal = terminal.has(incomingStatus ?? '');
  const older = typeof previous.timestamp === 'number' && Number.isFinite(previous.timestamp) &&
    typeof incoming.timestamp === 'number' && Number.isFinite(incoming.timestamp) && incoming.timestamp < previous.timestamp;
  const regresses = previousTerminal && (!incomingTerminal || (previousStatus !== 'unconfirmed' && incomingStatus === 'unconfirmed'))
    || previousStatus === 'running' && (incomingStatus === 'preparing' || incomingStatus === 'pending');
  if (!older && !regresses) {
    return { ...previous, ...incoming, parentCallId, status: incomingStatus ?? previousStatus };
  }
  // A result can arrive first. Recover only the earlier call's missing context.
  const name = previous.name ?? incoming.name;
  const input = previous.input === undefined ? incoming.input : previous.input;
  const startedAt = previous.startedAt ?? incoming.startedAt;
  return parentCallId !== previous.parentCallId || name !== previous.name ||
      input !== previous.input || startedAt !== previous.startedAt
    ? { ...previous, parentCallId, name, input, startedAt } : previous;
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
    ? previous.findIndex(item => item.kind === 'tool' && agentToolKey(item) === agentToolKey(event)) : -1;
  if (index < 0) return [...previous, event];
  const merged = mergeToolSnapshots(previous[index], event);
  if (merged === previous[index]) return previous;
  const next = [...previous];
  next[index] = merged;
  return next;
}
