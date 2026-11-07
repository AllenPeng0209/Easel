import { ACTIVE_USER_ID } from './projectScope';

export type Role = 'owner' | 'operator' | 'viewer';
export type CollaboratorRole = Exclude<Role, 'owner'>;

export interface RepositoryIdentity { id: string; username: string; display_name: string }
export interface Repository {
  id: string; name: string; description: string; createdAt: number; role: Role;
  owner: RepositoryIdentity; created_by: RepositoryIdentity; member_count: number; visibility: 'private';
  permissions: Record<string, boolean>;
}
export interface RepositoryEvent {
  id: number; at: number; project_id: string; action: string;
  actor: RepositoryIdentity; target: RepositoryIdentity; details: Record<string, unknown>;
}

export interface User {
  id: string;
  username: string;
  display_name: string;
  is_admin: boolean;
  active: boolean;
}

export interface DirectoryUser {
  id: string;
  username: string;
  display_name: string;
}

export interface Member {
  user_id: string;
  username: string;
  display_name: string;
  role: Role;
  active: boolean;
  is_admin: boolean;
}

export interface CreateUserInput {
  username: string;
  display_name?: string;
  password: string;
  is_admin?: boolean;
}

export interface PatchUserInput {
  display_name?: string;
  active?: boolean;
  password?: string;
  is_admin?: boolean;
}

export class AccessError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'AccessError';
    this.status = status;
  }
}

const BASE = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '').replace(/\/(?:login|register)$/, '');
const PUBLIC_AUTH_PATHS = new Set(['/api/auth/bootstrap', '/api/auth/login', '/api/auth/register', '/api/auth/setup']);

export async function accessRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  // Identity is carried by the server's HttpOnly cookie, never by a cached user ID.
  if (!path.startsWith('/api/')) throw new AccessError(400, '无效的应用接口');
  const headers = new Headers(options.headers);
  // A tab marker only pins an existing cookie session to the user this request
  // belongs to. It cannot authenticate a user or override the cookie identity.
  const requestUser = ACTIVE_USER_ID || null;
  const privateRequest = path !== '/api/auth/me' && !PUBLIC_AUTH_PATHS.has(path);
  if (privateRequest && !requestUser) {
    throw new AccessError(401, '请登录并刷新工作空间后再操作');
  }
  headers.delete('X-Easel-User');
  if (requestUser && privateRequest) {
    headers.set('X-Easel-User', requestUser);
  }
  if (typeof options.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${BASE}${path}`, {
    ...options, headers, cache: 'no-store', credentials: 'same-origin',
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && requestUser && !PUBLIC_AUTH_PATHS.has(path)) {
      window.dispatchEvent(new CustomEvent('easel:auth-required', { detail: { userId: requestUser } }));
    }
    const detail = typeof data?.detail === 'string' ? data.detail
      : typeof data?.message === 'string' ? data.message : '';
    const fallback = response.status === 401 ? '登录已失效，请重新登录'
      : response.status === 403 ? '你没有执行此操作的权限' : `请求未完成（${response.status}）`;
    throw new AccessError(response.status, detail || fallback);
  }
  return data as T;
}

function send<T>(path: string, method: string, data?: unknown): Promise<T> {
  return accessRequest<T>(path, { method, ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
}

export function fetchAuthBootstrap(): Promise<{ configured: boolean; user?: User | null }> {
  return accessRequest('/api/auth/bootstrap', { headers: { 'X-Easel-Bootstrap-Identity': '1' } });
}

export function fetchAuthUser(): Promise<User> {
  return accessRequest('/api/auth/me');
}

export function setupAccount(input: { username: string; display_name: string; password: string }): Promise<{ user: User }> {
  const { username, display_name, password } = input;
  return send('/api/auth/setup', 'POST', { username, display_name, password });
}

export function loginAccount(input: { username: string; password: string }): Promise<{ user: User }> {
  const { username, password } = input;
  return send('/api/auth/login', 'POST', { username, password });
}

export function registerAccount(input: { username: string; display_name: string; password: string }): Promise<{ user: User }> {
  const { username, display_name, password } = input;
  return send('/api/auth/register', 'POST', { username, display_name, password });
}

export function fetchProjects(): Promise<Repository[]> { return accessRequest('/api/projects'); }
export function fetchProject(id: string): Promise<Repository> { return accessRequest(`/api/projects/${encodeURIComponent(id)}`); }
export function createProject(input: { name: string; description?: string }): Promise<Repository> {
  return send('/api/projects', 'POST', { name: input.name, description: input.description || '' });
}
export function updateProject(id: string, input: { name: string; description: string }): Promise<Repository> {
  return send(`/api/projects/${encodeURIComponent(id)}`, 'PATCH', { name: input.name, description: input.description });
}

export function logoutAccount(): Promise<{ ok: boolean }> {
  return send('/api/auth/logout', 'POST');
}

export function fetchUsers(): Promise<User[]> {
  return accessRequest('/api/users');
}

export function createUser(input: CreateUserInput): Promise<User> {
  const { username, display_name, password, is_admin } = input;
  return send('/api/users', 'POST', { username, display_name, password, is_admin });
}

export function patchUser(id: string, input: PatchUserInput): Promise<User> {
  const { display_name, active, password, is_admin } = input;
  return send(`/api/users/${encodeURIComponent(id)}`, 'PATCH', { display_name, active, password, is_admin });
}

export function fetchUserDirectory(): Promise<DirectoryUser[]> {
  return accessRequest('/api/users/directory');
}

function membersPath(projectId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/members`;
}

export function fetchMembers(projectId: string): Promise<Member[]> {
  return accessRequest(membersPath(projectId));
}

export function addMember(projectId: string, input: { user_id: string; role: CollaboratorRole }): Promise<Member> {
  const { user_id, role } = input;
  return send(membersPath(projectId), 'POST', { user_id, role });
}

export function changeMemberRole(projectId: string, userId: string, role: CollaboratorRole): Promise<Member> {
  return send(`${membersPath(projectId)}/${encodeURIComponent(userId)}`, 'PATCH', { role });
}

export function removeMember(projectId: string, userId: string): Promise<{ ok: boolean }> {
  return send(`${membersPath(projectId)}/${encodeURIComponent(userId)}`, 'DELETE');
}

export function transferOwnership(projectId: string, input: { user_id: string; expected_owner_id: string; confirmation: string }): Promise<Pick<Repository, 'owner' | 'created_by' | 'member_count' | 'visibility'>> {
  const { user_id, expected_owner_id, confirmation } = input;
  return send(`/api/projects/${encodeURIComponent(projectId)}/transfer`, 'POST', { user_id, expected_owner_id, confirmation });
}
export function leaveProject(projectId: string): Promise<{ ok: boolean }> {
  return send(`/api/projects/${encodeURIComponent(projectId)}/leave`, 'POST');
}
export function fetchProjectActivity(projectId: string, before?: number): Promise<{ items: RepositoryEvent[]; next_cursor: number | null }> {
  return accessRequest(`/api/projects/${encodeURIComponent(projectId)}/activity${before ? `?before=${before}` : ''}`);
}
