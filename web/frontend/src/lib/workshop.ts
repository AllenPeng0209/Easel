import type { PublicationApprovalSubject } from './publicationApproval';
import { projectUrl, projectFetch } from './projectScope';
export type ReviewKind = 'human' | 'simulated';
export type ReviewDecision = 'approved' | 'changes_requested';

export interface WorkshopReference {
  id: string;
  revision: number;
  shotId?: string;
  artifactId?: string;
  artifactSha256?: string;
}

export interface WorkshopStatus {
  state: 'running' | 'unavailable' | 'starting';
  ui_url?: string;
  version?: string;
  message?: string;
}

export interface WorkshopDeliverable {
  id: string;
  path: string;
  sha256: string;
  revision: number;
  created_at: string;
}

export interface WorkshopReview {
  id: string;
  artifact_id?: string;
  revision: number;
  artifact_sha256: string;
  decision: ReviewDecision;
  kind: ReviewKind;
  notes: string;
  created_at: string;
}

export interface WorkshopPublication {
  id: string;
  revision: number;
  artifact_id: string;
  artifact_sha256: string;
  platform: string;
  url: string;
  published_at: string;
  created_at: string;
}

export interface WorkshopComment {
  id: string;
  revision: number;
  at_seconds: number;
  notes: string;
  kind: ReviewKind;
  created_at: string;
}

export const workshopSubmissionErrors = {
  submission_unconfirmed: '提交结果未确认',
  submission_timeout: '提交等待超时',
  invalid_receipt: '任务回执无效',
  source_revision_mismatch: '来源版本不一致',
  submission_interrupted: '提交过程已中断',
} as const;

export interface WorkshopSubmissionAttempt {
  number: number;
  status: 'submitting' | 'unconfirmed' | 'confirmed';
  started_at: string;
  settled_at?: string;
  task_id?: string;
  error_code?: keyof typeof workshopSubmissionErrors;
}

const submissionTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:\+00:00|Z)$/;

export function normalizeWorkshopSubmissionAttempts(value: unknown): WorkshopSubmissionAttempt[] {
  if (!Array.isArray(value)) return [];
  const attempts = new Map<number, WorkshopSubmissionAttempt>();
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') continue;
    const { number, status, started_at, settled_at, task_id, error_code } = entry as Partial<WorkshopSubmissionAttempt>;
    if (typeof number !== 'number' || !Number.isSafeInteger(number) || number < 1 || attempts.has(number)
      || (status !== 'submitting' && status !== 'unconfirmed' && status !== 'confirmed')
      || typeof started_at !== 'string' || !submissionTime.test(started_at)) continue;
    attempts.set(number, {
      number, status, started_at,
      ...(status !== 'submitting' && typeof settled_at === 'string' && submissionTime.test(settled_at) ? { settled_at } : {}),
      ...(status === 'confirmed' && typeof task_id === 'string' && /^[A-Za-z0-9:_-]{1,160}$/.test(task_id) ? { task_id } : {}),
      ...(status === 'unconfirmed' && typeof error_code === 'string' && Object.hasOwn(workshopSubmissionErrors, error_code) ? { error_code } : {}),
    });
  }
  return [...attempts.values()].sort((first, next) => first.number - next.number);
}

export function workshopProgress(receipt?: { progress?: unknown; progress_known?: unknown }): number | null {
  const progress = receipt?.progress;
  return receipt?.progress_known === true && typeof progress === 'number' && progress >= 0 && progress <= 1
    ? progress : null;
}

export interface WorkshopCancellation {
  status: 'pending' | 'unconfirmed' | 'cancelled' | 'already_finished';
  taskId: string;
  renderId: string;
  jobStatus: WorkshopRenderReceipt['status'];
  requestedAt: number | null;
  updatedAt: number | null;
}

export function workshopCancellationLabel(receipt: {
  id?: unknown; task_id?: unknown; status?: unknown; cancellation?: unknown;
}): string | null {
  if (!receipt.cancellation || typeof receipt.cancellation !== 'object') return null;
  const detail = receipt.cancellation as Partial<WorkshopCancellation>;
  if (typeof detail.taskId !== 'string' || !detail.taskId || detail.taskId !== receipt.task_id
      || typeof detail.renderId !== 'string' || !detail.renderId || detail.renderId !== receipt.id
      || detail.jobStatus !== receipt.status) return null;
  if (detail.status === 'pending') return '正在停止';
  if (detail.status === 'unconfirmed') return '停止结果待核对';
  if (detail.status === 'cancelled' && receipt.status === 'cancelled') return '已确认停止';
  if (detail.status === 'already_finished' && (receipt.status === 'succeeded' || receipt.status === 'failed')) return '任务已先结束';
  return null;
}

export interface WorkshopRenderReceipt {
  id: string;
  operation_id: string;
  task_id: string;
  content_revision: number;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  submission_state?: 'unconfirmed' | 'confirmed';
  submission_attempts?: WorkshopSubmissionAttempt[];
  progress: number;
  progress_known?: boolean;
  cancellation?: WorkshopCancellation;
  completed_at?: string;
  message?: string;
  created_at: string;
  updated_at: string;
  artifact_id?: string;
  collected_revision?: number;
}

export interface WorkshopAsset {
  id: string; kind: 'character' | 'scene' | 'prop' | 'image' | 'video' | 'audio' | 'subtitle' | 'document';
  name: string; description?: string; path: string; sha256?: string;
  versions: { path: string; sha256: string; created_at: string }[];
}
export interface WorkshopCandidate { id: string; path: string; sha256: string; created_at?: string; prompt?: string; }
export interface WorkshopShot {
  id: string; title: string; prompt: string; seconds: number; asset_ids: string[];
  primary_frame_asset_id?: string | null;
  candidates: WorkshopCandidate[]; selected_candidate_id?: string | null;
}
export const publicationResultLabel = (status?: string) => ({ executing: '正在发布', submitted: '已提交，等待平台回执',
  confirmed: '已公开发布', unknown: '发布结果待核对', failed: '发布失败', draft_created: '平台草稿已创建',
  scheduled: '已安排发布' }[status || ''] || '等待发布回执');

export interface WorkshopProject {
  id: string;
  title: string;
  brief: string;
  reference_url: string;
  aspect_ratio: string;
  revision: number;
  stage: string;
  script: string;
  assets: WorkshopAsset[];
  shots: WorkshopShot[];
  publication_preparation?: { approval_id: string; payload_hash: string; revision: number; artifact_id: string; subject: PublicationApprovalSubject } | null;
  publication_runs: { approval_id: string; revision?: number; artifact_id?: string; artifact_sha256?: string; result?: { status: string; message?: string; url?: string } | null }[];
  deliverables: WorkshopDeliverable[];
  reviews: WorkshopReview[];
  comments?: WorkshopComment[];
  renders?: WorkshopRenderReceipt[];
  created_at?: string;
  updated_at?: string;
  archived_at?: string | null;
  publications?: WorkshopPublication[];
}

export type WorkshopLifecycleKey = 'draft' | 'producing' | 'pending_review'
  | 'ready_to_publish' | 'publishing' | 'published' | 'archived';

export const WORKSHOP_LIFECYCLE_STATES: readonly { key: WorkshopLifecycleKey; label: string }[] = [
  { key: 'draft', label: '草稿' }, { key: 'producing', label: '制作中' },
  { key: 'pending_review', label: '待审核' },
  { key: 'ready_to_publish', label: '待发布' }, { key: 'publishing', label: '发布中' }, { key: 'published', label: '已发布' },
];

/** List state is derived from exact deliveries, reviews and publication receipts. */
export function projectLifecycle(project: WorkshopProject): {
  key: WorkshopLifecycleKey; label: string;
  tone: 'neutral' | 'active' | 'warning' | 'success' | 'danger'; detail: string;
} {
  const state = (key: WorkshopLifecycleKey, tone: 'neutral' | 'active' | 'warning' | 'success' | 'danger', detail: string) =>
    ({ key, label: key === 'archived' ? '已归档' : WORKSHOP_LIFECYCLE_STATES.find(item => item.key === key)!.label, tone, detail });
  if (project.archived_at) return state('archived', 'neutral', '项目已归档，可恢复继续制作');
  const artifact = project.deliverables?.at(-1);
  const render = [...(project.renders || [])].reverse().find(item => !item.artifact_id
    && item.content_revision === project.revision);
  if (render && ['queued', 'running', 'succeeded'].includes(render.status)) {
    return state('producing', 'active', render.status === 'succeeded' ? '成片导出完成，等待收取'
      : render.status === 'running' ? `正在导出 · ${Math.round(Math.max(0, Math.min(1, render.progress || 0)) * 100)}%` : '导出任务排队中');
  }
  if (render && ['failed', 'cancelled'].includes(render.status)) return state('producing', 'danger',
    render.submission_state === 'unconfirmed' ? '导出提交结果待核对' : render.status === 'cancelled' ? '导出已取消，可进入项目重试' : '导出失败，可进入项目检查');
  if (artifact && artifact.revision !== project.revision) return state('producing', 'warning', '项目已有修改，等待交付新版本');
  const run = project.publication_runs?.at(-1);
  if (run?.result && artifact && artifact.revision === project.revision && run.revision === project.revision && run.artifact_id === artifact.id && run.artifact_sha256 === artifact.sha256) {
    if (run.result.status === 'confirmed') return state('published', 'success', publicationResultLabel('confirmed'));
    if (['executing', 'submitted', 'unknown', 'failed'].includes(run.result.status)) return state('publishing',
      run.result.status === 'failed' ? 'danger' : 'active', publicationResultLabel(run.result.status));
  }
  if (artifact) {
    const publication = project.publications?.find(item => item.revision === project.revision
      && item.artifact_id === artifact.id && item.artifact_sha256 === artifact.sha256);
    if (publication) return state('published', 'success', `已记录发布至 ${publication.platform}`);
    const review = [...(project.reviews || [])].reverse().find(item => item.revision === project.revision
      && item.artifact_sha256 === artifact.sha256 && (!item.artifact_id || item.artifact_id === artifact.id));
    if (review?.decision === 'changes_requested') return state('producing', 'warning', '已有修改意见，等待返修');
    if (review?.kind === 'human' && review.decision === 'approved') return state('ready_to_publish', 'success', '人工审核已通过，等待发布');
    return state('pending_review', 'warning', review?.kind === 'simulated' && review.decision === 'approved'
      ? '模拟预审已通过，等待人工审核' : '已有成片，等待人工审核');
  }
  if (['failed', 'error'].includes(project.stage)) return state('producing', 'danger', '制作遇到问题，请进入项目检查');
  if (['planning', 'generating', 'editing', 'producing'].includes(project.stage)) return state('producing', 'active',
    project.stage === 'planning' ? '正在策划脚本与分镜' : project.stage === 'generating' ? '正在生成镜头素材' : '正在制作与编辑');
  return state('draft', 'neutral', '在 AI 对话中交办制作任务');
}

export function filterWorkshopProjects(projects: WorkshopProject[], query = '', status: WorkshopLifecycleKey | 'all' = 'all'): WorkshopProject[] {
  const needle = query.trim().toLocaleLowerCase();
  const timestamp = (project: WorkshopProject) => {
    const value = Date.parse(project.updated_at || project.created_at || '');
    return Number.isFinite(value) ? value : 0;
  };
  return projects.filter(project => {
    const lifecycle = projectLifecycle(project).key;
    return (status === 'all' ? lifecycle !== 'archived' : lifecycle === status)
      && (!needle || `${project.title}\n${project.brief}`.toLocaleLowerCase().includes(needle));
  }).sort((a, b) => timestamp(b) - timestamp(a));
}

export interface WorkshopProjectInput {
  title: string;
  brief: string;
  reference_url: string;
  aspect_ratio: string;
}

export interface WorkshopProjectPatch {
  expected_revision: number;
  title?: string;
  brief?: string;
  reference_url?: string;
  aspect_ratio?: string;
}

class WorkshopRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const result = await projectFetch(projectUrl(`/api/workshop${path}`), {
    cache: 'no-store',
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  const data = await result.json().catch(() => null);
  if (!result.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail
      : typeof detail?.message === 'string' ? detail.message
      : typeof data?.message === 'string' ? data.message
      : result.status === 409 ? '项目已发生变化，请刷新后核对再提交。' : `工坊请求失败（${result.status}）`;
    throw new WorkshopRequestError(message, result.status);
  }
  return data as T;
}

export function fetchWorkshopStatus(signal?: AbortSignal): Promise<WorkshopStatus> {
  return request('/status', { signal });
}

export function fetchWorkshopProjects(signal?: AbortSignal): Promise<{ projects: WorkshopProject[] }> {
  return request('/projects', { signal });
}

export function fetchWorkshopProject(id: string, signal?: AbortSignal): Promise<WorkshopProject> {
  return request(`/projects/${encodeURIComponent(id)}`, { signal });
}

export async function fetchWorkshopProjectsWithActive(selectedId = '', signal?: AbortSignal): Promise<{ projects: WorkshopProject[] }> {
  const { projects } = await fetchWorkshopProjects(signal);
  const selected = projects.find(project => project.id === selectedId) || projects[0];
  if (!selected) return { projects };
  let detail: WorkshopProject;
  try {
    detail = await fetchWorkshopProject(selected.id, signal);
  } catch (cause) {
    if (signal?.aborted) throw cause;
    throw cause;
  }
  return { projects: projects.map(project => project.id === selected.id ? detail : project) };
}

export function createWorkshopProject(input: WorkshopProjectInput): Promise<WorkshopProject> {
  return request('/projects', { method: 'POST', body: JSON.stringify(input) });
}

export function updateWorkshopProject(id: string, patch: WorkshopProjectPatch): Promise<WorkshopProject> {
  return request(`/projects/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(patch) });
}

export function archiveWorkshopProject(id: string, input: {
  expected_revision: number; archived: boolean;
}): Promise<WorkshopProject> {
  return request(`/projects/${encodeURIComponent(id)}/archive`, { method: 'POST', body: JSON.stringify(input) });
}

export function recordWorkshopPublication(id: string, input: {
  expected_revision: number; artifact_id: string; platform: string; url: string; published_at?: string;
}): Promise<WorkshopProject> {
  return request(`/projects/${encodeURIComponent(id)}/publications`, { method: 'POST', body: JSON.stringify(input) });
}

export function addWorkshopComment(id: string, input: {
  expected_revision: number; at_seconds: number; notes: string; kind: ReviewKind; shot_id?: string;
}): Promise<WorkshopProject> {
  return request(`/projects/${encodeURIComponent(id)}/comments`, { method: 'POST', body: JSON.stringify(input) });
}

export function addWorkshopReview(id: string, input: {
  expected_revision: number; artifact_id: string; decision: ReviewDecision; kind: ReviewKind; notes: string; publication_approval_id?: string; publication_payload_hash?: string;
}): Promise<WorkshopProject> {
  return request(`/projects/${encodeURIComponent(id)}/reviews`, { method: 'POST', body: JSON.stringify(input) });
}

function renderPath(projectId: string, renderId?: string) {
  return `/projects/${encodeURIComponent(projectId)}/renders${renderId ? `/${encodeURIComponent(renderId)}` : ''}`;
}

function retainRenderReceipt(current: WorkshopRenderReceipt, candidate: WorkshopRenderReceipt) {
  if (current.artifact_id && !candidate.artifact_id) return true;
  const currentTime = Date.parse(current.updated_at);
  const candidateTime = Date.parse(candidate.updated_at);
  if (Number.isFinite(currentTime) && Number.isFinite(candidateTime)) {
    if (candidateTime !== currentTime) return candidateTime < currentTime;
    // Python receipts retain microseconds; Date.parse only retains milliseconds.
    const remainder = (value: string) => (value.match(/\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/)?.[1] || '')
      .slice(3, 9).padEnd(6, '0');
    const currentRemainder = remainder(current.updated_at);
    const candidateRemainder = remainder(candidate.updated_at);
    if (candidateRemainder !== currentRemainder) return candidateRemainder < currentRemainder;
  }
  const confirmed = (item: WorkshopRenderReceipt) => item.submission_state
    ? item.submission_state === 'confirmed' : Boolean(item.task_id);
  if (confirmed(current) !== confirmed(candidate)) return confirmed(current);
  const rank = (item: WorkshopRenderReceipt) => item.artifact_id ? 3
    : ['succeeded', 'failed', 'cancelled'].includes(item.status) ? 2 : item.status === 'running' ? 1 : 0;
  return rank(candidate) < rank(current)
    || (rank(candidate) === rank(current) && candidate.progress < current.progress);
}

/** One UI owner per project; the server's durable receipts survive navigation. */
const renderReceiptId = /^[a-f0-9]{32}$/;
const renderTaskId = /^[A-Za-z0-9:_-]{1,160}$/;
const renderStates = new Set(['queued', 'running', 'succeeded', 'failed', 'cancelled']);

function chatWorkshopReceipt(value: unknown): value is WorkshopRenderReceipt {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return typeof item.id === 'string' && renderReceiptId.test(item.id)
    && typeof item.operation_id === 'string' && item.operation_id.length > 0 && item.operation_id.length <= 256
    && typeof item.task_id === 'string' && (item.task_id === '' || renderTaskId.test(item.task_id))
    && Number.isSafeInteger(item.content_revision)
    && typeof item.status === 'string' && renderStates.has(item.status)
    && (item.submission_state === undefined || item.submission_state === 'confirmed' || item.submission_state === 'unconfirmed')
    && typeof item.progress === 'number' && item.progress >= 0 && item.progress <= 1
    && typeof item.created_at === 'string' && Number.isFinite(Date.parse(item.created_at))
    && typeof item.updated_at === 'string' && Number.isFinite(Date.parse(item.updated_at))
    && (item.artifact_id === undefined || typeof item.artifact_id === 'string' && renderReceiptId.test(item.artifact_id));
}

/** Bind one observation to the current revision of a scoped workshop card. */
export function selectChatWorkshopRender(project: Record<string, unknown>, id: string, revision: number | null): WorkshopRenderReceipt | null {
  if (!renderReceiptId.test(id) || project.id !== id || project.revision !== revision || project.archived_at
      || !Array.isArray(project.renders)) return null;
  const candidate = project.renders.findLast(item => item?.content_revision === revision);
  return chatWorkshopReceipt(candidate) ? candidate : null;
}

export function chatWorkshopRenderState(receipt: WorkshopRenderReceipt) {
  if (receipt.submission_state === 'unconfirmed' || !receipt.task_id) {
    return { code: 'unconfirmed', label: '渲染提交结果待核对', active: false };
  }
  switch (receipt.status) {
    case 'queued': return { code: 'queued', label: '渲染已排队', active: true };
    case 'running': return { code: 'running', label: '渲染进行中', active: true };
    case 'succeeded': return receipt.artifact_id
      ? { code: 'collected', label: '已记录成片收取 · 审核见工坊记录', active: false }
      : { code: 'uncollected', label: '渲染成功，尚未收取成片', active: false };
    case 'failed': return { code: 'failed', label: '渲染失败', active: false };
    case 'cancelled': return { code: 'cancelled', label: '渲染已取消', active: false };
  }
}

export function canRetryChatWorkshopObservation(error: unknown): boolean {
  return error instanceof TypeError || error instanceof WorkshopRequestError
    && (error.status === 408 || error.status === 429 || error.status >= 500);
}

export async function observeChatWorkshopRender(projectId: string, receipt: WorkshopRenderReceipt, signal: AbortSignal): Promise<WorkshopRenderReceipt> {
  if (!renderReceiptId.test(projectId) || !chatWorkshopReceipt(receipt) || !chatWorkshopRenderState(receipt).active) {
    throw new Error('没有可观察的渲染回执');
  }
  const next = await request<unknown>(renderPath(projectId, receipt.id), { signal });
  if (!chatWorkshopReceipt(next) || next.id !== receipt.id || next.operation_id !== receipt.operation_id
      || next.task_id !== receipt.task_id || next.content_revision !== receipt.content_revision) {
    throw new Error('渲染回执身份不一致');
  }
  return retainRenderReceipt(receipt, next) ? receipt : next;
}

export class WorkshopRenderFlow {
  private readonly projectId: string;
  private current: WorkshopRenderReceipt | null = null;
  private starting: Promise<WorkshopRenderReceipt> | null = null;
  private polling: Promise<WorkshopRenderReceipt> | null = null;
  private collecting: Promise<WorkshopProject> | null = null;
  private collectedProject: WorkshopProject | null = null;

  constructor(projectId: string, receipts: WorkshopRenderReceipt[] = []) {
    this.projectId = projectId;
    this.restore(receipts);
  }

  get receipt() { return this.current; }

  restore(receipts: WorkshopRenderReceipt[]): WorkshopRenderReceipt | null {
    const candidate = receipts.at(-1);
    if (!candidate || this.starting || this.collecting) return this.current;
    if (this.current) {
      if (candidate.id !== this.current.id) {
        if (!receipts.some(item => item.id === this.current?.id)) return this.current;
      } else if (retainRenderReceipt(this.current, candidate)) return this.current;
    }
    this.current = candidate;
    return this.current;
  }

  async start(project: WorkshopProject): Promise<WorkshopRenderReceipt> {
    if (this.starting) return this.starting;
    if (this.collecting) throw new Error('正在登记交付，请等待完成后再导出。');
    const existing = this.current;
    if (existing && (existing.content_revision === project.revision
      || existing.collected_revision === project.revision)
      && !['failed', 'cancelled'].includes(existing.status)) return existing;
    const pending = request<WorkshopRenderReceipt>(renderPath(this.projectId), {
      method: 'POST', body: JSON.stringify({ expected_revision: project.revision }),
    }).then(receipt => { this.current = receipt; this.collectedProject = null; return receipt; });
    this.starting = pending;
    try { return await pending; }
    finally { if (this.starting === pending) this.starting = null; }
  }

  async poll(): Promise<WorkshopRenderReceipt> {
    const receipt = this.current;
    if (!receipt) throw new Error('尚无导出任务，请先导出当前成片。');
    if (receipt.artifact_id || ['succeeded', 'failed', 'cancelled'].includes(receipt.status)) return receipt;
    if (this.polling) return this.polling;
    const pending = request<WorkshopRenderReceipt>(renderPath(this.projectId, receipt.id)).then(next => {
      if (this.current?.id === receipt.id && !retainRenderReceipt(this.current, next)) this.current = next;
      return this.current || next;
    });
    this.polling = pending;
    try { return await pending; }
    finally { if (this.polling === pending) this.polling = null; }
  }

  async collect(project: WorkshopProject): Promise<WorkshopProject> {
    const receipt = this.current;
    if (!receipt || receipt.status !== 'succeeded') throw new Error('导出任务尚未成功，不能登记成片。');
    if (receipt.artifact_id) return this.collectedProject || project;
    if (this.collecting) return this.collecting;
    if (receipt.content_revision !== project.revision) {
      throw new Error('内容已修改，请重新合成当前成片。');
    }
    const pending = request<WorkshopProject>(`${renderPath(this.projectId, receipt.id)}/collect`, {
      method: 'POST', body: JSON.stringify({ expected_revision: receipt.content_revision }),
    }).then(next => {
      const saved = next.renders?.find(item => item.id === receipt.id);
      if (!saved?.artifact_id) throw new Error('交付登记缺少成片回执，请刷新项目并重新检查任务。');
      this.current = saved;
      this.collectedProject = next;
      return next;
    });
    this.collecting = pending;
    try { return await pending; }
    finally { if (this.collecting === pending) this.collecting = null; }
  }
}

export function workshopMediaUrl(path: string): string | null {
  if (!path || path.startsWith('/') || path.includes('\\') || path.includes(':')
    || [...path].some(character => character.charCodeAt(0) < 32)) return null;
  const parts = path.split('/');
  if (parts.some(part => !part || part === '.' || part === '..')) return null;
  return projectUrl('/api/media/' + parts.map(encodeURIComponent).join('/'));
}

export function deliveryReviewState(project: WorkshopProject, artifact?: WorkshopDeliverable) {
  if (!artifact) return { canReview: false, label: '尚无交付', message: '' };
  if (artifact.revision !== project.revision) return { canReview: false, label: '历史交付',
    message: `这是历史交付。当前工程已更新至 V${project.revision}，请导出当前版本后提交审阅。` };
  return { canReview: true, label: '待审阅', message: '' };
}

export function reviewSummary(project: WorkshopProject, artifact?: WorkshopDeliverable) {
  const state = deliveryReviewState(project, artifact);
  const none = { humanApproved: false, kind: null, decision: null, label: state.label };
  if (!artifact || !state.canReview) return none;
  const latest = [...(project.reviews || [])].reverse().find(review =>
    review.revision === project.revision && review.artifact_sha256 === artifact.sha256);
  if (!latest) return none;
  const simulated = latest.kind === 'simulated';
  const approved = latest.decision === 'approved';
  return {
    humanApproved: !simulated && approved,
    kind: latest.kind,
    decision: latest.decision,
    label: simulated ? (approved ? '模拟审阅通过' : '模拟审阅待修改') : (approved ? '人工审阅通过' : '人工审阅待修改'),
  };
}

export function saveWorkshopProduction(project: WorkshopProject, input: { script: string; assets: Partial<WorkshopAsset>[]; shots: Partial<WorkshopShot>[] }): Promise<WorkshopProject> {
  return request(`/projects/${project.id}/production`, { method: 'PUT', body: JSON.stringify({ expected_revision: project.revision, ...input }) });
}
export function selectWorkshopCandidate(project: WorkshopProject, shotId: string, candidateId: string): Promise<WorkshopProject> {
  return request(`/projects/${project.id}/shots/${encodeURIComponent(shotId)}/select`, { method: 'POST', body: JSON.stringify({ expected_revision: project.revision, candidate_id: candidateId }) });
}
export function prepareWorkshopPublication(project: WorkshopProject, artifactId: string, input: { platform: string; title: string; body: string; tags: string; aiGenerated: boolean }): Promise<{ project: WorkshopProject }> {
  return request(`/projects/${project.id}/publish/prepare`, { method: 'POST', body: JSON.stringify({ expected_revision: project.revision, artifact_id: artifactId, ...input }) });
}
export async function uploadWorkshopAsset(project: WorkshopProject, file: File, asset: { id: string; kind: WorkshopAsset['kind']; name: string }): Promise<WorkshopProject> {
  const form = new FormData();
  form.append('file', file); form.append('expected_revision', String(project.revision));
  form.append('asset_id', asset.id); form.append('kind', asset.kind); form.append('name', asset.name);
  const response = await projectFetch(`/api/workshop/projects/${project.id}/assets/upload`, { method: 'POST', body: form });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '素材上传未完成');
  return result;
}

export async function openHuobaoStudio(project: WorkshopProject, signal?: AbortSignal): Promise<{ url: string; engine: string; upstream_commit: string }> {
  const theme = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  return request(`/projects/${project.id}/engine/open`, { method: 'POST', signal,
    body: JSON.stringify({ expected_revision: project.revision, theme }) });
}
