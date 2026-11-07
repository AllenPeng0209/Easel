// Immutable for this page lifetime: late responses always write to their original space.
// A switch persists the next project, then reloads the entire UI atomically.
function initialUser(): string {
  try { return sessionStorage.getItem('easel_authenticated_user') || ''; } catch { return ''; }
}
export const ACTIVE_USER_ID = initialUser();
function userKey(key: string): string {
  return ACTIVE_USER_ID ? `easel_user:${ACTIVE_USER_ID}:${key}` : key;
}
function initialProject(): string {
  try {
    return sessionStorage.getItem(userKey('easel_project')) || localStorage.getItem(userKey('easel_last_project')) || 'default';
  } catch { return 'default'; }
}

export const ACTIVE_PROJECT_ID = initialProject();

export function projectStorageKey(key: string): string {
  return userKey(ACTIVE_PROJECT_ID === 'default' ? key : `easel_project:${ACTIVE_PROJECT_ID}:${key}`);
}

export function projectUrl(url: string): string {
  if (ACTIVE_PROJECT_ID === 'default') return url;
  return url.replace(/(^|\/)api\/(?!projects(?:\/|$))/, `$1api/projects/${encodeURIComponent(ACTIVE_PROJECT_ID)}/`);
}

export function switchProject(id: string): void {
  // sessionStorage is authoritative per tab; localStorage is only the next new tab's preference.
  sessionStorage.setItem(userKey('easel_project'), id);
  try { localStorage.setItem(userKey('easel_last_project'), id); } catch { /* tab switch still works */ }
  window.location.hash = '#workspace';
  window.location.reload();
}

export function openProjectHome(): void {
  window.location.hash = '#projects';
  window.location.reload();
}

export async function projectFetch(url: string, options?: RequestInit): Promise<Response> {
  // The header is an expected identity, verified against the real cookie by the
  // server. A late callback in an old tab cannot operate as a newly logged-in user.
  let headers = options?.headers;
  if (ACTIVE_USER_ID) {
    const pinned = new Headers(headers);
    pinned.set('X-Easel-User', ACTIVE_USER_ID);
    headers = pinned;
  }
  const response = await fetch(projectUrl(url), { ...options, headers });
  if (response.status === 401 && ACTIVE_USER_ID) {
    window.dispatchEvent(new CustomEvent('easel:auth-required', { detail: { userId: ACTIVE_USER_ID } }));
  }
  return response;
}
