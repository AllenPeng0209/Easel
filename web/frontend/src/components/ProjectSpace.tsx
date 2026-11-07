import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ACTIVE_PROJECT_ID, ACTIVE_USER_ID, openProjectHome, projectFetch, switchProject } from '../lib/projectScope';
import { useAuth } from './AuthGate';
import AccessMembersPanel from './AccessMembersPanel';
import ProjectHome from './ProjectHome';
import type { Repository } from '../lib/access';
import { ScopedReadCache } from '../lib/scopedReadCache';
import '../styles/projects.css';

interface Space { projects: Repository[]; current: Repository; refresh: () => Promise<void> }
const SpaceContext = createContext<Space | null>(null);
const ROLE_NAMES = { owner: 'Owner · 所有者', operator: 'Operator · 运营者', viewer: 'Viewer · 查看者' };

// oxlint-disable-next-line react/only-export-components
export function useIpPermissions() {
  const space = useContext(SpaceContext);
  const auth = useAuth();
  const role = space?.current.role || 'viewer';
  return { role, canOperate: role === 'owner' || role === 'operator', canManageAccounts: role === 'owner',
    isPlatformAdmin: !!auth?.user.is_admin };
}

const apiBase = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '');
const projectReads = new ScopedReadCache();
async function projectRequest<T = Repository[]>(path = '', options?: RequestInit): Promise<T> {
  const read = !options?.method || options.method === 'GET';
  if (!read) projectReads.invalidate();
  const load = async (signal?: AbortSignal): Promise<T> => {
  const response = await projectFetch(`${apiBase}/api/projects${path}`, {
    cache: 'no-store', ...options, headers: { 'Content-Type': 'application/json', ...options?.headers }, ...(signal ? { signal } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'IP 操作失败，请重试');
  return data;
  };
  return read ? projectReads.read(`${ACTIVE_USER_ID}:${apiBase}/api/projects${path}`, load, { signal: options?.signal ?? undefined }) : load();
}

export default function ProjectSpace({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Repository[]>([]);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const refresh = async () => { projectReads.invalidate(); setProjects(await projectRequest()); setError(''); setLoaded(true); };
  useEffect(() => {
    let alive = true, reading = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const visible = () => document.visibilityState !== 'hidden';
    const update = async () => {
      if (!alive || reading) return;
      clearTimeout(timer);
      if (!visible()) return;
      reading = true;
      try {
        const items = await projectRequest<Repository[]>('', { signal: controller.signal });
        if (alive) { setProjects(items); setError(''); setLoaded(true); }
      } catch (error) {
        if (alive && (!(error instanceof Error) || error.name !== 'AbortError')) setError(error instanceof Error ? error.message : '项目列表暂不可用');
      } finally {
        reading = false;
        if (alive && visible()) timer = setTimeout(update, 15000);
      }
    };
    void update();
    window.addEventListener('focus', update);
    document.addEventListener('visibilitychange', update);
    return () => { alive = false; controller.abort(); window.removeEventListener('focus', update); document.removeEventListener('visibilitychange', update); clearTimeout(timer); };
  }, []);
  const current = projects.find(project => project.id === ACTIVE_PROJECT_ID);
  const home = !window.location.hash || window.location.hash === '#projects';
  if (home || !current) return <ProjectHome projects={projects} loaded={loaded} error={error} onRefresh={refresh}
    onOpen={switchProject} onCreate={async input => {
      const created = await projectRequest<Repository>('', { method: 'POST', body: JSON.stringify(input) });
      switchProject(created.id);
    }} />;
  return <SpaceContext.Provider value={{ projects, current, refresh }}>{children}</SpaceContext.Provider>;
}

export function ProjectBreadcrumb({ children }: { children?: ReactNode }) {
  const space = useContext(SpaceContext);
  if (!space) return null;
  return <div className="space-breadcrumb"><span className="space-dot" /><span className="space-owner">{space.current.owner?.username || '项目'}</span>
    <span className="space-breadcrumb-divider">/</span><strong>{space.current.name}</strong><span className="repo-private">私有</span>
    <span className="space-breadcrumb-hint">{ROLE_NAMES[space.current.role || 'viewer']}</span>{children}</div>;
}

export function ProjectSwitcher() {
  const space = useContext(SpaceContext);
  const [open, setOpen] = useState(false);
  const [membersFor, setMembersFor] = useState<Repository | null>(null);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); }
      if (event.key !== 'Tab') return;
      const fields = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input');
      if (!fields?.length) return;
      const first = fields[0], last = fields[fields.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open]);
  if (!space) return null;
  const visible = space.projects.filter(project => `${project.name} ${project.description} ${project.owner?.username || ''}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  return <>
    <div className="project-switcher">
      <button className="project-home-link" onClick={openProjectHome}><span aria-hidden="true">←</span> 所有 IP 项目</button>
      <div className="project-eyebrow">当前 IP 项目 <span>{ROLE_NAMES[space.current.role || 'viewer'].split(' · ')[0]}</span></div>
      <button ref={trigger} className={`project-trigger ${open ? 'is-open' : ''}`} aria-expanded={open}
        aria-haspopup="dialog" aria-controls={open ? 'ip-picker' : undefined} onClick={() => { setOpen(!open); setSearch(''); }} title="切换 IP">
        <span className="project-avatar">{space.current.name.slice(0, 1)}</span>
        <span className="project-trigger-copy"><strong>{space.current.name}</strong><small>{space.current.owner?.username || 'Owner'} / 私有项目</small></span>
        <span className="project-chevron" aria-hidden="true">⇄</span>
      </button>
      <button className="project-settings-link" onClick={() => setMembersFor(space.current)}>{space.current.role === 'owner' ? '项目设置与成员权限' : '查看项目与成员'} <span aria-hidden="true">↗</span></button>
      {error && <p className="project-error" role="alert">{error}</p>}
    </div>
    {open && createPortal(<div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) { setOpen(false); trigger.current?.focus(); } }}>
      <div id="ip-picker" className="project-picker-modal modal" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="ip-picker-title">
        <div className="project-manager-heading"><div><span className="project-eyebrow">YOUR IP PROJECTS</span><h2 id="ip-picker-title">切换 IP 项目</h2></div>
          <button className="project-close" aria-label="关闭 IP 选择" onClick={() => { setOpen(false); trigger.current?.focus(); }}>×</button></div>
        <p className="project-manager-intro">每个项目独立管理账号、内容和成员。你只能访问已加入的项目。</p>
        <input className="project-search" aria-label="搜索 IP" placeholder="搜索项目名称或 Owner…" autoFocus value={search} onChange={event => setSearch(event.target.value)} />
        <div className="project-options">
          {visible.map(project => <button key={project.id} className={`project-option ${project.id === space.current.id ? 'selected' : ''}`}
            aria-current={project.id === space.current.id ? 'true' : undefined}
            onClick={() => { if (project.id !== space.current.id) switchProject(project.id); else { setOpen(false); trigger.current?.focus(); } }}>
            <span className="project-avatar">{project.name.slice(0, 1)}</span>
            <span><strong>{project.name}</strong><small>{project.owner?.username || 'Owner'} / {ROLE_NAMES[project.role || 'viewer']}</small></span>
            {project.id === space.current.id ? <span className="project-current-badge">当前项目</span> : <span className="project-chevron" aria-hidden="true">→</span>}
          </button>)}
          {!visible.length && <p className="project-no-results">没有找到 IP 项目，试试其他名称。</p>}
        </div>
        <div className="project-picker-footer"><span>{space.projects.length} 个已加入的项目</span><button className="btn btn-primary" onClick={openProjectHome}>项目首页 / 新建 IP</button></div>
      </div>
    </div>, document.body)}
    {membersFor && <AccessMembersPanel projectId={membersFor.id} projectName={membersFor.name} role={membersFor.role}
      onClose={() => { setMembersFor(null); trigger.current?.focus(); }} onChanged={() => { void space.refresh().catch(error => setError(error.message)); }} />}
  </>;
}
