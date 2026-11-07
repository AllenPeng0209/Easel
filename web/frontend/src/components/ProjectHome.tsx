import { useState } from 'react';
import type { FormEvent } from 'react';
import { AuthAccountMenu, useAuth } from './AuthGate';
import AccessMembersPanel from './AccessMembersPanel';
import ThemeToggle from './ThemeToggle';
import type { Repository } from '../lib/access';
import '../styles/project-home.css';

const ROLE_NAMES = { owner: 'Owner · 所有者', operator: 'Operator · 运营者', viewer: 'Viewer · 查看者' };

function RepositoryIcon({ size = 22 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H20v19H7.5A2.5 2.5 0 0 1 5 18.5zm0 14a2.5 2.5 0 0 1 2.5-2.5H20M9 6h6M9 10h4" /></svg>;
}

export default function ProjectHome({ projects, loaded, error, onRefresh, onOpen, onCreate }: {
  projects: Repository[];
  loaded: boolean;
  error: string;
  onRefresh: () => Promise<void>;
  onOpen: (id: string) => void;
  onCreate: (input: { name: string; description: string }) => Promise<void>;
}) {
  const auth = useAuth();
  const [filter, setFilter] = useState<'all' | 'owned' | 'participating'>('all');
  const [query, setQuery] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [managing, setManaging] = useState<Repository | null>(null);
  const [refreshError, setRefreshError] = useState('');
  const first = loaded && projects.length === 0;
  const owned = projects.filter(project => project.role === 'owner').length;
  const visible = projects.filter(project => (filter === 'all' || (filter === 'owned' ? project.role === 'owner' : project.role !== 'owner'))
    && `${project.name} ${project.description} ${project.owner?.username || ''}`.toLocaleLowerCase().includes(query.toLocaleLowerCase().trim()));
  const refresh = async () => { setRefreshError(''); try { await onRefresh(); } catch (error) { setRefreshError(error instanceof Error ? error.message : '项目列表暂时无法刷新'); } };
  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (creating || !name.trim()) return;
    setCreating(true); setCreateError('');
    try { await onCreate({ name: name.trim(), description: description.trim() }); }
    catch (error) { setCreateError(error instanceof Error ? error.message : '创建未完成，请重试'); }
    finally { setCreating(false); }
  };

  return <div className="project-home">
    <header className="project-home-header">
      <a className="chirpiva-wordmark" href="#projects" aria-label="Chirpiva 响芽项目首页"><span className="chirpiva-mark" aria-hidden="true">c</span><strong>Chirpiva<span>响芽</span></strong></a>
      <span className="project-home-nav">项目空间</span>
      <div className="project-home-account"><ThemeToggle /><AuthAccountMenu /></div>
    </header>
    <main className="project-home-main" aria-busy={!loaded}>
      <section className={`project-home-hero ${first ? 'is-first' : ''}`}>
        <div className="project-home-hero-copy"><div className="project-home-kicker"><span />CREATOR WORKSPACE</div>
          <p className="project-home-greeting">{auth?.user.display_name || auth?.user.username || '创作者'}，欢迎来到响芽</p>
          <h1>{first ? <>你的第一个 IP，<br /><em>从这里开始生长。</em></> : <>每一个 IP，<br /><em>都有自己的主场。</em></>}</h1>
          <p>{first ? '为你的品牌起一个名字。创建后，你就是这个项目的 Owner，可以连接平台账号、开始创作，并邀请伙伴协作。' : '这里是你拥有和参与的 IP 项目。选择一个项目，继续创作、发布，与团队一起把好内容做下去。'}</p>
          {!first && loaded && <button className="btn btn-primary project-home-create" onClick={() => setCreateOpen(true)}>＋ 创建 IP 项目 <span aria-hidden="true">↗</span></button>}
        </div>
        <div className="project-home-art" aria-hidden="true"><span className="home-orbit orbit-one" /><span className="home-orbit orbit-two" /><span className="home-orbit orbit-three" />
          <div className="home-art-card card-back" /><div className="home-art-card card-middle" />
          <div className="home-art-card card-front"><span className="home-art-icon"><RepositoryIcon size={30} /></span><span className="repo-private">私有项目</span><strong>你的下一个好 IP</strong><p>YOUR NEXT GREAT IDEA</p><div><span>你来拥有</span><b>Owner</b></div></div>
          <span className="home-art-note">让每个想法，都有生长的空间。</span>
        </div>
      </section>
      {(error || refreshError) && <div className="project-home-error" role="alert"><span>{error || refreshError}</span><button className="btn btn-sm" onClick={() => { void refresh(); }}>重新加载</button></div>}
      {!loaded && <div className="project-home-loading" role="status"><span /><p>正在加载你的 IP 项目…</p></div>}
      {loaded && (first || createOpen) && <section className="project-create-section" aria-labelledby="create-ip-title">
        <form className="project-create-form" onSubmit={create}>
          <div className="project-create-heading"><div><span className="project-home-kicker">NEW PROJECT</span><h2 id="create-ip-title">{first ? '创建你的第一个 IP 项目' : '创建 IP 项目'}</h2></div><span className="repo-private">仅成员可见</span></div>
          <label htmlFor="new-project-name">IP 项目名称 <span>*</span></label>
          <div className="project-name-namespace"><span title={auth?.user.username}>{auth?.user.username || '你'} /</span><input id="new-project-name" name="name" autoFocus={createOpen} autoComplete="off" required maxLength={60} placeholder="例如：城市观察" disabled={creating} value={name} onChange={event => setName(event.target.value)} /></div>
          <label htmlFor="new-project-description">一句话介绍 <span className="optional">选填</span></label>
          <textarea id="new-project-description" name="description" rows={2} maxLength={500} placeholder="这个 IP 关注什么，想把内容带给谁？" disabled={creating} value={description} onChange={event => setDescription(event.target.value)} />
          <div className="project-create-ownership"><span className="access-avatar">{(auth?.user.display_name || auth?.user.username || '你').slice(0, 1)}</span><div><strong>项目由你拥有</strong><p>你是唯一的 Owner，成员和权限由你管理。</p></div><span className="access-role-badge role-owner">Owner</span></div>
          {createError && <p className="access-error" role="alert">{createError}</p>}
          <div className="project-create-actions">{!first && <button type="button" className="btn" disabled={creating} onClick={() => { setCreateOpen(false); setCreateError(''); }}>取消</button>}
            <button className="btn btn-primary" disabled={creating || !name.trim()}>{creating ? '正在创建…' : '创建项目并进入'}<span aria-hidden="true"> →</span></button></div>
        </form>
        <aside className="project-start-guide"><h3>一个项目，装下 IP 的全部。</h3><p>每个项目拥有独立的账号、内容和运营空间。</p>
          <ol><li><span>01</span><div><strong>建立自己的主场</strong><p>创建项目，确定 IP 的名字与方向。</p></div></li><li><span>02</span><div><strong>连接平台，开始创作</strong><p>连接社交账号，让 AI 帮你完成内容。</p></div></li><li><span>03</span><div><strong>让合适的伙伴加入</strong><p>添加 Operator 运营，或 Viewer 只读查看。</p></div></li></ol>
          {first && <div className="project-invite-note">来加入团队？把你的用户名 <strong>@{auth?.user.username}</strong> 告诉项目 Owner。被添加后，项目会出现在这里。<button type="button" onClick={() => { void refresh(); }}>刷新项目列表 ↻</button></div>}
        </aside>
      </section>}
      {loaded && !first && <section className="project-repositories" aria-labelledby="project-list-title">
        <div className="project-list-heading"><div><h2 id="project-list-title">IP 项目 <span>{projects.length}</span></h2><p>项目归创建者所有，协作者按角色参与。</p></div><button className="project-text-button" onClick={() => { void refresh(); }}>刷新 ↻</button></div>
        <div className="project-list-toolbar"><div className="project-filter-tabs" aria-label="筛选 IP 项目">{([
          ['all', '全部项目', projects.length], ['owned', '我拥有的', owned], ['participating', '我参与的', projects.length - owned],
        ] as const).map(([value, label, count]) => <button key={value} data-project-filter={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}<span>{count}</span></button>)}</div>
          <input type="search" aria-label="搜索 IP 项目" placeholder="搜索项目名称、Owner…" value={query} onChange={event => setQuery(event.target.value)} /></div>
        <div className="repository-grid">{visible.map(project => <article className="repository-card" data-repository={project.id} key={project.id}>
          <div className="repository-card-top"><span className="repository-card-icon"><RepositoryIcon /></span><span className="repo-private">私有</span><button className="project-text-button" aria-label={`${project.name} 成员与设置`} onClick={() => setManaging(project)}>{project.role === 'owner' ? '管理项目' : '查看成员'} <span aria-hidden="true">↗</span></button></div>
          <div className="repository-namespace">{project.owner?.username || '项目 Owner'} /</div>
          <h3><button data-open-project={project.id} onClick={() => onOpen(project.id)}>{project.name}</button></h3>
          <p className="repository-description">{project.description || '还没有填写简介，让好内容从这里开始。'}</p>
          <div className="repository-card-foot"><span className={`access-role-badge role-${project.role}`}>{ROLE_NAMES[project.role]}</span><span>{project.member_count || 1} 位成员</span><button aria-label={`进入 ${project.name}`} onClick={() => onOpen(project.id)}>进入项目 <span aria-hidden="true">→</span></button></div>
        </article>)}
          {filter !== 'participating' && !query && <button className="repository-new-card" onClick={() => setCreateOpen(true)}><span>＋</span><strong>让下一个想法有个家</strong><small>创建新的 IP 项目</small></button>}
        </div>
        {!visible.length && <div className="project-search-empty"><RepositoryIcon size={30} /><h3>{query ? '没有找到匹配的项目' : filter === 'participating' ? '你还没有参与其他项目' : '你还没有自己的 IP 项目'}</h3><p>{query ? '试试其他名称或 Owner 用户名。' : filter === 'participating' ? '让项目 Owner 通过用户名添加你，项目就会出现在这里。' : '点击「创建 IP 项目」，建立自己的创作空间。'}</p></div>}
      </section>}
      <footer className="project-home-footer"><span>CHIRPIVA · 让好内容生长</span><span>私有项目 · 明确归属 · 按角色协作</span></footer>
    </main>
    {managing && <AccessMembersPanel projectId={managing.id} projectName={managing.name} role={managing.role} onClose={() => setManaging(null)} onChanged={() => { void refresh(); }} />}
  </div>;
}
