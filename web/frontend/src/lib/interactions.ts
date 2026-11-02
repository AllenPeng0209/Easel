export type InteractionPlatform = 'xiaohongshu' | 'douyin';
export type InteractionKind = 'comment' | 'dm';
export type InteractionMode = 'simulation' | 'manual' | 'official';
export type InteractionQueue = 'pending' | 'review' | 'handled' | 'failed';
export interface InteractionClient { id: string; name: string; created_at?: string }
export interface InteractionChannel {
  id: string; client_id: string; name: string; platform: InteractionPlatform; kind: InteractionKind;
  delivery_mode: InteractionMode; account_label?: string; enabled: boolean; source_url?: string;
  status?: string; capabilities?: { can_sync?: boolean; can_send?: boolean; send_ready?: boolean; message?: string; [key: string]: unknown };
}
export interface InteractionConversation {
  id: string; client_id: string; channel_id: string; platform: InteractionPlatform; kind: InteractionKind;
  delivery_mode: InteractionMode; subject: string; author_name: string; status: InteractionQueue; revision: number;
  preview: string; last_message_at?: string; updated_at: string; human_takeover: boolean; last_error?: string;
}
export interface InteractionDraft {
  id: string; text: string; source: string; knowledge_id?: string; status: 'draft' | 'approved' | 'sent' | 'superseded';
  confidence: number; basis_message_id?: string; approved_at?: string; created_at?: string;
}
export interface InteractionMessage { id: string; external_id?: string; direction: 'inbound' | 'outbound'; text: string; author_name: string; created_at: string; simulated: boolean }
export interface InteractionEvent { id: string; type: string; message: string; created_at: string }
export interface InteractionSend { operation_id: string; status: 'sent' | 'unknown' | 'failed' | 'sending'; external_message_id?: string; receipt_id?: string; mode: string; message?: string; created_at: string }
export interface InteractionDetail extends InteractionConversation { messages: InteractionMessage[]; drafts: InteractionDraft[]; events: InteractionEvent[]; sends: InteractionSend[] }
export interface InteractionKnowledge { id: string; question: string; keywords: string[]; answer: string; auto_send: boolean; enabled: boolean; updated_at?: string }
export interface InteractionRules {
  mode: 'manual' | 'assist' | 'auto_faq'; enabled: boolean; blocked_keywords: string[];
  max_auto_per_hour?: number; max_auto_per_run?: number;
}
export interface InteractionBootstrap {
  client_id: string; clients: InteractionClient[]; channels: InteractionChannel[]; conversations: InteractionConversation[];
  knowledge: InteractionKnowledge[]; rules: InteractionRules; capabilities: unknown[];
  counts: { pending: number; review: number; handled: number; failed: number };
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await projectFetch(projectUrl(`/api/interactions${path}`), { cache: 'no-store', ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers } });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message = typeof data?.detail === 'string' ? data.detail : typeof data?.message === 'string' ? data.message
      : response.status === 409 ? '会话已更新，请刷新后核对，当前修改没有覆盖新消息。' : `互动中心请求未完成（${response.status}）`;
    throw new Error(message);
  }
  return data as T;
}
const encoded = encodeURIComponent;
const post = <T>(path: string, data: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(data) });
export function fetchInteractionBootstrap(clientId = '', signal?: AbortSignal): Promise<InteractionBootstrap> {
  return request(`/bootstrap${clientId ? `?client_id=${encoded(clientId)}` : ''}`, { signal });
}
export function getInteractionConversation(id: string, clientId: string, signal?: AbortSignal): Promise<InteractionDetail> {
  return request(`/conversations/${encoded(id)}?client_id=${encoded(clientId)}`, { signal });
}
export const createInteractionClient = (name: string) => post<InteractionBootstrap>('/clients', { name });
export const seedInteractionSimulation = (client_id: string) => post<InteractionBootstrap>('/simulation', { client_id });
export const buildInteractionDraft = (id: string, client_id: string, expected_revision: number) => post<InteractionDetail>(`/conversations/${encoded(id)}/draft`, { client_id, expected_revision });
export const saveInteractionDraft = (id: string, client_id: string, expected_revision: number, text: string) => request<InteractionDetail>(`/drafts/${encoded(id)}`, { method: 'PATCH', body: JSON.stringify({ client_id, expected_revision, text }) });
export const approveInteractionDraft = (id: string, client_id: string, expected_revision: number) => post<InteractionDetail>(`/drafts/${encoded(id)}/approve`, { client_id, expected_revision });
export const sendInteractionDraft = (id: string, client_id: string, expected_revision: number, operation_id: string) => post<InteractionDetail>(`/drafts/${encoded(id)}/send`, { client_id, expected_revision, operation_id });
export const performInteractionAction = (id: string, client_id: string, expected_revision: number, action: 'takeover' | 'resolve' | 'reopen') => post<InteractionDetail>(`/conversations/${encoded(id)}/action`, { client_id, expected_revision, action });
export const saveInteractionKnowledge = (client_id: string, input: Omit<InteractionKnowledge, 'id' | 'updated_at'>) => post<InteractionBootstrap>('/knowledge', { client_id, ...input });
export const updateInteractionKnowledge = (id: string, client_id: string, input: Partial<Omit<InteractionKnowledge, 'id' | 'updated_at'>>) => request<InteractionBootstrap>(`/knowledge/${encoded(id)}`, { method: 'PATCH', body: JSON.stringify({ client_id, ...input }) });
export const updateInteractionRules = (client_id: string, rules: InteractionRules) => request<InteractionBootstrap>('/rules', {
  method: 'PUT', body: JSON.stringify({ client_id, mode: rules.mode, enabled: rules.enabled,
    blocked_keywords: rules.blocked_keywords,
    ...(rules.max_auto_per_hour !== undefined ? { max_auto_per_hour: rules.max_auto_per_hour } : {}),
    ...(rules.max_auto_per_run !== undefined ? { max_auto_per_run: rules.max_auto_per_run } : {}),
  }),
});
export const runInteractionAutomation = (client_id: string) => post<InteractionBootstrap>('/automation/run', { client_id });
export const createInteractionChannel = (client_id: string, input: Pick<InteractionChannel, 'name' | 'platform' | 'kind' | 'delivery_mode' | 'account_label'> & {
  source_url?: string; config?: Record<string, string>;
}) => post<InteractionBootstrap>('/channels', { client_id, ...input });
export const syncInteractionChannel = (id: string, client_id: string, source_url: string) => post<InteractionBootstrap>(`/channels/${encoded(id)}/sync`, { client_id, source_url });

export function filterInteractionConversations(rows: InteractionConversation[], filters: { platform?: string; kind?: string; status?: string; query?: string } = {}) {
  const query = (filters.query || '').trim().toLocaleLowerCase();
  return rows.filter(row => (!filters.platform || filters.platform === 'all' || row.platform === filters.platform)
    && (!filters.kind || filters.kind === 'all' || row.kind === filters.kind)
    && (!filters.status || filters.status === 'all' || row.status === filters.status || filters.status === 'pending' && row.status === 'failed')
    && (!query || `${row.subject}\n${row.author_name}\n${row.preview}`.toLocaleLowerCase().includes(query)))
    .sort((a, b) => (Date.parse(b.updated_at) || 0) - (Date.parse(a.updated_at) || 0));
}

export function interactionStatus(status: string) {
  return status === 'handled' ? '已处理' : status === 'review' ? '待审核' : status === 'failed' ? '处理异常' : '待处理';
}
export const platformName = (platform: string) => platform === 'douyin' ? '抖音' : '小红书';
export const kindName = (kind: string) => kind === 'dm' ? '私信' : '评论';
import { projectUrl, projectFetch } from './projectScope';
