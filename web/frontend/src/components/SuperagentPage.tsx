import { useCallback, useRef } from 'react';
import { ACTIVE_PROJECT_ID, projectFetch, projectStorageKey } from '../lib/projectScope';
import SuperagentPanel, { type SuperagentTask } from './SuperagentPanel';

const endpoint = '/api/superagent/tasks';
type CreateRequest = { projectId: string; title: string; brief: string };

async function request<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  const response = await projectFetch(endpoint + path, {
    cache: 'no-store', signal,
    headers: { 'Content-Type': 'application/json' },
    ...(body ? { method: 'POST', body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === 'string' ? payload.detail : '任务操作未成功，请核对后重试。');
  }
  return response.json();
}

export function sourceConversation(task: SuperagentTask): string | undefined {
  const payload = task.steps[0]?.payload;
  if (payload && typeof payload === 'object' && 'sessionId' in payload && typeof payload.sessionId === 'string') {
    return payload.sessionId;
  }
}

const projects = [{ id: ACTIVE_PROJECT_ID, name: '当前 IP', canOperate: true }];

export function useChatSuperagent(sessionId: string | undefined, enabled: boolean,
  onCreated?: (sessionId: string, taskId: string, text: string) => void) {
  const pending = useRef(new Map<string, string>());
  const loadTasks = useCallback(async (signal?: AbortSignal) => {
    const catalog = await request<{ tasks: SuperagentTask[] }>('', undefined, signal);
    return catalog.tasks.filter(task => task.projectId === ACTIVE_PROJECT_ID &&
      (!sourceConversation(task) || sourceConversation(task) === sessionId));
  }, [sessionId]);

  const createTask = async (body: CreateRequest): Promise<SuperagentTask> => {
    if (!enabled || !sessionId) throw new Error('当前对话无法提交后台任务。');
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body.brief));
    const digest = Array.from(new Uint8Array(bytes), n => n.toString(16).padStart(2, '0')).join('');
    const key = projectStorageKey(`superagent-intake:${sessionId}:${digest}`);
    let requestId = pending.current.get(key);
    try { requestId ??= sessionStorage.getItem(key) ?? undefined; } catch { /* Keep in-memory deduplication. */ }
    requestId ??= crypto.randomUUID();
    pending.current.set(key, requestId);
    try { sessionStorage.setItem(key, requestId); } catch { /* Keep in-memory deduplication. */ }
    const task = await request<SuperagentTask>('', { ...body, requestId, sessionId });
    if (task.projectId !== ACTIVE_PROJECT_ID || sourceConversation(task) !== sessionId) {
      throw new Error('服务端任务与当前对话不匹配。');
    }
    onCreated?.(sessionId, task.id, body.brief);
    pending.current.delete(key);
    try { sessionStorage.removeItem(key); } catch { /* Optional storage. */ }
    return task;
  };

  const send = async (text: string) => {
    await createTask({ projectId: ACTIVE_PROJECT_ID, title: Array.from(text.trim()).slice(0, 60).join(''), brief: text });
    return true;
  };

  const tasks = enabled && sessionId ? <SuperagentPanel key={sessionId} embedded projects={projects} loadTasks={loadTasks}
    createTask={createTask}
    answerTask={(id, body) => request(`/${encodeURIComponent(id)}/answer`, body)}
    stopTask={(id, body) => request(`/${encodeURIComponent(id)}/stop`, body)}
    retryTask={(id, body) => request(`/${encodeURIComponent(id)}/retry`, body)}
  /> : null;

  return { send, tasks };
}
