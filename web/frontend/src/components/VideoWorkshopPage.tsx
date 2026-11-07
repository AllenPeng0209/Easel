import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useIpPermissions } from './ProjectSpace';
import { IconVideo, IconRefresh, IconChevron } from './icons';
import { createWorkshopProject, fetchWorkshopProject, fetchWorkshopProjects, filterWorkshopProjects, projectLifecycle, WORKSHOP_LIFECYCLE_STATES } from '../lib/workshop';
import type { WorkshopProject, WorkshopProjectInput, WorkshopReference, WorkshopLifecycleKey } from '../lib/workshop';
import HuobaoWorkshopFrame from './HuobaoWorkshopFrame';
import '../styles/workshop.css';
const preview = (brief: string) => brief.replace(/^[#\s]+/g, '').replace(/[|`>*#]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 160);
const dateLabel = (value?: string) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
const states = WORKSHOP_LIFECYCLE_STATES.filter(row => row.key !== 'publishing');
interface Props {
  initialProjectId?: string; initialView?: 'production' | 'review'; initialArtifactId?: string;
  onUseWorkshop?: (reference: WorkshopReference) => void | Promise<void>;
  onOpenChat?: (reference: WorkshopReference) => void | Promise<void>;
  presentation?: 'contained' | 'fullscreen';
}

export default function VideoWorkshopPage({ initialProjectId, initialView, initialArtifactId, onUseWorkshop, onOpenChat, presentation = 'fullscreen' }: Props = {}) {
  const { canOperate } = useIpPermissions();
  const [projects, setProjects] = useState<WorkshopProject[]>([]);
  const [selectedId, setSelectedId] = useState(initialProjectId || ''), [verifiedId, setVerifiedId] = useState('');
  const [appliedNavigation, setAppliedNavigation] = useState(initialProjectId);
  const [query, setQuery] = useState(''), [filter, setFilter] = useState<WorkshopLifecycleKey | 'all'>('all');
  const [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [missing, setMissing] = useState(false);
  const [creating, setCreating] = useState(false), [saving, setSaving] = useState(false);
  const [input, setInput] = useState<WorkshopProjectInput>({ title: '', brief: '', reference_url: '', aspect_ratio: '9:16' });
  const formEpoch = useRef(0), sequence = useRef(0);
  const refresh = useRef<() => void>(() => {});
  const invalid = !!selectedId && !/^[a-f0-9]{32}$/.test(selectedId);
  const project = initialProjectId === appliedNavigation && verifiedId === selectedId ? projects.find(row => row.id === selectedId) : undefined;
  const update = useCallback((next: WorkshopProject) => {
    sequence.current += 1;
    setProjects(rows => rows.map(row => row.id === next.id ? next : row));
  }, []);
  useEffect(() => {
    if (initialProjectId !== appliedNavigation) {
      setSelectedId(initialProjectId || ''); setVerifiedId(''); setAppliedNavigation(initialProjectId);
      setLoading(true); setMissing(false); setError('');
    }
  }, [initialProjectId, appliedNavigation]);
  useEffect(() => {
    const controller = new AbortController();
    let running = false, timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      timer = setTimeout(() => {
        if (document.visibilityState === 'hidden') schedule();
        else void load();
      }, 5000);
    };
    const load = async (manual = false) => {
      if (running || controller.signal.aborted) return;
      running = true; clearTimeout(timer);
      const current = ++sequence.current;
      if (manual) { setLoading(true); setError(''); setMissing(false); }
      let lookingUpProject = false;
      try {
        const result = await fetchWorkshopProjects(controller.signal);
        if (controller.signal.aborted || current !== sequence.current) return;
        lookingUpProject = !!selectedId && !invalid;
        const detail = lookingUpProject ? await fetchWorkshopProject(selectedId, controller.signal) : null;
        if (controller.signal.aborted || current !== sequence.current) return;
        const rows = result.projects.map(row => row.id === detail?.id ? detail : row);
        setProjects(detail && !rows.some(row => row.id === detail.id) ? [detail, ...rows] : rows);
        setVerifiedId(detail?.id || ''); setMissing(false); setError('');
      } catch (cause) {
        if (!controller.signal.aborted && current === sequence.current) {
          setMissing(lookingUpProject && cause instanceof Error && 'status' in cause && cause.status === 404);
          setError(cause instanceof Error ? cause.message : '视频项目暂不可用。');
        }
      } finally {
        running = false;
        if (!controller.signal.aborted) {
          if (current === sequence.current) setLoading(false);
          schedule();
        }
      }
    };
    const manualRefresh = () => { void load(true); };
    const focus = () => { if (document.visibilityState !== 'hidden') void load(); };
    refresh.current = manualRefresh;
    void load();
    window.addEventListener('focus', focus);
    return () => {
      controller.abort(); clearTimeout(timer); window.removeEventListener('focus', focus);
      if (refresh.current === manualRefresh) refresh.current = () => {};
    };
  }, [selectedId, invalid]);
  const back = useCallback(() => { setSelectedId(''); setVerifiedId(''); setError(''); setMissing(false); }, []);
  const open = (id: string) => { setSelectedId(id); setVerifiedId(''); setLoading(true); setMissing(false); setError(''); };
  const closeCreate = () => { formEpoch.current += 1; setCreating(false); };
  const create = async (event: FormEvent) => {
    event.preventDefault(); if (saving) return;
    const epoch = formEpoch.current;
    setSaving(true); setError('');
    try {
      const next = await createWorkshopProject(input);
      setProjects(rows => [next, ...rows]);
      if (epoch === formEpoch.current) { setCreating(false); setSelectedId(next.id); setVerifiedId(next.id); }
      setInput({ title: '', brief: '', reference_url: '', aspect_ratio: '9:16' });
    } catch (cause) { setError(cause instanceof Error ? cause.message : '创建项目未完成。'); }
    finally { setSaving(false); }
  };
  const active = projects.filter(row => !row.archived_at);
  const counts = Object.fromEntries(states.map(state => [state.key, active.filter(row => {
    const key = projectLifecycle(row).key;
    return key === state.key || state.key === 'ready_to_publish' && key === 'publishing';
  }).length]));
  const visible = filterWorkshopProjects(projects, query, filter === 'ready_to_publish' ? 'all' : filter)
    .filter(row => filter !== 'ready_to_publish' || ['ready_to_publish', 'publishing'].includes(projectLifecycle(row).key));
  return <div className="video-workshop" data-workshop-project-id={project?.id}>
    <header className="workshop-head"><div><div className="workshop-eyebrow">XIANGYA VIDEO STUDIO</div><h1><IconVideo size={25} />视频工坊</h1>
      <p>管理每个视频主题，跟进生产、审核与发布。</p></div>
      <div className="workshop-head-actions"><button className="btn btn-sm" aria-label="刷新视频工坊" onClick={() => refresh.current()}><IconRefresh size={14} />刷新</button>
        {canOperate && <button className="btn btn-sm btn-primary" onClick={() => { formEpoch.current += 1; setCreating(true); }}>+ 新建视频项目</button>}</div>
    </header>
    {error && (!selectedId || project) && <div className="workshop-global-error" role="alert">{error}<button onClick={() => refresh.current()}>重试</button></div>}
    {selectedId && !project && <section className="workshop-project-state" role={error && !missing ? 'alert' : 'status'}>
      <h2>{invalid ? '项目地址无效，暂不可用。' : loading ? '正在读取视频项目…' : missing ? '视频项目不存在或已被移除。' : '视频项目暂不可用。'}</h2>
      <p>{invalid ? '请从视频列表重新选择项目。' : loading ? '读取完成后会自动打开工作台。' : missing ? '返回列表查看当前可用的视频项目。' : error}</p>
      <div><button className="btn btn-sm" onClick={back}>返回视频列表</button>{!invalid && !loading && <button className="btn btn-sm" onClick={() => refresh.current()}>重试</button>}</div>
    </section>}
    {!selectedId && <section className="workshop-project-list-page" aria-label="视频列表"><div className="workshop-list-panel">
      <div className="workshop-list-toolbar"><div><h2>{filter === 'archived' ? '归档视频' : '视频项目'}</h2><span>{visible.length} 个主题</span></div>
        <div className="workshop-list-filters"><input className="field" type="search" aria-label="搜索视频主题或创作要求" placeholder="搜索主题" value={query} onChange={event => setQuery(event.target.value)} />
          <button className="workshop-archive-link" aria-label="查看归档视频" aria-pressed={filter === 'archived'} onClick={() => setFilter(filter === 'archived' ? 'all' : 'archived')}>{filter === 'archived' ? '返回列表' : '查看归档'}</button></div></div>
      <div className="workshop-status-filters" role="group" aria-label="视频状态筛选"><button className={filter === 'all' ? 'selected' : ''} aria-label="筛选：全部" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}><span>全部</span>{active.length > 0 && <span className="workshop-filter-count">{active.length}</span>}</button>
        {states.map(state => <button key={state.key} className={filter === state.key ? 'selected' : ''} aria-label={`筛选：${state.label}`} aria-pressed={filter === state.key} onClick={() => setFilter(state.key)}><span>{state.label}</span>{counts[state.key] > 0 && <span className="workshop-filter-count">{counts[state.key]}</span>}</button>)}</div>
      {visible.length ? <div className="workshop-table-scroll"><table className="workshop-project-table" aria-label="视频项目列表"><thead><tr><th scope="col">视频主题</th><th scope="col">状态</th><th scope="col">规格与版本</th><th scope="col">最近更新</th><th scope="col"><span className="workshop-sr-only">操作</span></th></tr></thead>
        <tbody>{visible.map(row => { const lifecycle = projectLifecycle(row); return <tr key={row.id}>
          <td data-label="视频主题"><div className="workshop-topic-cell"><div className="workshop-topic-mark"><IconVideo size={22} /></div><div><button className="workshop-topic-title" onClick={() => open(row.id)}>{row.title}</button><p>{preview(row.brief) || '尚未填写创作要求'}</p></div></div></td>
          <td data-label="状态"><span className={`workshop-lifecycle-status workshop-status-${lifecycle.tone}`}><i />{lifecycle.label}</span><span className="workshop-list-status-detail">{lifecycle.detail}</span></td>
          <td data-label="规格与版本"><span className="workshop-list-spec">{row.aspect_ratio}<span>V{row.revision}</span></span><span className="workshop-list-subtext">{row.deliverables.length ? `${row.deliverables.length} 版成片` : '尚未导出成片'}</span></td>
          <td data-label="最近更新"><time className="workshop-list-updated" dateTime={row.updated_at || row.created_at}>{dateLabel(row.updated_at || row.created_at)}</time></td>
          <td className="workshop-list-row-action"><button className="btn btn-sm" data-project-id={row.id} aria-label={`进入项目：${row.title}`} onClick={() => open(row.id)}>进入项目 <IconChevron size={13} /></button></td>
        </tr>; })}</tbody></table></div> : <div className="workshop-list-empty"><IconVideo size={34} /><h3>{loading ? '正在读取视频项目…' : projects.length ? '没有匹配的视频主题' : '创建你的第一个视频主题'}</h3><p>在 AI 对话中交办制作，结果会自动进入工坊。</p></div>}
    </div></section>}
    {project && <HuobaoWorkshopFrame key={project.id} project={project} initialView={initialView} initialArtifactId={initialArtifactId} onUpdate={update} onBack={back} onUseWorkshop={onUseWorkshop} onOpenChat={onOpenChat} presentation={presentation} />}
    {creating && <div className="workshop-modal-backdrop"><div className="workshop-modal" role="dialog" aria-modal="true" aria-label="新建视频项目" onKeyDown={event => { if (event.key === 'Escape') closeCreate(); }}><form className="workshop-project-form" onSubmit={create}>
      <div className="workshop-section-title"><h2>新建视频项目</h2><button type="button" className="icon-btn" aria-label="关闭项目表单" onClick={closeCreate}>×</button></div>
      <div className="workshop-form-grid"><label><span className="field-label">项目名称</span><input className="field" required autoFocus maxLength={120} value={input.title} onChange={event => setInput({ ...input, title: event.target.value })} /></label>
        <label><span className="field-label">画幅</span><select className="field" value={input.aspect_ratio} onChange={event => setInput({ ...input, aspect_ratio: event.target.value })}><option value="9:16">9:16 · 竖版短视频</option><option value="16:9">16:9 · 横版视频</option><option value="1:1">1:1 · 方形</option><option value="4:5">4:5 · 信息流</option></select></label>
        <label className="workshop-field-wide"><span className="field-label">创作要求</span><textarea className="field" rows={3} maxLength={12000} value={input.brief} onChange={event => setInput({ ...input, brief: event.target.value })} /></label>
        <label className="workshop-field-wide"><span className="field-label">参考视频链接（可选）</span><input className="field" type="url" value={input.reference_url} onChange={event => setInput({ ...input, reference_url: event.target.value })} /></label></div>
      <div className="workshop-form-actions"><button type="button" className="btn btn-sm" onClick={closeCreate}>取消</button><button className="btn btn-sm btn-primary" disabled={saving || !input.title.trim()}>{saving ? '正在创建…' : '创建项目'}</button></div>
    </form></div></div>}
  </div>;
}
