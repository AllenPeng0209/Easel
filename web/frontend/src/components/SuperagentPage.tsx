import { useCallback, useRef, useState } from 'react';
import { ACTIVE_USER_ID } from '../lib/projectScope';
import SuperagentPanel, { type SuperagentTask } from './SuperagentPanel';

const endpoint = '/api/superagent/tasks';
interface TaskCatalog {
  projects: { id: string; name: string; canOperate: boolean }[];
  tasks: SuperagentTask[];
}

async function request<T>(path: string, body?: object, signal?: AbortSignal): Promise<T> {
  const response = await fetch(endpoint + path, {
    cache: 'no-store', signal,
    headers: { 'Content-Type': 'application/json', 'X-Easel-User': ACTIVE_USER_ID },
    ...(body ? { method: 'POST', body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === 'string' ? payload.detail : '任务操作未成功，请核对后重试。');
  }
  return response.json();
}

export default function SuperagentPage() {
  const [projects, setProjects] = useState<TaskCatalog['projects']>([]);
  const pending = useRef<{ signature: string; id: string } | null>(null);
  const loadTasks = useCallback(async (signal?: AbortSignal) => {
    const result = await request<TaskCatalog>('', undefined, signal);
    if (!signal?.aborted) setProjects(result.projects);
    return result.tasks;
  }, []);

  return <SuperagentPanel projects={projects} loadTasks={loadTasks}
    createTask={async body => {
      const signature = JSON.stringify(body);
      const id = pending.current?.signature === signature ? pending.current.id : crypto.randomUUID();
      pending.current = { signature, id };
      const result = await request<SuperagentTask>('', { ...body, requestId: id });
      if (pending.current?.id === id) pending.current = null;
      return result;
    }}
    queuePublication={body => request("/publish", body)}
    answerTask={(id, body) => request(`/${encodeURIComponent(id)}/answer`, body)}
    stopTask={(id, body) => request(`/${encodeURIComponent(id)}/stop`, body)}
    retryTask={(id, body) => request(`/${encodeURIComponent(id)}/retry`, body)}
  />;
}
