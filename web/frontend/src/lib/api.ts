import { projectUrl, projectFetch } from './projectScope';
import type { AgentEvent } from './agentEvents';
import type { ChatMessage, ChatSession, ChatHistoryWindow, ReasoningSummary, AnswerCompletion, ChatFailure, ChatPlanRef, ChatExecutionState } from './store';
import type { ChatReference, ChatSessionReference } from './chatReferences';
import type { ChatCommand, ChatCommandOption } from './chatCommands';
import { mergeSessionCatalog } from './sessionSync';
function getBasePath(): string {
  const path = window.location.pathname;
  const cleaned = path.replace(/\/index\.html$/, '').replace(/\/$/, '');
  return cleaned;
}

const BASE = getBasePath();

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  // 全部是动态应用接口（登录态/数据等），禁止浏览器 HTTP 缓存——否则 /api/accounts 等可能被启发式
  // 缓存住旧响应（曾表现为“扫码登录后卡片仍显示未登录”）。调用方可用 options.cache 覆盖。
  const res = await projectFetch(projectUrl(`${BASE}${url}`), { cache: 'no-store', ...options });
  if (!res.ok) {
    // 优先显示后端返回的实质错误信息（FastAPI 的 {detail}），而不是无意义的 "API error: 400"
    let detail = '';
    try {
      const j = await res.json();
      detail = (j && (j.detail || j.message)) || '';
    } catch {
      /* 响应体不是 JSON，忽略 */
    }
    throw Object.assign(new Error(detail || `请求失败（${res.status} ${res.statusText}）`), { status: res.status });
  }
  return res.json() as Promise<T>;
}

export interface StatusResponse {
  gateway: boolean;
  transport?: string;
  runtime?: string;
  skills: SkillItem[];
  personas: PersonaItem[];
}

export interface HarnessSettingsResponse {
  runtime: string;
  models: Record<string, string>;
  sandbox: string;
  codex_auto_approve: boolean;
  tool_approval_mode?: 'manual' | 'ordinary' | 'all';
  locked: boolean;
  agents: { id: string; label: string; protocol: string; installed: boolean; install_hint: string }[];
}

export function fetchHarnessSettings(): Promise<HarnessSettingsResponse> {
  return request('/api/settings/harness');
}

export interface HarnessModel {
  id: string;
  label: string;
  default: boolean;
}

export function fetchHarnessModels(runtime: string): Promise<{ runtime: string; models: HarnessModel[] }> {
  return request(`/api/settings/harness/models/${encodeURIComponent(runtime)}`, { method: 'POST' });
}

export function saveHarnessSettings(runtime: string, model: string, sandbox: string, codexAutoApprove?: boolean): Promise<HarnessSettingsResponse> {
  return request('/api/settings/harness', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ runtime, model, sandbox, codex_auto_approve: codexAutoApprove }),
  });
}

export interface PersonaItem {
  name: string;
  description: string;
}

export interface PersonaDetail {
  name: string;
  content: string;
}

export interface SkillItem {
  name: string;
  description: string;
  layer: string;
  needsApi: boolean;
  apiConfigured: boolean;
}

export interface ApiKeySpec {
  env: string;
  label: string;
  required: boolean;
  secret: boolean;
  configured: boolean;
  masked: string;   // 脱敏值或非 secret 明文
  choices: string[];
}

export interface ApiProviderSpec {
  id: string;
  name: string;
  keys: ApiKeySpec[];
}

export interface ApiSpec {
  label: string;
  settings: ApiKeySpec[];
  providers: ApiProviderSpec[];
}

export interface SkillDetail {
  name: string;
  layer: string;
  description: string;
  body: string;
  needsApi: boolean;
  apiConfigured: boolean;
  apiSpec: ApiSpec | null;
}

export type FileKind = 'text' | 'image' | 'video' | 'audio' | 'binary';

/** 顶层项目目录的 .easel.json 展示头（供内容库富展示）。 */
export interface OutputMeta {
  title?: string;
  summary?: string;
  platform?: string;
  kind?: string;             // article|xhs-note|video|cards|poster|audio|other
  status?: string;           // draft|ready|published
  tags?: string[];
  cover?: string;            // outputs 下相对路径（后端已解析存在性）
  deliverables?: string[];   // 成品文件名（项目根相对）
  deliverablePaths?: string[];  // 成品的 outputs 相对路径（后端已解析存在性）
}

/** 产物树节点：文件或目录（目录带 children，可无限嵌套点开）。 */
export interface OutputNode {
  name: string;
  type: 'dir' | 'file';
  path: string;              // outputs 下的相对路径，如 "short-drama/监控诡影/episodes/ep01"
  mtime?: number;
  kind?: FileKind;           // 仅 file
  size?: number;             // 仅 file
  children?: OutputNode[];   // 仅 dir
  fileCount?: number;        // 仅 dir：递归文件数
  meta?: OutputMeta;         // 仅顶层项目 dir
}

/** @deprecated 用 OutputNode（type==='file'）。保留别名减少改动面。 */
export type OutputFile = OutputNode;

export interface OutputContent {
  path: string;
  content: string;
  kind: FileKind;
  isBinary: boolean;
}

export interface SkillResponse {
  response: string;
}

export interface ProfileBuildResponse {
  created: boolean;
  name: string;
  async?: boolean;
  status?: string;
  log?: string;
}

export interface ProfileBuildStatus {
  state: 'running' | 'done' | 'failed' | 'unknown';
  log: string;
}

export function fetchStatus(): Promise<StatusResponse> {
  return request<StatusResponse>('/api/status');
}

export function fetchPersonas(): Promise<PersonaItem[]> {
  return request<PersonaItem[]>('/api/personas');
}

export function fetchPersonaDetail(name: string): Promise<PersonaDetail> {
  return request<PersonaDetail>(`/api/persona/${encodeURIComponent(name)}`);
}

export interface PersonaFile {
  filename: string;
  content: string;
}

export function fetchPersonaFiles(name: string): Promise<{ name: string; files: PersonaFile[] }> {
  return request(`/api/persona/${encodeURIComponent(name)}/files`);
}

export function savePersonaFile(name: string, filename: string, content: string): Promise<{ ok: boolean }> {
  return request(`/api/persona/${encodeURIComponent(name)}/file`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, content }),
  });
}

export function deletePersona(name: string): Promise<{ ok: boolean; deleted: string }> {
  return request(`/api/persona/${encodeURIComponent(name)}`, { method: 'DELETE' });
}

// ---- 内容排期 ----
export interface ScheduleItem {
  id: string; title: string; date: string; platform: string;
  time: string; status: string; note: string;
  kind?: string;          // content（内容/发布）| event（平台活动/节日/特殊日期）
  url?: string;           // 已发布链接
  source?: string;        // manual | publish-page | chat | scheduler
  event_type?: string;    // event 专属：节日/电商/平台活动/行业
  end_date?: string;      // event 专属：活动区间结束日
}
export type ScheduleInput = Omit<ScheduleItem, 'id'>;
export interface ScheduleContext {
  today?: string; window_days?: number; published_recent?: number;
  per_platform?: { platform: string; recent_count: number; last_date: string | null; days_since_last: number | null }[];
  upcoming_schedules?: ScheduleItem[];
  upcoming_events?: ScheduleItem[];
  suggestions?: string[];
}
export function fetchSchedule(): Promise<ScheduleItem[]> {
  return request('/api/schedule');
}
export function fetchScheduleContext(days = 14): Promise<ScheduleContext> {
  return request(`/api/schedule/context?days=${days}`);
}
export function createSchedule(item: ScheduleInput): Promise<ScheduleItem> {
  return request('/api/schedule', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item),
  });
}
export function updateSchedule(id: string, item: ScheduleInput): Promise<ScheduleItem> {
  return request(`/api/schedule/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(item),
  });
}
export function deleteSchedule(id: string): Promise<{ ok: boolean }> {
  return request(`/api/schedule/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function fetchSkills(): Promise<SkillItem[]> {
  return request<SkillItem[]>('/api/skills');
}

export function fetchSkillDetail(name: string): Promise<SkillDetail> {
  return request<SkillDetail>(`/api/skill/${encodeURIComponent(name)}`);
}

/** 保存 API key 到项目根 .env（仅注册表内变量）。返回各需 API 的 skill 是否已就绪。 */
export function saveEnv(updates: Record<string, string>): Promise<{ ok: boolean; skills: Record<string, boolean> }> {
  return request('/api/env', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ updates }),
  });
}

export function executeSkill(skill: string, input: string, persona?: string): Promise<SkillResponse> {
  return request<SkillResponse>('/api/skill', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ skill, input, persona: persona || undefined }),
  });
}

/** 一次性 agent 任务（返回整段 markdown）——用于一稿多改 / 爆款拆解 / 发布预检等工具型调用。 */
export function runAgent(message: string, persona?: string): Promise<{ response: string }> {
  return request('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, persona: persona || undefined }),
  });
}

/** 从表单构建画像（首次引导）。后端写基线 + agent 分析社媒链接增强。 */
export function buildProfile(name: string, form: Record<string, unknown>): Promise<ProfileBuildResponse> {
  return request<ProfileBuildResponse>('/api/profile/build', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, form }),
  });
}

/** 轮询画像 AI 增强进度（构建异步化后用）。 */
export function profileBuildStatus(name: string): Promise<ProfileBuildStatus> {
  return request<ProfileBuildStatus>(`/api/profile/build/status/${encodeURIComponent(name)}`);
}

export function fetchOutputs(): Promise<OutputNode[]> {
  return request<OutputNode[]>('/api/outputs');
}

export function fetchArtifactArchive(path: string, signal?: AbortSignal): Promise<{ paths: string[] }> {
  return request<{ paths: string[] }>(`/api/artifacts/archive?path=${encodeURIComponent(path)}`, { signal });
}

export function fetchOutputContent(path: string): Promise<OutputContent> {
  return request<OutputContent>(`/api/output/${path.split('/').map(encodeURIComponent).join('/')}`);
}

/** 媒体文件（图片/视频/音频/HTML/PDF）的原样 URL，用于 <img>/<video>/iframe/下载。 */
export function mediaUrl(path: string): string {
  return projectUrl(`${BASE}/api/media/${path.split('/').map(encodeURIComponent).join('/')}`);
}

/** 删除内容库里的文件或整个项目目录（系统数据受保护，后端会拒）。 */
export function deleteOutput(path: string): Promise<{ ok: boolean; deleted: string }> {
  return request(`/api/output/${path.split('/').map(encodeURIComponent).join('/')}`, { method: 'DELETE' });
}

export interface UploadedFile { id: string; name: string; path: string; size?: number; mime?: string; }

export interface ChatAttachmentCandidate { id: string; name: string; mime: string; size: number; context?: string; }

export function searchChatAttachments(sessionId: string, query: string, signal?: AbortSignal): Promise<{
  files: UploadedFile[]; candidates: ChatAttachmentCandidate[];
}> {
  const params = new URLSearchParams({ sessionId, query });
  return request(`/api/chat/attachment-search?${params}`, { signal });
}

export function selectChatAttachment(sessionId: string, candidateId: string, signal?: AbortSignal): Promise<{ file: UploadedFile }> {
  return request('/api/chat/attachment-select', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId, candidateId }), signal,
  });
}


/** OpenClaw ask_user 问答题（SSE question 事件 payload）。 */
export interface ChatQuestionOption { label: string; description?: string; }
export interface ChatQuestionItem {
  questionId: string;
  header?: string;
  question: string;
  options?: ChatQuestionOption[];
  multiSelect?: boolean;
  defaultAnswer?: string[];
}
export interface ChatQuestion {
  turnId?: string;
  publication?: import('./publicationApproval').PublicationApprovalRecord;
  id: string;              // gateway question record id (ask_...)
  questions: ChatQuestionItem[];
  expiresAtMs?: number;
  status?: string;
  kind?: 'preference' | 'approval';
  requiresHuman?: boolean;
  defaultAnswers?: Record<string, string[]>;
  autoResolveAtMs?: number | null;
  answers?: Record<string, string[]>;
  resolvedBy?: string;
}
export interface ChatQuestionClosed {
  id: string; status: string; answers?: Record<string, string[]>; resolvedBy?: string;
}
const questionTerminal = new Set([
  'resolved', 'answered', 'expired', 'revoked', 'replaced', 'cancelled',
  'declined', 'blocked', 'denied', 'not_found',
]);

function readQuestionReceipt(value: unknown, id: string): ChatQuestionClosed | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const { id: reportedId, status, answers, resolvedBy } = value as Partial<ChatQuestionClosed>;
  if (reportedId !== id || typeof status !== 'string' || !questionTerminal.has(status)) return undefined;
  const validAnswers = answers && typeof answers === 'object' && !Array.isArray(answers)
    && Object.values(answers).every(items => Array.isArray(items) && items.every(item => typeof item === 'string'));
  return { id, status, ...(validAnswers ? { answers } : {}),
    ...(typeof resolvedBy === 'string' ? { resolvedBy } : {}) };
}

export async function answerQuestion(
  payload: { questionId: string; sessionId?: string; turnId?: string; answers: Record<string, string[]>; resolvedBy?: string },
): Promise<{ ok: boolean; error?: string; status?: string; expired?: boolean; receipt?: ChatQuestionClosed }> {
  const res = await projectFetch(projectUrl(`${BASE}/api/chat/question/answer`), {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (res.status === 401 || res.status === 403) return { ok: false, status: 'revoked' };
  const data = await res.json().catch(() => ({})) as { ok?: boolean; status?: string; expired?: boolean; receipt?: unknown };
  const receipt = readQuestionReceipt(data.receipt, payload.questionId);
  const status = receipt?.status ?? (typeof data.status === 'string' &&
    (questionTerminal.has(data.status) || data.status === 'pending' || data.status === 'unknown') ? data.status : 'pending');
  return { ok: res.ok && data.ok === true && !!receipt && ['resolved', 'answered'].includes(receipt.status),
    status, expired: data.expired === true, receipt };
}

/** 批量查 question 状态：过滤重放中已解决/已过期的旧题。 */
export async function questionStatus(
  questionIds: string[], sessionId?: string,
): Promise<Record<string, { status: string; answers?: Record<string, string[]>; resolvedBy?: string }>> {
  if (!questionIds.length) return {};
  try {
    const res = await projectFetch(projectUrl(`${BASE}/api/chat/question/status`), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questionIds, sessionId }),
    });
    if (res.status === 401 || res.status === 403) {
      return Object.fromEntries(questionIds.map(id => [id, { status: 'revoked' }]));
    }
    if (!res.ok) return {};
    const data = await res.json().catch(() => ({})) as { ok?: boolean; questions?: Record<string, { status: string; answers?: Record<string, string[]>; resolvedBy?: string }> };
    return data.questions || {};
  } catch {
    return {};
  }
}
/** 上传素材到当前会话的隔离 inbox，返回后端可校验的附件引用。 */
export async function uploadFiles(files: File[], sessionId: string, signal?: AbortSignal): Promise<UploadedFile[]> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  fd.append('sessionId', sessionId);
  const r = await request<{ ok: boolean; files: UploadedFile[] }>('/api/upload', { method: 'POST', body: fd, signal });
  return r.files;
}

/** 超限文件复制通道：返回与 uploadFiles 同构的附件引用。 */
export interface AdoptedFile { id: string; name: string; path: string; }
export async function adoptOversize(files: File[], sessionId: string, signal?: AbortSignal): Promise<AdoptedFile[]> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  fd.append('sessionId', sessionId);
  const r = await request<{ ok: boolean; files: AdoptedFile[] }>('/api/upload/local', { method: 'POST', body: fd, signal });
  return r.files;
}

export function deleteSession(sessionKey: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/api/session/${encodeURIComponent(sessionKey)}`, {
    method: 'DELETE',
  });
}

// ---- 账号登录 ----
export interface AccountItem {
  platform: string;
  name: string;
  backend: string;      // xhs | web | biliup | unsupported
  supported: boolean;
  loggedIn: boolean;
  note: string;
  loginMode?: 'qr' | 'browser';
}

export type AccountCapabilityState = 'verified' | 'needs_verification' | 'unavailable';
export interface AccountDirectoryItem {
  key: string;
  platform: string;
  platformName: string;
  name: string;
  avatar: string;
  loggedIn: boolean;
  status: 'logged_in' | 'needs_login' | 'unknown';
  nativeAccountId: number | null;
  current: boolean;
  identityVerified: boolean;
  capabilities: Record<string, AccountCapabilityState>;
}
export interface AccountDirectory {
  project_id: string;
  platforms: AccountItem[];
  accounts: AccountDirectoryItem[];
}

export function fetchAccountDirectory(signal?: AbortSignal): Promise<AccountDirectory> {
  return request<AccountDirectory>('/api/accounts/directory', { signal });
}

export interface RemoteLoginDescriptor {
  id: string;
  platform: string;
}

export interface LoginStart {
  remote?: RemoteLoginDescriptor;
  /** Browser-free login attempt; used for precise cancellation, never for frame polling. */
  attempt?: RemoteLoginDescriptor;
  mode: 'qr' | 'browser' | 'terminal' | 'credentials';
  state?: string;       // starting | qr_ready | success | expired | error | unknown
  message?: string;
  qr?: string;          // outputs 下相对路径，用 mediaUrl() 取图
  qrTs?: number;
  configured?: boolean; // 凭证式：是否已配置
}

// ---- 凭证式账号（微信公众号 AppID/AppSecret）----
export interface CredentialStatus {
  configured: boolean;
  appIdMasked: string;
  name: string;
  author: string;
}

/** 读取凭证式平台（公众号）已配置状态（AppSecret 不回传）。 */
export function getCredentials(platform: string): Promise<CredentialStatus> {
  return request<CredentialStatus>(`/api/accounts/${encodeURIComponent(platform)}/credentials`);
}

/** 保存公众号 AppID/AppSecret（后端会调官方接口验证连通性）。 */
export function saveCredentials(
  platform: string,
  payload: { appId: string; appSecret: string; name?: string; author?: string },
): Promise<{ ok: boolean; message: string }> {
  return request(`/api/accounts/${encodeURIComponent(platform)}/credentials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

/** 启动「公众号后台」扫码登录（数据中心取数用，独立于 AppID 凭证）。返回二维码状态。 */
export function startMpLogin(platform: string): Promise<LoginStatus> {
  return request<LoginStatus>(`/api/accounts/${encodeURIComponent(platform)}/mp-login`, { method: 'POST' });
}

/** 轮询公众号后台扫码登录状态。 */
export function mpLoginStatus(platform: string): Promise<LoginStatus> {
  return request<LoginStatus>(`/api/accounts/${encodeURIComponent(platform)}/mp-login/status`,
    { signal: AbortSignal.timeout(8000) });
}

export interface LoginStatus {
  remote?: RemoteLoginDescriptor;
  /** Browser-free login attempt; used for precise cancellation, never for frame polling. */
  attempt?: RemoteLoginDescriptor;
  mode: 'qr' | 'browser';
  state: string;
  message: string;
  qr: string;
  qrTs?: number;
}

export function fetchAccounts(): Promise<AccountItem[]> {
  return request<AccountItem[]>('/api/accounts');
}

export interface AccountWhoami {
  loggedIn: boolean;
  name: string;
  avatar: string;   // 头像 URL（http）或空
  accountId?: string;
  accountIdKind?: string;
  accountIdentityStatus?: 'verified' | 'unknown';
}

/** 真校验某平台登录态 + 拉昵称/头像（后端起 headless 浏览器，数秒）。 */
export function accountWhoami(platform: string, force = false): Promise<AccountWhoami> {
  return request<AccountWhoami>(`/api/accounts/${encodeURIComponent(platform)}/whoami${force ? '?force=true' : ''}`);
}

/** 退出登录：删该平台持久化登录态。 */
export function logoutAccount(platform: string): Promise<{ ok: boolean; deleted: string[] }> {
  return request(`/api/logout/${encodeURIComponent(platform)}`, { method: 'POST' });
}

export interface PublishResult {
  ok?: boolean;
  message: string;
  detail?: string;
  async?: boolean;   // true = 异步发布（抖音，可能触发短信验证），需轮询 publishStatus
  pending?: boolean;
}

export interface PublishingPolicy {
  mode: 'manual' | 'automated';
  automaticSubmission: boolean;
}

export function fetchPublishingPolicy(): Promise<PublishingPolicy> {
  return request('/api/publishing-policy');
}

/** Create a local export only; no social platform is contacted. */
export function exportManualPackage(platform: string, payload: {
  title: string; body: string; tags: string; media: string[]; reviewed: boolean;
}): Promise<{ path: string; filename: string; published: false }> {
  return request(`/api/manual-publish/${encodeURIComponent(platform)}/export`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
}

/** Host preparation freezes the exact account/content/media subject; it does not dispatch. */
export interface PublicationInput {
  title: string; body: string; media: string[]; tags?: string; persona?: string;
  draft?: boolean; aiGenerated?: boolean; artifactIds?: string[]; approvalId?: string; payloadHash?: string;
}

export function preparePublication(platform: string, payload: PublicationInput): Promise<import('./publicationApproval').PublicationApprovalRecord> {
  return request(`/api/publish/${encodeURIComponent(platform)}/prepare`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
}

export function fetchPublicationApproval(id: string): Promise<import('./publicationApproval').PublicationApprovalRecord & {
  runnerStatus?: { state: string; message: string };
}> { return request(`/api/publication-approvals/${encodeURIComponent(id)}`); }

/** Explicitly request a same-operation platform receipt readback; never resubmit publication. */
export function refreshPublicationApproval(id: string): Promise<import('./publicationApproval').PublicationApprovalRecord> {
  return request(`/api/publication-approvals/${encodeURIComponent(id)}/refresh`, { method: 'POST' });
}

/** Read the current actor/IP-owned immutable declaration; never infer identity from a path. */
export function fetchPublicationArtifact(id: string): Promise<import('./agentEvents').AgentArtifact> {
  return request(`/api/chat/artifacts/${encodeURIComponent(id)}`);
}

export function decidePublication(id: string, approved: boolean, payloadHash: string): Promise<import('./publicationApproval').PublicationApprovalRecord> {
  return request(`/api/publication-approvals/${encodeURIComponent(id)}/decision`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approved, payloadHash }),
  });
}

export function publishNow(
  platform: string,
  payload: PublicationInput,
): Promise<import('./publicationApproval').PublicationApprovalRecord> {
  return request(`/api/publish/${encodeURIComponent(platform)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export type PublishStatus = import('./publicationApproval').PublicationApprovalRecord & { mode: 'publish' };

/** 轮询异步发布状态（抖音）。 */
export function publishStatus(platform: string, approvalId: string): Promise<PublishStatus> {
  return request<PublishStatus>(`/api/publish/${encodeURIComponent(platform)}/status?approvalId=${encodeURIComponent(approvalId)}`);
}

/** 发布触发短信墙时回填验证码。 */
export function submitPublishSms(platform: string, code: string, approvalId: string): Promise<{ ok: boolean }> {
  return request(`/api/publish/${encodeURIComponent(platform)}/sms?approvalId=${encodeURIComponent(approvalId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
}

export function startLogin(platform: string): Promise<LoginStart> {
  return request<LoginStart>(`/api/login/${encodeURIComponent(platform)}?restart=true`, { method: 'POST' });
}

export function loginStatus(platform: string): Promise<LoginStatus> {
  return request<LoginStatus>(`/api/login/${encodeURIComponent(platform)}/status`,
    { signal: AbortSignal.timeout(8000) });
}

// ---- 归因层：账号创作数据 ----
export interface AnalyticsPlatform {
  platform: string;
  name: string;
  loggedIn: boolean;
}

export interface AccountAnalytics {
  platform: string;
  name: string;
  nickname: string;
  loggedIn: boolean;
  followers: number | null;
  likes: number | null;
  following: number | null;
  posts: number | null;
  metrics: { label: string; value: string | number; vs: string }[];
  notes: { title: string; url: string; cover?: string; stat?: string }[];
  growth: Record<'last' | 'day' | 'week' | 'month' | 'year',
    { followers: number | null; likes: number | null; posts: number | null; since_days: number | null } | null>;
  fetched_at: number;
  warning?: string;
}

/** 支持抓数据的平台 + 各自登录态。 */
export function fetchAnalyticsPlatforms(): Promise<AnalyticsPlatform[]> {
  return request<AnalyticsPlatform[]>('/api/analytics/platforms');
}

/** 抓某平台已登录账号的创作数据，客户端等待时限略长于后端。 */
export function fetchAccountAnalytics(platform: string): Promise<AccountAnalytics> {
  return request<AccountAnalytics>(`/api/analytics/${encodeURIComponent(platform)}`,
    { signal: AbortSignal.timeout(platform === 'wechat-oa' ? 195000 : 105000) });
}

/** 回填短信验证码（登录风控短信墙）：提交后 runner 读走填码继续登录。 */
export function submitLoginSms(platform: string, code: string): Promise<{ ok: boolean }> {
  return request(`/api/login/${encodeURIComponent(platform)}/sms`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code }),
  });
}

/** 用户显式停止：终止后端正在跑的对话 agent，释放会话锁，令下一句能立刻发。 */
export function stopChat(sessionId: string, expectedTurnId: string): Promise<{ stopped: boolean; status?: string }> {
  return request('/api/chat/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId, expectedTurnId }),
  });
}

export type ChatTurnOutcome = 'completed' | 'failed' | 'stopped' | 'unconfirmed';

export function steerChat(sessionId: string, turnId: string, requestId: string, message: string): Promise<{ status: 'accepted' | 'unconfirmed' }> {
  return request('/api/chat/steer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId, turnId, requestId, message }),
  });
}

function publicReasoningSummary(value: unknown): ReasoningSummary | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const { text, truncated } = value as Partial<ReasoningSummary>;
  if (typeof text !== 'string' || !text.trim() || typeof truncated !== 'boolean') return undefined;
  return { text, truncated };
}

function parseAnswerCompletion(value: unknown): AnswerCompletion | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const { itemId } = value as Partial<AnswerCompletion>;
  if (typeof itemId !== 'string' || !itemId.trim() || itemId.length > 512 || /\p{Cc}/u.test(itemId)) return undefined;
  return { itemId };
}

const chatFailureCodes = new Set<ChatFailure['code']>([
  'authentication', 'model_unavailable', 'rate_limit', 'quota_exhausted', 'network',
  'timeout', 'permission_denied', 'session_conflict', 'storage', 'unknown',
  'server_queue_full', 'server_owner_queue_full', 'server_queue_timeout', 'server_unavailable',
]);

function parseChatFailure(value: unknown): ChatFailure {
  const fallback: ChatFailure = { code: 'unknown', retryable: false, phase: 'execution' };
  if (!value || typeof value !== 'object') return fallback;
  const { code, phase, retryable } = value as Partial<ChatFailure>;
  if (!code || !chatFailureCodes.has(code) || (phase !== 'startup' && phase !== 'execution' && phase !== 'finalization')) return fallback;
  return { code, phase, retryable: phase === 'startup' && retryable === true
    && ['network', 'timeout', 'rate_limit', 'server_queue_full', 'server_owner_queue_full', 'server_queue_timeout'].includes(code) };
}

function parseChatExecutionState(value: unknown): ChatExecutionState | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const { executionState, queuePosition } = value as Partial<ChatExecutionState>;
  if (executionState !== 'queued' && executionState !== 'running') return undefined;
  return { executionState, ...(executionState === 'queued' && Number.isInteger(queuePosition)
    && queuePosition! >= 1 && queuePosition! <= 256 ? { queuePosition } : {}) };
}

function chatFailureError(failure: ChatFailure, message = '执行失败', unconfirmed = false) {
  return Object.assign(new Error(message), { failure, unconfirmed });
}

export function streamChat(
  message: string,
  persona: string | undefined,
  sessionId: string,
  onToken: (chunk: string) => void,
  onDone: (sessionKey?: string, outcome?: ChatTurnOutcome) => void,
  onError: (err: Error) => void,
  onReasoningSummary?: (summary: ReasoningSummary) => void,
  onActivity?: (status: string) => void,
  onInterrupted?: () => void,
  turnId?: string,
  resumeOnly = false,
  onRecoveryUnavailable?: () => void,
  attachments: UploadedFile[] = [],
  onQuestion?: (q: ChatQuestion) => void,
  onHeartbeat?: (note: string) => void,
  onQuestionClosed?: (q: ChatQuestionClosed) => void,
  onAgentEvent?: (event: AgentEvent) => void,
  workshopRef?: import('./workshop').WorkshopReference,
  onAdmitted?: (receipt: { turnId: string; sessionId: string }) => void,
  requireAdmission = false,
  references?: ChatReference[],
  sessionReferences?: ChatSessionReference[],
  command?: ChatCommand,
  onAnswerCompleted?: (receipt: AnswerCompletion) => void,
  planRef?: ChatPlanRef,
  modelOverride?: string,
  onExecutionState?: (state: ChatExecutionState) => void,
): AbortController {
  const controller = new AbortController();
  let lastEventId = 0;
  let admitted = false;
  let pendingFailure: ChatFailure | undefined;
  let pendingUnconfirmed = false;
  let admissionTimer: ReturnType<typeof setTimeout> | undefined;
  const clearAdmissionTimer = () => {
    if (admissionTimer !== undefined) clearTimeout(admissionTimer);
    admissionTimer = undefined;
  };

  const consume = async (res: Response, replay: boolean): Promise<boolean> => {
      if (!res.ok) {
        const detail = res.status === 401 ? '平台登录状态已失效，请重新登录。'
          : res.status === 403 ? '当前账号没有发送权限。'
          : modelOverride && res.status === 422 ? '服务端未接收这条消息，请核对所选模型、输入和附件。'
          : '服务端未接收这条消息，请核对输入和附件。';
        throw Object.assign(new Error(detail), { status: res.status });
      }
      const reader = res.body?.getReader();
      if (!reader) throw new Error('No response body');

      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';
      let currentId = 0;
      let dataLines: string[] = [];

      const dispatch = () => {
        if (controller.signal.aborted) return true;
        if (!currentEvent || dataLines.length === 0 || (currentId > 0 && currentId <= lastEventId)) return false;
        const data = dataLines.join('\n');
        if (currentEvent === 'admitted') {
          let receipt: { turnId?: string; sessionId?: string } | undefined;
          try { receipt = JSON.parse(data); } catch { return false; }
          if (!turnId || receipt?.turnId !== turnId || receipt.sessionId !== sessionId
              || (requireAdmission && currentId !== 1)) return false;
          if (currentId > 0) lastEventId = currentId;
          if (!admitted) {
            admitted = true;
            clearAdmissionTimer();
            onAdmitted?.({ turnId, sessionId });
          }
          return false;
        }
        if (requireAdmission && !admitted) {
          if (replay && (currentEvent === 'done' || currentEvent === 'error')) {
            onError(new Error('没有收到任务接收回执，发送状态未确认。'));
            return true;
          }
          return false;
        }
        if (currentId > 0) lastEventId = currentId;
        if (currentEvent === 'token') {
          try { onToken(JSON.parse(data) as string); } catch { onToken(data); }
        } else if (currentEvent === 'reasoning_summary' && onReasoningSummary) {
          try {
            const summary = publicReasoningSummary(JSON.parse(data));
            if (summary) onReasoningSummary(summary);
          } catch { /* Ignore malformed summary snapshots. */ }
        } else if (currentEvent === 'answer_completed' && onAnswerCompleted) {
          try {
            const receipt = parseAnswerCompletion(JSON.parse(data));
            if (receipt) onAnswerCompleted(receipt);
          } catch { /* Ignore malformed answer receipts. */ }
        } else if (currentEvent === 'activity' && onActivity) {
          try { onActivity(JSON.parse(data) as string); } catch { onActivity(data); }
        } else if (currentEvent === 'execution_state' && onExecutionState) {
          try {
            const state = parseChatExecutionState(JSON.parse(data));
            if (state) onExecutionState(state);
          } catch { /* Unknown future states do not imply that a model has started. */ }
        } else if (currentEvent === 'agent_event' && onAgentEvent) {
          try {
            const event = JSON.parse(data) as AgentEvent;
            if (event && typeof event === 'object' && typeof event.id === 'string' && typeof event.kind === 'string') onAgentEvent(event);
          } catch { /* malformed event is not executable UI */ }
        } else if (currentEvent === 'question' && onQuestion) {
          // 防御：question 事件负载必须是对象且带 questions 数组。旧后端重放流曾把
          // question 双层序列化（JSON.parse 后得到字符串），透传会渲染出无法作答的
          // 空壳卡片——宁可不显示，交给用户手动追问。
          try {
            const q = JSON.parse(data) as ChatQuestion;
            if (q && typeof q === 'object' && Array.isArray(q.questions)) onQuestion(q);
          } catch { /* 解析失败忽略 */ }
        } else if (currentEvent === 'question_closed' && onQuestionClosed) {
          try {
            const closed = JSON.parse(data) as ChatQuestionClosed;
            if (closed && typeof closed.id === 'string' && typeof closed.status === 'string') onQuestionClosed(closed);
          } catch {}
        } else if (currentEvent === 'heartbeat') {
          // 防呆心跳：独立于 activity/thinking，仅作「未卡住」提示，不覆盖真实状态。
          if (onHeartbeat) { try { onHeartbeat(JSON.parse(data) as string); } catch { onHeartbeat(data); } }
        } else if (currentEvent === 'error') {
          let payload: unknown;
          try { payload = JSON.parse(data); } catch { /* Legacy text has no safe structured fields. */ }
          pendingFailure = parseChatFailure(payload);
          pendingUnconfirmed = pendingFailure.phase === 'finalization' && !!payload && typeof payload === 'object'
            && (payload as { unconfirmed?: unknown }).unconfirmed === true;
        } else if (currentEvent === 'done') {
          let completion: { sessionKey?: string; outcome?: ChatTurnOutcome } | undefined;
          try { completion = JSON.parse(data); } catch { /* Legacy done event. */ }
          if (pendingFailure) {
            onError(chatFailureError(pendingFailure, undefined, pendingUnconfirmed || completion?.outcome === 'unconfirmed'));
            return true;
          }
          const outcome = completion?.outcome;
          onDone(completion?.sessionKey, outcome === 'completed' || outcome === 'failed' ||
            outcome === 'stopped' || outcome === 'unconfirmed' ? outcome : undefined);
          return true;
        }
        return false;
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line === '') {
            if (dispatch()) return true;
            currentEvent = ''; currentId = 0; dataLines = [];
          } else if (line.startsWith('id:')) {
            const id = Number(line.slice(3).trim());
            currentId = Number.isSafeInteger(id) && id > 0 ? id : 0;
          }
          else if (line.startsWith('event:')) currentEvent = line.slice(6).trim();
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
        }
      }
      if (pendingFailure && (replay || !turnId) && !controller.signal.aborted) {
        onError(chatFailureError(pendingFailure, undefined, pendingUnconfirmed));
        return true;
      }
      // A disconnect can leave a complete-looking data line with no SSE frame
      // terminator. Never dispatch it or move the recovery cursor past it.
      return false;
  };

  if (requireAdmission) {
    admissionTimer = setTimeout(() => {
      if (controller.signal.aborted || admitted) return;
      controller.abort();
      onError(new Error('等待任务接收回执超时，发送状态未确认。'));
    }, 30_000);
    controller.signal.addEventListener('abort', clearAdmissionTimer, { once: true });
  }

  void (async () => {
    const started = Date.now();
    let first = !resumeOnly;
    let legacyAdmission = false;
    while (!controller.signal.aborted) {
      try {
        const res: Response = first
          ? await projectFetch(projectUrl(`${BASE}/api/chat/stream`), {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ message, persona: persona || undefined, sessionId, turnId, attachments, workshopRef,
                ...(references?.length ? { references } : {}),
                ...(sessionReferences?.length ? { sessionReferences } : {}),
                ...(command ? { command } : {}), ...(planRef ? { planRef } : {}),
                ...(modelOverride ? { modelOverride } : {}) }),
              signal: controller.signal,
            })
          : await projectFetch(projectUrl(`${BASE}/api/chat/jobs/${encodeURIComponent(turnId || '')}/stream?after=${lastEventId}${legacyAdmission ? '&legacyAdmission=true' : ''}`), {
              signal: controller.signal,
            });
        if (controller.signal.aborted) return;
        legacyAdmission ||= res.headers.get('X-Easel-Legacy-Admission') === '1';
        // jobs 流 404 = 任务记录不存在（后端重启丢日志 / 首发请求 5xx 没起 supervisor）。
        // 必须终结：旧逻辑依赖 onRecoveryUnavailable（新发送不传），不传时静默落进
        // 无限重试（1s 一次 ×130 分钟），onError 永不触发，会话卡死在「自动续接」。
        if (!first && res.status === 404) {
          if (onRecoveryUnavailable) onRecoveryUnavailable();
          else if (pendingFailure) onError(chatFailureError(pendingFailure, undefined, pendingUnconfirmed));
          else onError(chatFailureError({ code: 'network', retryable: false, phase: 'finalization' }, '任务记录不存在或已失效，无法续接', true));
          return;
        }
        if (await consume(res, !first)) return;
      } catch (err: unknown) {
        if (controller.signal.aborted || (err instanceof Error && err.name === 'AbortError')) return;
        const status = (err as Error & { status?: number })?.status;
        if (first && status && status >= 400 && status < 500) {
          onError(err instanceof Error ? err : new Error('请求失败'));
          return;
        }
      }
      first = false;
      // Backend chat runs may spend 5 minutes waiting for a session lock and then
      // run for 2 hours. Keep reconnecting for the same end-to-end budget.
      if (!turnId || Date.now() - started >= 130 * 60 * 1000) {
        onError(chatFailureError(pendingFailure ?? { code: 'timeout', retryable: false, phase: 'finalization' }, '连接中断，自动重连超时', !pendingFailure || pendingUnconfirmed));
        return;
      }
      onInterrupted?.();
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  })().finally(clearAdmissionTimer);

  return controller;
}

/** 取某会话最近一轮的完整结果（SSE 断线后据此取回）。 */
export interface LastChatTurnStatus extends Partial<ChatExecutionState> {
  status: string;
  turn_id?: string;
  statusUnavailable?: boolean;
  requestedModel?: string;
  outcome?: Exclude<ChatTurnOutcome, 'unconfirmed'>; clean_end?: boolean; stop_reason?: string;
  error?: ChatFailure;
}

interface LastChatTurn extends LastChatTurnStatus {
  text: string; events?: AgentEvent[]; questions?: ChatQuestion[];
  reasoning_summary?: ReasoningSummary;
  answer_completed?: AnswerCompletion;
}

export async function fetchLastTurn(sessionId: string, turnId?: string, signal?: AbortSignal): Promise<LastChatTurn> {
  const query = turnId ? `?turn_id=${encodeURIComponent(turnId)}` : '';
  const result = await request<LastChatTurn>(`/api/chat/last/${encodeURIComponent(sessionId)}${query}`, { signal });
  const execution = parseChatExecutionState(result);
  return { ...result, executionState: execution?.executionState, queuePosition: execution?.queuePosition,
    reasoning_summary: publicReasoningSummary(result.reasoning_summary),
    answer_completed: parseAnswerCompletion(result.answer_completed),
    error: result.error == null ? undefined : parseChatFailure(result.error) };
}

/** Small status check for stop/archive/delete guards; never use this to recover answer content. */
export async function fetchLastTurnStatus(sessionId: string, turnId?: string, signal?: AbortSignal): Promise<LastChatTurnStatus> {
  const query = `?status_only=true${turnId ? `&turn_id=${encodeURIComponent(turnId)}` : ''}`;
  const result = await request<LastChatTurnStatus>(`/api/chat/last/${encodeURIComponent(sessionId)}${query}`, { signal });
  const execution = parseChatExecutionState(result);
  return { status: result.status, turn_id: result.turn_id,
    statusUnavailable: result.statusUnavailable === true || undefined,
    requestedModel: result.requestedModel, outcome: result.outcome,
    clean_end: result.clean_end, stop_reason: result.stop_reason,
    ...execution, error: result.error == null ? undefined : parseChatFailure(result.error) };
}

interface ChatSessionPage {
  sessions: ChatSession[];
  deletedIds?: string[];
  nextCursor?: string | null;
}

export interface SessionReferencePreview {
  sessionId: string;
  turnId?: string;
  label: string;
  snapshotHash: string;
  preview: { role: 'user' | 'assistant'; content: string }[];
  truncated: boolean;
}

export function fetchSessionReference(sessionId: string, signal?: AbortSignal, turnId?: string): Promise<SessionReferencePreview> {
  const query = turnId ? `&turnId=${encodeURIComponent(turnId)}` : '';
  return request(`/api/chat/session-reference?sessionId=${encodeURIComponent(sessionId)}${query}`, { signal });
}

export interface SessionReferenceTurnPage {
  turns: { turnId: string; label: string }[];
  before: string | null;
}

export function fetchSessionReferenceTurns(sessionId: string, before?: string, signal?: AbortSignal): Promise<SessionReferenceTurnPage> {
  const query = before ? `&before=${encodeURIComponent(before)}` : '';
  return request(`/api/chat/session-reference/turns?sessionId=${encodeURIComponent(sessionId)}${query}`, { signal });
}

export interface SessionModelCapability {
  runtime: string;
  model: string | null;
  lastRequestedModel?: string;
  options: { id: string; label: string }[];
  canSelect: boolean;
  reason?: string;
}

export function fetchSessionModels(sessionId: string, signal?: AbortSignal): Promise<SessionModelCapability> {
  return request(`/api/chat/models?sessionId=${encodeURIComponent(sessionId)}`, { signal });
}

export function fetchChatCommands(signal?: AbortSignal): Promise<{ commands: ChatCommandOption[] }> {
  return request('/api/chat/commands', { signal });
}

export async function fetchChatSessions(signal?: AbortSignal, windowed = false): Promise<{ sessions: ChatSession[]; deletedIds: string[] }> {
  const sessions: ChatSession[] = [];
  const deleted = new Set<string>();
  const seenCursors = new Set<string>();
  let cursor = '';
  while (true) {
    signal?.throwIfAborted();
    const query = cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
    const page = await request<ChatSessionPage>(`/api/chat/sessions?limit=50${windowed ? '&windowed=true' : ''}${query}`, { signal });
    if (!Array.isArray(page?.sessions) || (page.deletedIds !== undefined
      && (!Array.isArray(page.deletedIds) || page.deletedIds.some(id => typeof id !== 'string')))) {
      throw new Error('服务器会话目录回执无效');
    }
    for (const session of page.sessions) sessions.push(session);
    for (const id of page.deletedIds || []) deleted.add(id);
    if (page.nextCursor == null) {
      const deletedIds = [...deleted];
      return { sessions: mergeSessionCatalog([], sessions, deletedIds), deletedIds };
    }
    if (typeof page.nextCursor !== 'string' || !page.nextCursor || seenCursors.has(page.nextCursor) || !page.sessions.length) {
      throw new Error('服务器会话目录游标无效');
    }
    cursor = page.nextCursor;
    seenCursors.add(cursor);
  }
}
export function syncChatSessions(sessions: ChatSession[]): Promise<{ sessions: ChatSession[]; deletedIds?: string[] }> {
  return request('/api/chat/sessions?windowed=true', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessions }) });
}
export function fetchEarlierChatTurns(sessionId: string, before: string, signal?: AbortSignal): Promise<{
  messages: ChatMessage[]; historyWindow: ChatHistoryWindow;
}> {
  return request(`/api/chat/sessions/${encodeURIComponent(sessionId)}/history?before=${encodeURIComponent(before)}`, { signal });
}

export function fetchCompleteChatSession(sessionId: string, signal?: AbortSignal): Promise<ChatSession> {
  return request(`/api/chat/sessions/${encodeURIComponent(sessionId)}/history?full=true`, { signal });
}

export async function forkPrefixHash(messages: readonly ChatMessage[], cutoffIndex: number): Promise<string> {
  const prefix = messages.slice(0, cutoffIndex + 1).map(message => [message.role, message.content]);
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(prefix)));
  return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
}

export function forkChatSession(sourceId: string, body: {
  cutoffIndex: number; cutoffTurnId: string; prefixHash: string; requestId: string;
}): Promise<{ session: ChatSession }> {
  return request(`/api/chat/sessions/${encodeURIComponent(sourceId)}/fork`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
}

/** Read a technical export with the same project and expected-user binding as chat. */
export async function fetchSessionLogZip(sessionId: string, signal: AbortSignal): Promise<{ blob: Blob; filename: string }> {
  const response = await projectFetch(`${BASE}/api/chat/sessions/${encodeURIComponent(sessionId)}/export/logs`, {
    signal, cache: 'no-store',
  });
  if (!response.ok) {
    const message = response.status === 401 || response.status === 403 ? '当前没有技术日志导出权限。'
      : response.status === 404 ? '该对话已删除或不在当前可访问范围。' : '日志暂时无法导出，请稍后重试。';
    throw new Error(message);
  }
  if (response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/zip') {
    throw new Error('服务器没有返回 ZIP 导出文件。');
  }
  const disposition = response.headers.get('Content-Disposition') || '';
  const filename = /^attachment;\s*filename="?((?:(?:xiangya|easel)-session-)[a-z0-9_-]{1,128}\.zip)"?(?:;|$)/i.exec(disposition)?.[1]
    ?? 'xiangya-session-logs.zip';
  return { blob: await response.blob(), filename };
}

export interface ChatDeletionReceipt {
  ok: true;
  sessionId: string;
  catalogDeleted: true;
  nativeHistoryDeleted: false;
}

export async function deleteChatSession(id: string): Promise<ChatDeletionReceipt> {
  const value = await request<Partial<ChatDeletionReceipt>>(`/api/chat/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!value || value.ok !== true || value.sessionId !== id || value.catalogDeleted !== true || value.nativeHistoryDeleted !== false) {
    throw new Error('对话目录删除结果尚未确认。');
  }
  return { ok: true, sessionId: id, catalogDeleted: true, nativeHistoryDeleted: false };
}

// ═══ 设置面板 · 环境安装（install_tool 引擎桥） ═══

export interface EnvTool {
  id: string;
  state: 'ok' | 'missing' | 'no_dir' | 'fail';
  version: string | null;
  detail: string | null;
  name: string;
  group: string;
  group_name?: string;
  desc: string;
  big?: boolean;
}

export interface EnvToolsResponse {
  python: string;
  tools: EnvTool[];
  cachedAt?: number;
}

export function fetchEnvTools(refresh = false): Promise<EnvToolsResponse> {
  return request(`/api/env/tools${refresh ? '?refresh=1' : ''}`);
}

export interface EnvJobResult {
  id: string;
  state: 'ok' | 'fail';
  version?: string | null;
  strategy?: string | null;
  detail?: string | null;
}

export interface EnvJob {
  jobId: string;
  id: string;
  state: 'running' | 'ok' | 'fail';
  lines: string[];
  result: EnvJobResult | null;
  started: number;
  ended: number | null;
}

export function startEnvInstall(id: string): Promise<{ jobId: string; id: string; state: string }> {
  return request('/api/env/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  });
}

export function fetchEnvJob(jobId: string): Promise<EnvJob> {
  return request(`/api/env/job/${encodeURIComponent(jobId)}`);
}

// ═══ 设置面板 · 模型通道（只读 + 真自测） ═══

export interface ModelRow {
  slot?: string;
  order: number;
  name: string;
  sub: string;
  type: string;
  model: string;
  baseUrl: string;
  keyMasked: string;
  role: string;
  result: string;
  protocol?: string;
  thinking?: boolean;
  thinkingFormat?: string;
  keyNew?: string;
  keyNew2?: string;
  key2Label?: string;
  key2Masked?: string;
  modelEditable?: boolean;
  baseEditable?: boolean;
  baseOptional?: boolean;
  adv?: boolean;
  deletable?: boolean;
}

export interface ModelSaveRow {
  slot: string;
  name?: string;
  model: string;
  baseUrl: string;
  key: string;
  key2?: string;
  primary?: boolean;
  /** 自定义供应商上游协议：openai（默认）| anthropic */
  protocol?: string;
  thinking?: boolean;
  thinkingFormat?: string;
}

export interface ModelSaveResponse extends ModelChannelsResponse {
  ok: boolean;
  note?: string;
}

export function saveModelConfig(channel: string, rows: ModelSaveRow[]): Promise<ModelSaveResponse> {
  return request('/api/settings/models/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel, rows }),
  });
}

export interface ModelChannelRows { rows: ModelRow[] }

export interface ModelChannelsResponse {
  transport?: 'api' | 'openclaw';
  channels: {
    chat: ModelChannelRows;
    transcribe: ModelChannelRows;
    image?: ModelChannelRows;
    video?: ModelChannelRows;
    music?: ModelChannelRows;
    speech?: ModelChannelRows;
  };
  primary: string;
}

export function fetchModelChannels(runtime?: string): Promise<ModelChannelsResponse> {
  return request(`/api/settings/models${runtime ? `?runtime=${encodeURIComponent(runtime)}` : ''}`);
}

export function saveChatTransport(transport: 'api' | 'openclaw'): Promise<ModelChannelsResponse> {
  return request('/api/settings/chat/transport', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ transport }),
  });
}

// ── 网络代理设置（EASEL_PROXY；更多设置页） ─────────────────────────

export interface ProxySettings {
  proxy: string;                       // 生效中的代理地址，空串=未配置
  source: 'env' | 'envfile' | 'none';  // env=启动环境固定（只读）；envfile=.env 持久化
}

export function fetchProxySettings(): Promise<ProxySettings> {
  return request('/api/settings/proxy');
}

export function saveProxySettings(proxy: string): Promise<{ ok: boolean; proxy: string; source: string }> {
  return request('/api/settings/proxy', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proxy }),
  });
}

// （保存接口见上方 saveModelConfig）

export interface SelftestResult { baseUrl: string; ok: boolean; ms: number; detail?: string }

export function runChannelSelftest(channel: string): Promise<{ channel: string; results: SelftestResult[]; testedAt: number }> {
  return request('/api/settings/models/selftest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel }),
  });
}

export interface LocalAgentModel {
  id: string;
  name: string;
  contextWindow?: number | null;
}

export interface LocalAgentInfo {
  id: string;
  label: string;
  installed: boolean;
  command: string;
  path: string;
  openclawProvider: string | null;
  configProvider?: string | null;
  supported: boolean;
  configured: boolean;
  usableWithoutKey: boolean;
  loginHint: string;
  models?: LocalAgentModel[];
}

export function fetchLocalAgents(): Promise<{
  agents: LocalAgentInfo[];
  installedCount: number;
  usableWithoutKeyCount: number;
  usableWithoutKey: string[];
}> {
  return request('/api/settings/local-agents');
}

export function enableLocalAgent(id: string, model = ''): Promise<{ ok: boolean; note: string; agent: LocalAgentInfo }> {
  return request('/api/settings/local-agents/enable', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, model }),
  });
}

export interface ModelsFetchResponse { baseUrl: string; models: string[]; fetchedAt: number }

/**
 * 拉取某供应商的模型列表（自定义供应商免手打模型名）。
 * key 留空时后端按 slot 从已存配置里取（用户不用为了拉列表重贴一遍 Key）。
 */
export function fetchAvailableModels(
  baseUrl: string, key: string, protocol: string, slot = '',
  name = '',
): Promise<ModelsFetchResponse> {
  return request('/api/settings/models/available', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ baseUrl, key, protocol, slot, name }),
  });
}
