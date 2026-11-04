import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ACTIVE_PROJECT_ID, switchProject } from '../lib/projectScope';
import '../styles/projects.css';
import { useAuth } from './AuthGate';
import AccessMembersPanel from './AccessMembersPanel';
import type { Role } from '../lib/access';
import { projectFetch } from '../lib/projectScope';

interface Project { id: string; name: string; description: string; createdAt: number; role?: Role }
interface Space { projects: Project[]; current: Project; refresh: () => Promise<void> }
const SpaceContext = createContext<Space | null>(null);
const ROLE_NAMES = { owner: '负责人', operator: '运营者', viewer: '查看者' };

// oxlint-disable-next-line react/only-export-components
export function useIpPermissions() {
  const space = useContext(SpaceContext);
  const auth = useAuth();
  const role = space ? (space.current.role || 'viewer') : 'owner';
  return { role, canOperate: role === 'owner' || role === 'operator', canManageAccounts: role === 'owner',
    isPlatformAdmin: space ? !!auth?.user.is_admin : true };
}

const apiBase = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '');
async function projectRequest<T = Project[]>(path = '', options?: RequestInit): Promise<T> {
  const response = await projectFetch(`${apiBase}/api/projects${path}`, {
    cache: 'no-store', ...options, headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'IP 操作失败，请重试');
  return data;
}

export default function ProjectSpace({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const refresh = async () => { setProjects(await projectRequest()); setError(''); };
  useEffect(() => {
    let alive = true;
    const update = () => { void projectRequest().then(items => { if (alive) { setProjects(items); setError(''); setLoaded(true); } })
      .catch(error => { if (alive) setError(error.message); }); };
    update();
    window.addEventListener('focus', update);
    const timer = setInterval(update, 15_000);
    return () => { alive = false; window.removeEventListener('focus', update); clearInterval(timer); };
  }, []);
  const current = projects.find(project => project.id === ACTIVE_PROJECT_ID);
  const createFirst = async (event: FormEvent) => {
    event.preventDefault();
    if (creating || !newName.trim()) return;
    setCreating(true); setError('');
    try {
      const created = await projectRequest<Project>('', { method: 'POST', body: JSON.stringify({name:newName.trim()}) });
      switchProject(created.id);
    } catch (error) { setError(error instanceof Error ? error.message : '创建未完成，请重试'); setCreating(false); }
  };
  if (!current) return <div className="space-loading">
    <div className="space-loading-mark">E</div><h2>{error ? '暂时无法打开 IP' : projects.length ? '请选择可访问的 IP' : loaded ? '还没有可访问的 IP' : '正在打开 IP 空间…'}</h2>
    <p>{error || (projects.length ? '原来的空间已不可用，请选择一个有权限的 IP。' : loaded ? '联系 IP 负责人添加成员权限，或创建你的第一个 IP。' : '准备账号、对话与内容工作区')}</p>
    {error && <button className="btn" onClick={() => { void refresh().catch(error => setError(error.message)); }}>重试</button>}
    {projects.map(project => <button className="btn" key={project.id} onClick={() => switchProject(project.id)}>{project.name} · {ROLE_NAMES[project.role || 'viewer']}</button>)}
    {loaded && !projects.length && <form className="empty-ip-form" onSubmit={createFirst}>
      <input aria-label="第一个 IP 名称" maxLength={60} required value={newName} disabled={creating} placeholder="例如：汽车观察" onChange={event => setNewName(event.target.value)} />
      <button className="btn btn-primary" disabled={creating || !newName.trim()}>{creating ? '创建中…' : '创建我的 IP'}</button>
    </form>}
  </div>;
  return <SpaceContext.Provider value={{ projects, current, refresh }}>{children}</SpaceContext.Provider>;
}

export function ProjectBreadcrumb({ children }: { children?: ReactNode }) {
  const space = useContext(SpaceContext);
  if (!space) return null;
  return <div className="space-breadcrumb"><span className="space-dot" /><strong>{space.current.name}</strong>
    <span className="space-breadcrumb-divider">/</span><span>自媒体工作空间</span>
    <span className="space-breadcrumb-hint">{ROLE_NAMES[space.current.role || 'owner']} · 账号 · 对话 · 内容独立管理</span>{children}</div>;
}

export function ProjectSwitcher() {
  const space = useContext(SpaceContext);
  const [open, setOpen] = useState(false);
  const [manager, setManager] = useState(false);
  const [membersFor, setMembersFor] = useState<Project | null>(null);
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState<Project | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!manager && !open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) { setManager(false); setOpen(false); trigger.current?.focus(); }
      if (event.key !== 'Tab') return;
      const fields = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, textarea');
      if (!fields?.length) return;
      const first = fields[0], last = fields[fields.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [manager, open, busy]);
  if (!space) return null;

  const edit = (project: Project | null) => {
    setEditing(project); setName(project?.name || ''); setDescription(project?.description || ''); setError('');
  };
  const showManager = () => { setOpen(false); edit(null); setManager(true); };
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (busy || !name.trim()) return;
    setBusy(true); setError('');
    try {
      await projectRequest(editing ? `/${editing.id}` : '', {
        method: editing ? 'PATCH' : 'POST', body: JSON.stringify({ name: name.trim(), description: description.trim() }),
      });
      await space.refresh(); edit(null);
    } catch (error) { setError(error instanceof Error ? error.message : '保存失败'); }
    finally { setBusy(false); }
  };
  const showMembers = (project: Project) => { setOpen(false); setManager(false); setMembersFor(project); };
  const visible = space.projects.filter(project => `${project.name} ${project.description}`.toLowerCase().includes(search.toLowerCase()));

  return <>
    <div className="project-switcher">
      <div className="project-eyebrow">IP 管理 <span>{space.projects.length} 个 IP</span></div>
      <button ref={trigger} className={`project-trigger ${open ? 'is-open' : ''}`} aria-expanded={open}
        aria-haspopup="dialog" aria-controls={open ? "ip-picker" : undefined} onClick={() => { setOpen(!open); setSearch(''); }} title="切换 IP">
        <span className="project-avatar">{space.current.name.slice(0, 1)}</span>
        <span className="project-trigger-copy"><strong>{space.current.name}</strong><small>{space.current.description || '独立自媒体工作空间'}</small></span>
        <span className="project-chevron" aria-hidden="true">⇄</span>
      </button>
    </div>
    {open && createPortal(<div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) { setOpen(false); trigger.current?.focus(); } }}>
      <div id="ip-picker" className="project-picker-modal modal" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="ip-picker-title">
        <div className="project-manager-heading"><div><span className="project-eyebrow">CREATOR WORKSPACES</span><h2 id="ip-picker-title">IP 管理</h2></div>
          <button className="project-close" aria-label="关闭 IP 选择" onClick={() => { setOpen(false); trigger.current?.focus(); }}>×</button></div>
        <p className="project-manager-intro">选择一个 IP，进入它的账号、对话与内容空间。</p>
        <input className="project-search" aria-label="搜索 IP" placeholder="搜索 IP 名称或定位…" autoFocus value={search} onChange={event => setSearch(event.target.value)} />
        <div className="project-options">
          {visible.map(project => <button key={project.id} className={`project-option ${project.id === space.current.id ? 'selected' : ''}`}
            aria-current={project.id === space.current.id ? 'true' : undefined}
            onClick={() => { if (project.id !== space.current.id) switchProject(project.id); else { setOpen(false); trigger.current?.focus(); } }}>
            <span className="project-avatar">{project.name.slice(0, 1)}</span>
            <span><strong>{project.name}</strong><small>{ROLE_NAMES[project.role || 'owner']} · {project.description || '跨平台自媒体 IP'}</small></span>
            {project.id === space.current.id ? <span className="project-current-badge">当前 IP</span> : <span className="project-chevron" aria-hidden="true">→</span>}
          </button>)}
          {!visible.length && <p className="project-no-results">没有找到 IP，试试其他名称。</p>}
        </div>
        <div className="project-picker-footer"><span>{space.projects.length} 个 IP · 各平台账号独立管理</span>
          <button className="btn btn-primary" onClick={showManager}>新建 / 管理 IP</button></div>
      </div>
    </div>, document.body)}
    {manager && createPortal(<div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget && !busy) setManager(false); }}>
      <div className="project-manager modal" ref={dialog} role="dialog" aria-modal="true" aria-labelledby="project-manager-title">
        <div className="project-manager-heading"><div><span className="project-eyebrow">CREATOR WORKSPACES</span><h2 id="project-manager-title">IP 管理</h2></div>
          <button className="project-close" aria-label="关闭IP 管理" disabled={busy} onClick={() => { setManager(false); trigger.current?.focus(); }}>×</button></div>
        <p className="project-manager-intro">一个 IP 对应多个社交平台账号，独立管理它的定位、对话、内容与发布。</p>
        <div className="project-manager-body"><div className="project-manager-list">
          {space.projects.map(project => <div key={project.id} className="project-manager-item">
            <span className="project-avatar small">{project.name.slice(0, 1)}</span><div><strong>{project.name}</strong>
              <small>{ROLE_NAMES[project.role || 'owner']} · {project.id === space.current.id ? '当前空间' : project.description || '独立空间'}</small></div>
            <button className="project-text-button" disabled={busy} onClick={() => showMembers(project)}>成员</button>
            {(!project.role || project.role === 'owner') && <button className="project-text-button" disabled={busy} onClick={() => edit(project)}>编辑</button>}
            {project.id !== space.current.id && <button className="project-text-button" disabled={busy} onClick={() => switchProject(project.id)}>进入</button>}
          </div>)}
        </div><form onSubmit={save} className="project-form">
          <h3>{editing ? '编辑 IP' : '新建 IP'}</h3>
          <label htmlFor="project-name">IP 名称</label><input id="project-name" autoFocus maxLength={60} required placeholder="例如：汽车观察、城市生活" value={name} disabled={busy} onChange={event => setName(event.target.value)} />
          <label htmlFor="project-description">简介 <span>选填</span></label><textarea id="project-description" maxLength={500} rows={3} placeholder="这个品牌的定位与内容方向" value={description} disabled={busy} onChange={event => setDescription(event.target.value)} />
          <p className="project-form-note">新 IP 从空白空间开始。创建后点击「进入」，再到「账号」连接该品牌的社交平台账号。</p>
          {error && <p className="project-error" role="alert">{error}</p>}
          <div className="project-form-actions">{editing && <button type="button" className="btn" disabled={busy} onClick={() => edit(null)}>取消编辑</button>}
            <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>{busy ? '保存中…' : editing ? '保存修改' : '＋ 创建 IP'}</button></div>
        </form></div>
      </div>
    </div>, document.body)}
    {membersFor && <AccessMembersPanel projectId={membersFor.id} projectName={membersFor.name} role={membersFor.role} onClose={() => { setMembersFor(null); trigger.current?.focus(); }} onChanged={() => { void space.refresh().catch(error => setError(error.message)); }} />}
  </>;
}
