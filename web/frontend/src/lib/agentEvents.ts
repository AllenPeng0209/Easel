/** Xiangya transport adapter for the DeepSeek Harness tool/trajectory projection.
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

const changeKinds = ['added', 'modified', 'deleted'] as const;
const changeDiffStates = ['text', 'binary', 'oversized', 'unavailable'] as const;
const changeOutcomes = ['completed', 'stopped', 'failed', 'unconfirmed'] as const;
const changeWarnings = ['shared_workspace', 'concurrent_observation', 'scan_limit', 'unreadable', 'summary_limit', 'unsupported'] as const;

export type AgentFileChangeOutcome = typeof changeOutcomes[number];
export interface AgentFileChanges {
  files: {
    path: string;
    kind: typeof changeKinds[number];
    diffStatus: typeof changeDiffStates[number];
    added?: number;
    deleted?: number;
    compareAvailable?: true;
  }[];
  coverage: 'complete' | 'partial';
  warnings: (typeof changeWarnings[number])[];
  total: number;
}

function enumValue<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === 'string' && values.includes(value as T);
}

function validChangePath(value: unknown): value is string {
  if (typeof value !== 'string' || !value || value.length > 1024) return false;
  let path = value;
  for (let pass = 0; pass < 4; pass++) {
    if (path.startsWith('_') || /^[a-z][a-z0-9+.-]*:/i.test(path) || /[\\\p{Cc}\p{Cs}]/u.test(path)
        || path.split('/').some(part => !part || part.startsWith('.'))) return false;
    try {
      const decoded = decodeURIComponent(path);
      if (decoded === path) break;
      path = decoded;
    } catch { break; }
  }
  return true;
}

function nonnegativeCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

export function normalizeAgentFileChanges(value: unknown): AgentFileChanges | null {
  if (!value || typeof value !== 'object') return null;
  const { files, coverage, warnings, total } = value as Record<string, unknown>;
  if ((coverage !== 'complete' && coverage !== 'partial') || !Array.isArray(files) || files.length > 200
      || !nonnegativeCount(total) || total < files.length || (coverage === 'complete' && total !== files.length)
      || !Array.isArray(warnings) || warnings.length > changeWarnings.length
      || !warnings.every(warning => enumValue(changeWarnings, warning))) return null;
  const paths = new Set<string>();
  const rows: AgentFileChanges['files'] = [];
  for (const file of files) {
    if (!file || typeof file !== 'object') return null;
    const { path, kind, diffStatus, added, deleted, compareAvailable } = file;
    if (!validChangePath(path) || paths.has(path) || !enumValue(changeKinds, kind)
        || !enumValue(changeDiffStates, diffStatus)) return null;
    paths.add(path);
    if (diffStatus === 'text' && (!nonnegativeCount(added) || !nonnegativeCount(deleted))) return null;
    rows.push({ path, kind, diffStatus, ...(diffStatus === 'text' ? { added, deleted,
      ...(compareAvailable === true ? { compareAvailable: true as const } : {}) } : {}) });
  }
  return { files: rows, coverage, warnings: [...new Set(warnings)], total };
}

export function latestAgentFileChanges(events: readonly unknown[]): {
  status: AgentFileChangeOutcome; changes: AgentFileChanges; turnId?: string;
} | null {
  for (let index = events.length - 1; index >= 0; index--) {
    const candidate = events[index];
    if (!candidate || typeof candidate !== 'object' || (candidate as AgentEvent).kind !== 'changes') continue;
    const event = normalizeAgentEvent(candidate);
    if (event?.kind === 'changes' && event.changes && enumValue(changeOutcomes, event.status)) {
      return event.changes.files.length || event.changes.coverage === 'partial'
        ? { status: event.status, changes: event.changes, ...(event.turnId ? { turnId: event.turnId } : {}) } : null;
    }
  }
  return null;
}

export interface AgentEvent {
  id: string;
  kind: 'tool' | 'plan' | 'usage' | 'context' | 'decision' | 'artifact' | 'task' | 'publication' | 'business' | 'steer' | 'steer_capability' | 'subagent' | 'changes';
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
  nativePublicationTool?: true;
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
  changes?: AgentFileChanges;
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

const kinds = new Set(['tool', 'plan', 'usage', 'context', 'decision', 'artifact', 'task', 'publication', 'business', 'steer', 'subagent', 'changes']);
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

export function isConfirmedToolStatus(value: unknown): boolean {
  const status = knownToolStatus(value);
  return status !== undefined && status !== 'unconfirmed' && terminal.has(status);
}

/** DeepSeek ToolRow's preparing/start/result split, using Xiangya's observed Host statuses. */
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
  if (candidate.kind === 'changes') {
    const changes = normalizeAgentFileChanges(candidate.changes);
    if (!changes || candidate.source !== 'outputs_snapshot' || !enumValue(changeOutcomes, candidate.status)) return null;
    return { id: candidate.id, kind: 'changes', source: 'outputs_snapshot', status: candidate.status, changes,
      ...(typeof candidate.runId === 'string' ? { runId: candidate.runId } : {}),
      ...(isSubagentRouteSegment(candidate.turnId) ? { turnId: candidate.turnId } : {}) };
  }
  if (candidate.kind === 'subagent') {
    const subagent = normalizeAgentSubagent(candidate.subagent);
    if (!subagent || !isSubagentRouteSegment(candidate.turnId)) return null;
    return { id: candidate.id, kind: 'subagent', turnId: candidate.turnId, subagent,
      ...(typeof candidate.runId === 'string' ? { runId: candidate.runId } : {}) };
  }
  return candidate as AgentEvent;
}

/** A typed native publication observation belongs to one exact tool name and call. */
export function publicationCallKey(event: AgentEvent): string | undefined {
  if ((event.name !== 'easel_publish' && event.name !== 'easel_publication_status') ||
      typeof event.runId !== 'string' || !/^[A-Za-z0-9_-]{1,512}$/.test(event.runId) ||
      typeof event.callId !== 'string' || !event.callId.trim() || event.callId.length > 256 ||
      /\p{Cc}/u.test(event.callId)) return undefined;
  return JSON.stringify([event.runId, event.callId, event.name]);
}

export function nativePublicationToolKey(event: AgentEvent): string | undefined {
  return event.kind === 'tool' && event.nativePublicationTool === true ? publicationCallKey(event) : undefined;
}

export function agentEventKey(event: AgentEvent): string {
  const publicationCall = event.kind === 'publication' ? publicationCallKey(event) : undefined;
  if (publicationCall) return JSON.stringify([publicationCall, event.id]);
  return event.kind === 'tool' && !event.runId && event.turnId
    ? JSON.stringify([null, event.turnId, event.id])
    : JSON.stringify([event.runId ?? null, event.id]);
}

export function agentToolKey(
  event: Pick<AgentEvent, 'id' | 'callId' | 'runId' | 'turnId'>,
  callId = event.callId || event.id,
): string {
  const scope = event.runId ? ['run', event.runId] : ['turn', event.turnId ?? null];
  return JSON.stringify([...scope, callId]);
}

export function mergeToolSnapshots(previous: AgentEvent, incoming: AgentEvent): AgentEvent {
  const parentCallId = previous.parentCallId || incoming.parentCallId;
  const previousStatus = knownToolStatus(previous.status);
  const incomingStatus = knownToolStatus(incoming.status);
  const previousTerminal = terminal.has(previousStatus ?? '');
  const incomingTerminal = terminal.has(incomingStatus ?? '');
  const upgradesUnconfirmed = previousStatus === 'unconfirmed' && isConfirmedToolStatus(incomingStatus);
  const older = typeof previous.timestamp === 'number' && Number.isFinite(previous.timestamp) &&
    typeof incoming.timestamp === 'number' && Number.isFinite(incoming.timestamp) && incoming.timestamp < previous.timestamp;
  const regresses = isConfirmedToolStatus(previousStatus) || previousTerminal && !incomingTerminal
    || previousStatus === 'running' && (incomingStatus === 'preparing' || incomingStatus === 'pending');
  if (upgradesUnconfirmed || !older && !regresses) {
    const context = upgradesUnconfirmed
      ? { ...previous, completedAt: undefined, durationMs: undefined, exitCode: undefined, signal: undefined }
      : previous;
    return { ...context, ...incoming, parentCallId, status: incomingStatus ?? previousStatus };
  }

  // A terminal result can arrive first. Recover only missing call context.
  const context: Partial<AgentEvent> = {};
  const name = previous.name && previous.name !== 'tool' ? previous.name : incoming.name ?? previous.name;
  if (name !== previous.name) context.name = name;
  if (parentCallId !== previous.parentCallId) context.parentCallId = parentCallId;
  for (const field of ['input', 'title', 'toolKind', 'locations', 'startedAt'] as const) {
    if (previous[field] === undefined && incoming[field] !== undefined) {
      Object.assign(context, { [field]: incoming[field] });
    }
  }
  if (name && name === incoming.name) {
    for (const flag of ['nativeBusinessTool', 'nativePublicationTool', 'nativeWebSearch'] as const) {
      if (previous[flag] !== true && incoming[flag] === true) Object.assign(context, { [flag]: true });
    }
  }
  return Object.keys(context).length ? { ...previous, ...context } : previous;
}

export function publicationEventKey(event: AgentEvent): string | null {
  const value = event.kind === 'publication' ? event.publication : undefined;
  if (!value || ![value.id, value.actorId, value.projectId, value.payloadHash].every(part => typeof part === 'string')) return null;
  const durable = [value.id, value.actorId, value.projectId, value.payloadHash, value.operationId || null];
  const correlation = publicationCallKey(event);
  return JSON.stringify(correlation ? [correlation, ...durable] : durable);
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
