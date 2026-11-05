/** Read-only adapter for the server-owned business objects in the active IP. */
import { ACTIVE_PROJECT_ID, projectFetch, projectUrl } from './projectScope';
import { businessObjectInScope, normalizeBusinessObject } from './businessObjects';
import type { BusinessObject } from './businessObjects';

export type BusinessTarget = {kind: 'workshop'; id: string; artifactId?: string};
export function parseBusinessHash(hash: string, projectId = ACTIVE_PROJECT_ID): BusinessTarget | null {
  const match = /^#\/business\/([^/]+)\/(workshop)\/([A-Za-z0-9_-]{1,160})(?:\/artifact\/([a-f0-9]{32}))?$/.exec(hash);
  if (!match) return null;
  let scope: string;
  try { scope = decodeURIComponent(match[1]); } catch { return null; }
  if (scope !== projectId) return null;
  const kind = match[2] as BusinessTarget['kind'];
  if (!/^[a-f0-9]{32}$/.test(match[3])) return null;
  return {kind, id: match[3], ...(match[4] ? { artifactId: match[4] } : {})};
}

export async function fetchBusinessObject(kind: BusinessObject['kind'], id: string,
  signal?: AbortSignal): Promise<BusinessObject> {
  const query = new URLSearchParams({action: 'inspect', kind, id});
  const response = await projectFetch(projectUrl(`/api/chat/business-objects?${query}`), {cache:'no-store',signal});
  if (!response.ok) throw Object.assign(new Error('业务对象暂不可用，请在当前 IP 核对后重试'), {status: response.status});
  const payload: unknown = await response.json();
  const items = payload && typeof payload === 'object' && 'items' in payload ? payload.items : null;
  const record = Array.isArray(items) && items.length === 1 ? normalizeBusinessObject(items[0]) : null;
  if (!record || !businessObjectInScope(record) || record.kind !== kind || record.id !== id) {
    throw new Error('业务对象与当前身份或请求不匹配');
  }
  return record;
}
