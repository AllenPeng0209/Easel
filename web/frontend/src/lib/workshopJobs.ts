/**
 * IP-wide, read-only roster adapter for the DeepSeek Harness ui-jobs pattern.
 * All progress, elapsed time and artifact identities come from Host observations.
 */
import { ACTIVE_PROJECT_ID, projectFetch } from './projectScope';

export const WORKSHOP_JOB_LABELS = {
  queued: '排队中', running: '运行中', awaiting_collection: '待收集成片', awaiting_review: '待审核',
  changes_requested: '待修改', stopping: '正在取消', failed: '失败', completed: '已完成', cancelled: '已取消', unconfirmed: '状态未确认',
} as const;

type WorkshopJobStatus = keyof typeof WORKSHOP_JOB_LABELS;

const cancellationStatuses = ['pending', 'unconfirmed', 'cancelled', 'already_finished'] as const;
const renderStatuses = ['queued', 'running', 'succeeded', 'failed', 'cancelled'] as const;

export interface WorkshopJobCancellation {
  status: typeof cancellationStatuses[number];
  taskId: string;
  renderId: string;
  jobStatus: typeof renderStatuses[number];
  requestedAt: number | null;
  updatedAt: number | null;
}

export interface WorkshopJob {
  id: string;
  workshopProjectId: string;
  title: string;
  taskId: string | null;
  status: WorkshopJobStatus;
  progress: number | null;
  createdAt: number | null;
  updatedAt: number | null;
  finishedAt: number | null;
  elapsedMs: number | null;
  artifactId: string | null;
  artifactSha256: string | null;
  contentRevision: number | null;
  engineRevision: number | null;
  canCancel: boolean;
  cancellation?: WorkshopJobCancellation;
}

export interface WorkshopJobRoster {
  jobs: WorkshopJob[];
  incomplete: boolean;
}

const ID = /^[a-f0-9]{32}$/;
const HASH = /^[a-f0-9]{64}$/;
const boundedText = (value: unknown, max: number): value is string => typeof value === 'string' && !!value.trim()
  && value.length <= max && !/\p{Cc}/u.test(value);
const integer = (value: unknown): number | null => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;

function normalizeCancellation(value: unknown, renderId: string, taskId: string | null): WorkshopJobCancellation | undefined {
  if (!value || typeof value !== 'object' || !taskId) return undefined;
  const row = value as Record<string, unknown>;
  const status = cancellationStatuses.find(candidate => candidate === row.status);
  const jobStatus = renderStatuses.find(candidate => candidate === row.jobStatus);
  if (!status || !jobStatus || row.renderId !== renderId || row.taskId !== taskId
      || status === 'cancelled' && jobStatus !== 'cancelled'
      || status === 'already_finished' && (jobStatus === 'queued' || jobStatus === 'running')) return undefined;
  return { status, jobStatus, renderId, taskId, requestedAt: integer(row.requestedAt), updatedAt: integer(row.updatedAt) };
}

function normalizeJob(value: unknown): WorkshopJob | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (typeof row.id !== 'string' || !ID.test(row.id) || typeof row.workshopProjectId !== 'string' || !ID.test(row.workshopProjectId)
      || !boundedText(row.title, 1024) || typeof row.status !== 'string' || !Object.hasOwn(WORKSHOP_JOB_LABELS, row.status)) return null;
  const taskId = boundedText(row.taskId, 256) ? row.taskId : null;
  return {
    id: row.id, workshopProjectId: row.workshopProjectId, title: row.title, taskId,
    status: row.status as WorkshopJobStatus,
    progress: typeof row.progress === 'number' && Number.isFinite(row.progress) && row.progress >= 0 && row.progress <= 1
      ? row.progress : null,
    createdAt: integer(row.createdAt), updatedAt: integer(row.updatedAt), finishedAt: integer(row.finishedAt),
    elapsedMs: integer(row.elapsedMs),
    artifactId: typeof row.artifactId === 'string' && ID.test(row.artifactId) ? row.artifactId : null,
    artifactSha256: typeof row.artifactSha256 === 'string' && HASH.test(row.artifactSha256) ? row.artifactSha256 : null,
    contentRevision: integer(row.contentRevision), engineRevision: integer(row.engineRevision),
    canCancel: row.canCancel === true && taskId !== null,
    cancellation: normalizeCancellation(row.cancellation, row.id, taskId),
  };
}

export class WorkshopJobsReadError extends Error {
  readonly scopeUnavailable: boolean;

  constructor(scopeUnavailable: boolean) {
    super('Workshop jobs read failed');
    this.scopeUnavailable = scopeUnavailable;
  }
}

export async function fetchWorkshopJobs(signal: AbortSignal): Promise<WorkshopJobRoster> {
  const response = await projectFetch('/api/workshop/jobs', { signal, cache: 'no-store' });
  if (!response.ok) throw new WorkshopJobsReadError(response.status === 401 || response.status === 403);
  const result: unknown = await response.json();
  if (!result || typeof result !== 'object' || !('projectId' in result) || result.projectId !== ACTIVE_PROJECT_ID
      || !('jobs' in result) || !Array.isArray(result.jobs)) throw new WorkshopJobsReadError(true);
  const jobs: WorkshopJob[] = [];
  const seen = new Set<string>();
  for (const value of result.jobs) {
    const job = normalizeJob(value);
    if (!job) continue;
    const key = JSON.stringify([job.workshopProjectId, job.id]);
    if (seen.has(key)) continue;
    seen.add(key);
    jobs.push(job);
  }
  return { jobs, incomplete: jobs.length !== result.jobs.length };
}

export function workshopJobElapsed(ms: number | null): string {
  if (ms === null) return '耗时未知';
  if (ms < 1000) return `${ms} ms`;
  const seconds = Math.floor(ms / 1000);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds / 60) % 60;
  return `${hours ? `${hours} 小时 ` : ''}${minutes ? `${minutes} 分 ` : ''}${seconds % 60} 秒`;
}

/** Keep observation fair even when recent provider updates reorder the roster. */
export function nextWorkshopJobObservations(jobs: readonly WorkshopJob[], after?: string) {
  const active = jobs.filter(job => job.taskId && (job.status === 'queued' || job.status === 'running'))
    .map(job => ({ key: `${job.workshopProjectId}:${job.id}`, job }))
    .sort((left, right) => left.key.localeCompare(right.key));
  const next = after === undefined ? 0 : active.findIndex(row => row.key > after);
  const start = Math.max(0, next);
  const rows = [...active.slice(start), ...active.slice(0, start)].slice(0, 3);
  return { jobs: rows.map(row => row.job), cursor: rows.at(-1)?.key ?? after };
}

/** Refresh only the exact confirmed receipt; its provider response is not a roster row. */
export async function observeWorkshopJob(job: WorkshopJob, signal: AbortSignal): Promise<void> {
  if (!ID.test(job.workshopProjectId) || !ID.test(job.id) || !job.taskId
      || job.status !== 'queued' && job.status !== 'running') return;
  signal.throwIfAborted();
  const response = await projectFetch(`/api/workshop/projects/${job.workshopProjectId}/renders/${job.id}`,
    { signal, cache: 'no-store' });
  if (!response.ok) throw new WorkshopJobsReadError(false);
}


export class WorkshopCancelRequestError extends Error {
  readonly status: number;
  constructor(status: number) {
    super('Workshop cancellation was not confirmed');
    this.status = status;
  }
}

export async function requestWorkshopJobCancellation(job: WorkshopJob, signal: AbortSignal): Promise<WorkshopJobCancellation> {
  if (!job.canCancel || !ID.test(job.workshopProjectId) || !ID.test(job.id) || !job.taskId) {
    throw new WorkshopCancelRequestError(409);
  }
  const response = await projectFetch(`/api/workshop/projects/${job.workshopProjectId}/renders/${job.id}/cancel`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
    body: JSON.stringify({ taskId: job.taskId }),
  });
  if (!response.ok) throw new WorkshopCancelRequestError(response.status);
  const result = normalizeCancellation(await response.json(), job.id, job.taskId);
  if (!result) throw new WorkshopCancelRequestError(502);
  return result;
}
