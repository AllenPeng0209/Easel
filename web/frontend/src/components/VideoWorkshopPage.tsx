import { useIpPermissions } from './ProjectSpace';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { IconVideo, IconRefresh, IconChevron, IconCheck } from './icons';
import {
  addWorkshopComment, addWorkshopReview, connectWorkshopCanvas, createWorkshopProject,
  archiveWorkshopProject, deliveryReviewState, fetchWorkshopProject, fetchWorkshopProjects, fetchWorkshopProjectsWithActive, fetchWorkshopStatus,
  filterWorkshopProjects, projectLifecycle, recordWorkshopPublication, reviewSummary, safeRuntimeUrl, WORKSHOP_LIFECYCLE_STATES,
  updateWorkshopProject, workshopCanvasUrl, workshopMediaUrl, WorkshopRenderFlow,
} from '../lib/workshop';
import type {
  ReviewDecision, ReviewKind, WorkshopDeliverable, WorkshopProject,
  WorkshopProjectInput, WorkshopStatus, WorkshopLifecycleKey,
} from '../lib/workshop';
import '../styles/workshop.css';

const STAGES: Record<string, string> = {
  draft: '创作准备', planning: '分镜策划', generating: '素材生成', editing: '编辑中',
  rendered: '待审阅', ready_for_review: '待审阅', reviewed: '已审阅',
  approved: '人工审阅通过', changes_requested: '待修改', simulated_reviewed: '已模拟审阅',
};

function timeLabel(value: number) {
  const seconds = Math.max(0, Number.isFinite(value) ? value : 0);
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
}

function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', { hour12: false }) : '';
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '操作未完成，请稍后重试。';
}

function referenceLink(value: string): string | null {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function ProjectForm({ project, onSave, onCancel }: {
  project?: WorkshopProject;
  onSave: (input: WorkshopProjectInput, canvasId: string) => Promise<void>;
  onCancel: () => void;
}) {
  const { canOperate } = useIpPermissions();
  const [input, setInput] = useState<WorkshopProjectInput>(() => ({
    title: project?.title || '', brief: project?.brief || '',
    reference_url: project?.reference_url || '', aspect_ratio: project?.aspect_ratio || '9:16',
  }));
  const [canvasId, setCanvasId] = useState(project?.beeftv_canvas_id || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canOperate || !input.title.trim() || saving) return;
    setSaving(true); setError('');
    try { await onSave({ ...input, title: input.title.trim() }, canvasId.trim()); }
    catch (cause) { setError(errorMessage(cause)); }
    finally { setSaving(false); }
  };
  return <form className="workshop-project-form" onSubmit={submit} onKeyDown={event => { if (saving && event.key === 'Escape') event.stopPropagation(); }}>
    <div className="workshop-section-title"><h2>{project ? '编辑项目' : '新建视频项目'}</h2>
      <button type="button" className="icon-btn" aria-label="关闭项目表单" onClick={onCancel} disabled={saving}>×</button></div>
    <div className="workshop-form-grid">
      <label><span className="field-label">项目名称</span>
        <input className="field" required maxLength={120} autoFocus value={input.title}
          placeholder="例如：视频工坊 · 营销样片" onChange={event => setInput({ ...input, title: event.target.value })} /></label>
      <label><span className="field-label">画幅</span>
        <select className="field" value={input.aspect_ratio}
          onChange={event => setInput({ ...input, aspect_ratio: event.target.value })}>
          <option value="9:16">9:16 · 竖版短视频</option><option value="16:9">16:9 · 横版视频</option>
          <option value="1:1">1:1 · 方形</option><option value="4:5">4:5 · 信息流</option>
        </select></label>
      <label className="workshop-field-wide"><span className="field-label">创作要求</span>
        <textarea className="field" rows={3} value={input.brief} maxLength={12000}
          placeholder="推广对象、受众、时长、卖点，以及希望保留或调整的内容。"
          onChange={event => setInput({ ...input, brief: event.target.value })} /></label>
      <label className="workshop-field-wide"><span className="field-label">参考视频链接（可选）</span>
        <input className="field" type="url" value={input.reference_url} maxLength={2000}
          placeholder="https://…" onChange={event => setInput({ ...input, reference_url: event.target.value })} /></label>
      {project && <details className="workshop-field-wide workshop-advanced"><summary>高级关联设置</summary>
        <label><span className="field-label">关联画布编号</span><input className="field" value={canvasId} maxLength={160}
          onChange={event => setCanvasId(event.target.value)} /></label></details>}
    </div>
    {error && <p className="workshop-error" role="alert">{error}</p>}
    <div className="workshop-form-actions"><button type="button" className="btn btn-sm" onClick={onCancel} disabled={saving}>取消</button>
      <button className="btn btn-sm btn-primary" disabled={saving || !input.title.trim()}>{saving ? '正在保存…' : project ? '保存修改' : '创建项目'}</button></div>
  </form>;
}

function ExportControls({ project, connected, onUpdate, onOpenReview, observeOnly = false }: {
  project: WorkshopProject;
  connected: boolean;
  onUpdate: (project: WorkshopProject) => void;
  onOpenReview: () => void;
  observeOnly?: boolean;
}) {
  const { canOperate } = useIpPermissions();
  const [flow] = useState(() => new WorkshopRenderFlow(project.id, project.renders || []));
  const [receipt, setReceipt] = useState(() => flow.receipt);
  const [starting, setStarting] = useState(false);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState('');
  const latestProject = useRef(project);
  const receiptId = receipt?.id;
  const receiptStatus = receipt?.status;
  const artifactId = receipt?.artifact_id;

  useEffect(() => {
    latestProject.current = project;
    const previous = flow.receipt;
    const restored = flow.restore(project.renders || []);
    const recovered = previous?.status === 'failed' && restored && ['queued', 'running'].includes(restored.status);
    if (restored?.id !== previous?.id || recovered) { setPaused(false); setError(''); }
    setReceipt(restored);
  }, [flow, project]);

  useEffect(() => {
    if (!canOperate || !receiptId || artifactId || paused || receiptStatus === 'failed' || receiptStatus === 'cancelled') return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const advance = async () => {
      try {
        const next = await flow.poll();
        if (stopped) return;
        setReceipt(next);
        if (next.status === 'succeeded') {
          if (observeOnly) { setPaused(true); return; }
          const delivered = await flow.collect(latestProject.current);
          if (stopped) return;
          setReceipt(flow.receipt);
          onUpdate(delivered);
        } else if (!['failed', 'cancelled'].includes(next.status)) {
          timer = window.setTimeout(() => void advance(), 3000);
        }
      } catch (cause) {
        if (!stopped) { setError(errorMessage(cause)); setPaused(true); }
      }
    };
    timer = window.setTimeout(() => void advance(), receiptStatus === 'succeeded' ? 0 : 3000);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [flow, receiptId, receiptStatus, artifactId, paused, onUpdate, canOperate, observeOnly]);

  const collectReady = observeOnly && receipt?.status === 'succeeded' && !receipt.artifact_id;
  const start = async () => {
    if (!canOperate || starting || (!collectReady && (!connected || !project.beeftv_canvas_id))) return;
    setStarting(true); setError(''); setPaused(false);
    try {
      if (collectReady) { const next = await flow.collect(project); setReceipt(flow.receipt); onUpdate(next); }
      else setReceipt(await flow.start(project));
    }
    catch (cause) { setError(errorMessage(cause)); setPaused(true); }
    finally { setStarting(false); }
  };

  const active = receipt && !receipt.artifact_id && ['queued', 'running', 'succeeded'].includes(receipt.status);
  const busy = starting || (!!active && !paused);
  const failed = receipt && ['failed', 'cancelled'].includes(receipt.status);
  const unconfirmed = failed && receipt.submission_state === 'unconfirmed';
  const progress = Math.round(Math.max(0, Math.min(1, receipt?.progress || 0)) * 100);
  const label = starting ? '正在提交导出任务…' : !receipt ? '使用当前画布素材合成完整视频'
    : receipt.artifact_id ? '成片已导出，可进入交付与审阅'
      : receipt.status === 'queued' ? '导出任务正在排队'
        : receipt.status === 'running' ? `正在导出成片 · ${progress}%`
          : receipt.status === 'succeeded' ? paused ? '导出完成，交付登记待处理' : '导出完成，正在登记交付…'
            : unconfirmed ? '导出提交结果待核对'
              : receipt.status === 'cancelled' ? '导出任务已取消' : '导出任务失败';

  return <section className="workshop-export-bar" aria-label="成片导出">
    <div className="workshop-export-status"><strong role="status">{label}</strong>
      {receipt && !receipt.artifact_id && ['queued', 'running'].includes(receipt.status)
        && <div className="workshop-export-progress" role="progressbar" aria-label="成片导出进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
          <span style={{ width: `${progress}%` }} /></div>}
      {(error || (failed && receipt.message)) && <p className="workshop-export-error" role="alert">{error || receipt?.message}</p>}
    </div>
    <div className="workshop-export-actions">
      {paused && active && !collectReady && <button className="btn btn-sm" type="button" onClick={() => { setError(''); setPaused(false); }}>继续检查导出</button>}
      {receipt?.artifact_id && <button className="btn btn-sm" type="button" onClick={onOpenReview}>查看交付</button>}
      <button className="btn btn-sm btn-primary" type="button" disabled={busy || (!collectReady && (!connected || !project.beeftv_canvas_id))} onClick={() => void start()}>
        {busy ? '正在导出…' : collectReady ? '收取已导出的成片' : unconfirmed ? '重试核对导出' : '导出当前成片'}</button>
    </div>
  </section>;
}

function ReviewPanel({ project, onUpdate }: { project: WorkshopProject; onUpdate: (project: WorkshopProject) => void }) {
  const { canOperate } = useIpPermissions();
  const [artifactId, setArtifactId] = useState('');
  const [kind, setKind] = useState<ReviewKind>('human');
  const [notes, setNotes] = useState('');
  const [reviewNotes, setReviewNotes] = useState('');
  const [at, setAt] = useState('0');
  const [checkedFor, setCheckedFor] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const player = useRef<HTMLVideoElement>(null);
  const artifacts = project.deliverables || [];
  const artifact = artifacts.find(item => item.id === artifactId) || artifacts.at(-1);
  const media = artifact ? workshopMediaUrl(artifact.path) : null;
  const summary = reviewSummary(project, artifact);
  const eligibility = deliveryReviewState(project, artifact);
  const current = eligibility.canReview;
  const target = artifact ? `${project.revision}:${artifact.id}:${artifact.sha256}` : '';
  const checked = !!target && checkedFor === target;
  const setArtifact = (id: string) => { setArtifactId(id); setCheckedFor(''); setNotice(''); setError(''); };

  const comment = async (event: FormEvent) => {
    event.preventDefault();
    const seconds = Number(at);
    if (!canOperate || !notes.trim() || !Number.isFinite(seconds) || seconds < 0 || busy || (artifact && !current)) return;
    setBusy(true); setError(''); setNotice('');
    try {
      onUpdate(await addWorkshopComment(project.id, { expected_revision: project.revision,
        at_seconds: seconds, notes: notes.trim(), kind }));
      setNotes(''); setNotice('时间戳意见已保存。');
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  };

  const review = async (decision: ReviewDecision) => {
    if (!canOperate || !artifact || !current || !checked || busy || (decision === 'changes_requested' && !reviewNotes.trim())) return;
    setBusy(true); setError(''); setNotice('');
    try {
      onUpdate(await addWorkshopReview(project.id, { expected_revision: project.revision,
        artifact_id: artifact.id, decision, kind, notes: reviewNotes.trim() }));
      setCheckedFor('');
      setNotice(kind === 'simulated' ? '模拟审阅结论已记录，仍需真人审阅后定稿。' : '当前成片版本的人工审阅结论已记录。');
    } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  };

  return <div className="workshop-review-layout">
    <section className="workshop-panel workshop-delivery">
      <div className="workshop-section-title"><h2>成片预览</h2>
        <span className={`workshop-pill ${summary.humanApproved ? 'ok' : ''}`}>{summary.label}</span></div>
      {artifact ? <>
        <label className="workshop-version-select"><span>交付版本</span>
          <select className="field" aria-label="交付版本" value={artifact.id} onChange={event => setArtifact(event.target.value)}>
            {[...artifacts].reverse().map((item: WorkshopDeliverable) => <option key={item.id} value={item.id}>
              {`V${item.revision} · ${dateLabel(item.created_at)}${item.id === artifacts.at(-1)?.id ? ' · 最新' : ''}`}
            </option>)}
          </select></label>
        {media ? <video key={media} ref={player} className="workshop-player" src={media} controls preload="metadata"
          onLoadedMetadata={() => setCheckedFor('')} /> : <p className="workshop-error">交付文件路径不可用，请重新检查导出结果。</p>}
        <div className="workshop-delivery-foot"><span>工程 V{artifact.revision}</span>
          {media && <a className="btn btn-sm" href={`${media}?download=1`} download>下载成片</a>}</div>
        {!current && <p className="workshop-hint" role="status">{eligibility.message}</p>}
      </> : <div className="workshop-empty-inline"><IconVideo size={32} /><h3>等待第一版成片</h3>
        <p>在画布中完成分镜和素材，剪辑导出后，成片及其版本会出现在这里。</p></div>}
      <div className="workshop-history"><h3>审阅记录</h3>
        {(project.reviews || []).length === 0 ? <p className="workshop-hint">尚未记录审阅结论。</p>
          : [...project.reviews].reverse().map(item => <div className="workshop-review-record" key={item.id}>
            <div><strong>{item.kind === 'simulated' ? '模拟审阅' : '人工审阅'} · {item.decision === 'approved' ? '通过' : '要求修改'}</strong>
              <span>工程 V{item.revision} · {dateLabel(item.created_at)}</span></div>
            {item.notes && <p>{item.notes}</p>}
          </div>)}
      </div>
    </section>
    <div className="workshop-review-side">
      {canOperate && <><section className="workshop-panel">
        <div className="workshop-section-title"><h2>审阅方式</h2></div>
        <label className="workshop-kind-select"><span className="field-label">本次操作身份</span>
          <select className="field" value={kind} onChange={event => { setKind(event.target.value as ReviewKind); setCheckedFor(''); }}>
            <option value="human">人工审阅</option><option value="simulated">模拟审阅 · 流程验收</option>
          </select></label>
        <p className="workshop-hint">{kind === 'simulated' ? '用于模拟操作、记录检查发现，不代表真人已经审核通过。' : '由审阅人核对当前成片的画面、声音、字幕及内容，再记录结论。'}</p>
      </section>
      <form className="workshop-panel" onSubmit={comment}>
        <div className="workshop-section-title"><h2>添加修改意见</h2></div>
        <div className="workshop-timestamp"><label><span className="field-label">位置（秒）</span>
          <input className="field" type="number" min="0" step="0.1" value={at} onChange={event => setAt(event.target.value)} /></label>
          <button className="btn btn-sm" type="button" disabled={!media} onClick={() => setAt((player.current?.currentTime || 0).toFixed(1))}>使用播放位置</button></div>
        <label><span className="field-label">意见</span><textarea className="field" rows={3} maxLength={8000} value={notes}
          placeholder="例如：00:08 的卖点字幕停留太短，延长半秒。" onChange={event => setNotes(event.target.value)} /></label>
        <button className="btn btn-sm" disabled={busy || (!!artifact && !current) || !notes.trim() || !Number.isFinite(Number(at)) || Number(at) < 0}>保存时间戳意见</button>
      </form>
      <section className="workshop-panel">
        <div className="workshop-section-title"><h2>当前版本结论</h2></div>
        <label><span className="field-label">审阅备注</span><textarea className="field" rows={3} maxLength={8000} value={reviewNotes}
          placeholder="记录修改要求或通过依据。" onChange={event => setReviewNotes(event.target.value)} /></label>
        <label className="workshop-check"><input type="checkbox" checked={checked} disabled={!artifact || !current}
          onChange={event => setCheckedFor(event.target.checked ? target : '')} /><span>我已核对当前版本的画面、声音与字幕</span></label>
        <div className="workshop-review-actions">
          <button className="btn btn-sm" disabled={busy || !current || !checked || !reviewNotes.trim()} onClick={() => void review('changes_requested')}>要求修改</button>
          <button className="btn btn-sm btn-primary" disabled={busy || !current || !checked} onClick={() => void review('approved')}>
            <IconCheck size={14} />{kind === 'simulated' ? '记录模拟通过' : '确认人工审阅通过'}</button>
        </div>
        <p className="workshop-hint">结论只针对这份成片和当前工程版本；内容变更后需要重新审阅。</p>
      </section>
      </>}
      {!canOperate && <p className="workshop-hint" role="status">只读权限：可以查看成片、审阅结论与修改意见。</p>}
      {(project.comments || []).length > 0 && <section className="workshop-panel workshop-comments"><h2>时间戳意见</h2>
        {[...(project.comments || [])].reverse().map(item => <div className="workshop-comment" key={item.id}>
          <button type="button" className="workshop-time-link" disabled={!media} onClick={() => { if (player.current) player.current.currentTime = item.at_seconds; }}>{timeLabel(item.at_seconds)}</button>
          <span>{item.kind === 'simulated' ? '模拟' : '人工'} · V{item.revision}</span><p>{item.notes}</p>
        </div>)}
      </section>}
      {error && <p className="workshop-error" role="alert">{error}</p>}
      {notice && <p className="workshop-notice" role="status">{notice}</p>}
    </div>
  </div>;
}

interface VideoWorkshopPageProps { initialProjectId?: string }

export default function VideoWorkshopPage({ initialProjectId }: VideoWorkshopPageProps = {}) {
  const { canOperate, isPlatformAdmin } = useIpPermissions();
  const canEditCanvas = canOperate && isPlatformAdmin;
  const [status, setStatus] = useState<WorkshopStatus>({ state: 'starting', message: '正在检查创作服务…' });
  const [projects, setProjects] = useState<WorkshopProject[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [verifiedSelectedId, setVerifiedSelectedId] = useState('');
  const requestedNavigationId = initialProjectId || '';
  const initialTarget = /^[a-f0-9]{32}$/.test(requestedNavigationId) ? requestedNavigationId : '';
  const [appliedNavigationId, setAppliedNavigationId] = useState(requestedNavigationId);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [form, setForm] = useState<'create' | 'edit' | 'publish' | null>(null);
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<WorkshopLifecycleKey | 'all'>('all');
  const [metadataBusy, setMetadataBusy] = useState(false);
  const [metadataError, setMetadataError] = useState('');
  const [publication, setPublication] = useState({ platform: '', url: '' });
  const [editingProject, setEditingProject] = useState<WorkshopProject | null>(null);
  const [viewChoice, setViewChoice] = useState<{ projectId: string; tab: 'canvas' | 'review' } | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [frameVersion, setFrameVersion] = useState(0);
  const [connectingCanvas, setConnectingCanvas] = useState(false);
  const [canvasError, setCanvasError] = useState('');
  const [canvasSession, setCanvasSession] = useState<{ key: string; url: string } | null>(null);
  const loadSequence = useRef(0);
  const selectedProjectId = useRef('');
  const mounted = useRef(true);
  const navigationTarget = useRef<HTMLDivElement>(null);
  const formSession = useRef(0);
  const project = appliedNavigationId === requestedNavigationId && verifiedSelectedId === selectedId
    ? projects.find(item => item.id === selectedId) : undefined;
  const visibleProjects = filterWorkshopProjects(projects, query, statusFilter);
  const activeCount = projects.filter(item => !item.archived_at).length;
  const lifecycleCounts = Object.fromEntries(WORKSHOP_LIFECYCLE_STATES.map(item => [item.key,
    projects.filter(candidate => projectLifecycle(candidate).key === item.key).length]));
  const latestDeliveryState = project ? deliveryReviewState(project, project.deliverables?.at(-1)) : null;
  const runtimeUrl = canEditCanvas ? safeRuntimeUrl(status.ui_url) : null;
  const canvasUrl = canEditCanvas ? workshopCanvasUrl(status.ui_url, project?.beeftv_canvas_id || '') : null;
  const running = status.state === 'running' && !!runtimeUrl;
  const projectId = project?.id || '';
  const tab = !canEditCanvas ? 'review' : viewChoice?.projectId === projectId ? viewChoice.tab : project?.deliverables?.length ? 'review' : 'canvas';
  const setTab = (next: 'canvas' | 'review') => { if (next === 'canvas' && !canEditCanvas) return; setViewChoice({ projectId, tab: next }); };
  const canvasId = project?.beeftv_canvas_id || '';
  const canvasSessionKey = `${projectId}:${canvasId}:${frameVersion}`;
  const activeCanvas = canEditCanvas && project && canvasSession?.key === canvasSessionKey ? canvasSession : null;
  const latestArtifact = project?.deliverables?.at(-1);
  const canRecordPublication = canOperate && !!project && !project.archived_at && !!latestArtifact
    && reviewSummary(project, latestArtifact).humanApproved && !!latestDeliveryState?.canReview;

  const showForm = (next: 'create' | 'edit' | 'publish') => { if (!canOperate) return; formSession.current += 1; setForm(next); };

  const closeForm = () => {
    formSession.current += 1; setForm(null); setEditingProject(null); setMetadataError('');
    document.getElementById('workshop-create-project')?.focus();
  };

  const openProject = (id: string) => {
    formSession.current += 1;
    selectedProjectId.current = id; setSelectedId(id); setVerifiedSelectedId(''); setViewChoice(null);
    setCanvasSession(null); setExpanded(false); setForm(null); setCanvasError(''); setMetadataError('');
    void load();
  };

  const returnToList = () => {
    formSession.current += 1;
    loadSequence.current += 1;
    selectedProjectId.current = ''; setSelectedId(''); setVerifiedSelectedId(''); setViewChoice(null); setError('');
    setCanvasSession(null); setExpanded(false); setForm(null); setCanvasError(''); setMetadataError('');
  };

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; loadSequence.current += 1; }; }, []);

  useEffect(() => {
    setCanvasSession(previous => {
      if (!canEditCanvas) return null;
      // A failed health probe must not discard the editor's current DOM or unsaved input.
      if (previous?.key === canvasSessionKey) return previous;
      if (tab !== 'canvas' || !projectId || !canvasId || !running || !canvasUrl) return null;
      return { key: canvasSessionKey, url: canvasUrl };
    });
  }, [canvasSessionKey, projectId, canvasId, running, canvasUrl, tab, canEditCanvas]);

  const load = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++loadSequence.current;
    const requested = selectedProjectId.current;
    const runtimeTask = canEditCanvas ? fetchWorkshopStatus(signal)
      : Promise.resolve<WorkshopStatus>({ state: 'unavailable', message: '通过受控工作空间查看与处理视频。' });
    const listTask = canOperate && requested
      ? fetchWorkshopProjectsWithActive(requested, signal) : fetchWorkshopProjects(signal);
    // The legacy list helper may fall back to its first row or a cached detail.
    // Only this exact authorized GET can establish the selected entity.
    const detailTask = requested ? fetchWorkshopProject(requested, signal) : Promise.resolve(null);
    const [runtime, list, detail] = await Promise.allSettled([runtimeTask, listTask, detailTask]);
    if (!mounted.current || signal?.aborted || sequence !== loadSequence.current || requested !== selectedProjectId.current) return;
    if (runtime.status === 'fulfilled') setStatus(runtime.value);
    else setStatus({ state: 'unavailable', message: errorMessage(runtime.reason) });
    if (requested) {
      if (detail.status === 'fulfilled' && detail.value?.id === requested) {
        const selected = list.status === 'fulfilled' ? detail.value : { ...detail.value,
          engine_dirty: true, engine_current_revision: null,
          engine_message: '无法确认画布当前版本，请重新读取后再审阅。' };
        setProjects(previous => {
          const items = list.status === 'fulfilled' ? list.value.projects || [] : previous;
          return items.some(item => item.id === requested) ? items.map(item => item.id === requested ? selected : item) : [...items, selected];
        });
        setVerifiedSelectedId(requested);
        setError(list.status === 'rejected' ? '项目已读取，视频列表暂时无法刷新。' : '');
      } else {
        setVerifiedSelectedId('');
        setError('指定视频项目不可用，可能已删除或当前 IP 无权访问。');
        setProjects(previous => (list.status === 'fulfilled' ? list.value.projects || [] : previous).filter(item => item.id !== requested));
      }
    } else if (list.status === 'fulfilled') { setProjects(list.value.projects || []); setError(''); }
    else {
      setError(errorMessage(list.reason));
      setProjects(previous => {
        const activeId = selectedProjectId.current;
        return previous.map(item => item.id === activeId && item.beeftv_canvas_id
          ? { ...item, engine_dirty: true, engine_current_revision: null,
            engine_message: '无法确认画布当前版本，请重新读取后再审阅。' } : item);
      });
    }
    setLoading(false);
  }, [canEditCanvas, canOperate]);

  useEffect(() => {
    loadSequence.current += 1;
    formSession.current += 1;
    selectedProjectId.current = initialTarget;
    setSelectedId(initialTarget); setVerifiedSelectedId(''); setAppliedNavigationId(requestedNavigationId);
    setViewChoice(initialTarget ? { projectId: initialTarget, tab: 'review' } : null);
    setCanvasSession(null); setExpanded(false); setForm(null); setCanvasError(''); setMetadataError(''); setLoading(true);
  }, [initialTarget, requestedNavigationId]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const refresh = () => { if (document.visibilityState === 'visible') void load(controller.signal); };
    const timer = window.setInterval(refresh, 5000);
    window.addEventListener('focus', refresh);
    return () => { controller.abort(); window.clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, [load, requestedNavigationId]);

  useEffect(() => {
    if (!initialTarget || project?.id !== initialTarget) return;
    navigationTarget.current?.scrollIntoView({ block: 'start' });
    navigationTarget.current?.focus({ preventScroll: true });
  }, [initialTarget, project?.id]);

  useEffect(() => {
    if (!expanded) return;
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpanded(false); };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [expanded]);

  const update = useCallback((next: WorkshopProject) => {
    loadSequence.current += 1;
    setProjects(previous => previous.some(item => item.id === next.id)
      ? previous.map(item => item.id === next.id ? next : item) : [next, ...previous]);
    void load();
  }, [load]);

  const save = async (input: WorkshopProjectInput, canvasId: string) => {
    if (!canOperate) return;
    const session = formSession.current;
    const creating = form === 'create';
    const next = form === 'edit' && editingProject
      ? await updateWorkshopProject(editingProject.id, { expected_revision: editingProject.revision,
        title: input.title, brief: input.brief, reference_url: input.reference_url,
        aspect_ratio: input.aspect_ratio, beeftv_canvas_id: canvasId })
      : await createWorkshopProject(input);
    update(next);
    if (session !== formSession.current) return;
    closeForm();
    if (creating) openProject(next.id);
  };

  const archive = async () => {
    if (!canOperate || !project || metadataBusy) return;
    setMetadataBusy(true); setMetadataError('');
    try { update(await archiveWorkshopProject(project.id, { expected_revision: project.revision, archived: !project.archived_at })); }
    catch (cause) { setMetadataError(errorMessage(cause)); }
    finally { setMetadataBusy(false); }
  };

  const savePublication = async (event: FormEvent) => {
    event.preventDefault();
    if (!canOperate || !project || !latestArtifact || !canRecordPublication || metadataBusy) return;
    const session = formSession.current;
    setMetadataBusy(true); setMetadataError('');
    try {
      update(await recordWorkshopPublication(project.id, { expected_revision: project.revision, artifact_id: latestArtifact.id,
        platform: publication.platform.trim(), url: publication.url.trim() }));
      if (session === formSession.current) { closeForm(); setPublication({ platform: '', url: '' }); }
    } catch (cause) { setMetadataError(errorMessage(cause)); }
    finally { setMetadataBusy(false); }
  };

  const connectCanvas = async () => {
    if (!canEditCanvas || !project || connectingCanvas || !running) return;
    setConnectingCanvas(true); setCanvasError('');
    try {
      update(await connectWorkshopCanvas(project.id, project.revision));
      setFrameVersion(value => value + 1);
    } catch (cause) { setCanvasError(errorMessage(cause)); }
    finally { setConnectingCanvas(false); }
  };

  return <div className={`video-workshop ${expanded ? 'video-workshop-expanded' : ''}`}
    ref={navigationTarget} data-workshop-project-id={project?.id} tabIndex={project ? -1 : undefined}>
    <header className="workshop-head">
      <div><div className="workshop-eyebrow">EASEL VIDEO STUDIO</div>
        <h1><IconVideo size={25} />视频工坊</h1><p>{project ? 'AI 先出初稿，你来精修与定稿。' : '管理每个视频主题，跟进生产、审核与发布。'}</p></div>
      <div className="workshop-head-actions">
        <span className={`workshop-runtime ${running ? 'ok' : ''}`}><span />{!canEditCanvas ? canOperate ? '受控创作空间' : '只读空间' : running ? '创作服务已连接' : status.state === 'starting' ? '正在连接' : '创作服务未就绪'}</span>
        <button className="btn btn-sm" type="button" aria-label="刷新视频工坊" onClick={() => void load()}><IconRefresh size={14} />刷新</button>
        {canOperate && <button id="workshop-create-project" className="btn btn-sm btn-primary" type="button" onClick={() => showForm('create')}>+ 新建视频项目</button>}
        {expanded && <button className="btn btn-sm" onClick={() => setExpanded(false)}>退出大画布</button>}
      </div>
    </header>
    {error && <div className="workshop-global-error" role="alert">{error}<button type="button" onClick={() => void load()}>重新读取</button></div>}
    {!!requestedNavigationId && !initialTarget && <div className="workshop-global-error" role="alert">指定视频项目不可用，项目编号无效。</div>}
    {canOperate && form && <div className="workshop-modal-backdrop"><div className="workshop-modal" role="dialog" aria-modal="true"
      aria-label={form === 'publish' ? '记录已发布视频' : form === 'edit' ? '编辑项目' : '新建视频项目'} tabIndex={-1}
      onKeyDown={event => {
        if (event.key === 'Escape' && !metadataBusy) { event.stopPropagation(); closeForm(); }
        if (event.key !== 'Tab') return;
        const targets = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]')];
        const first = targets[0]; const last = targets.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}>
      {form === 'publish' ? <form className="workshop-project-form" onSubmit={event => void savePublication(event)}>
        <div className="workshop-section-title"><h2>记录已发布视频</h2>
          <button type="button" className="icon-btn" aria-label="关闭发布记录表单" onClick={closeForm} disabled={metadataBusy}>×</button></div>
        <p className="workshop-publication-intro">登记这份成片已经完成的发布，保留平台与作品链接。</p>
        <label><span className="field-label">发布平台</span><input className="field" required autoFocus maxLength={80} value={publication.platform}
          placeholder="例如：抖音、小红书、视频号" onChange={event => setPublication({ ...publication, platform: event.target.value })} /></label>
        <label><span className="field-label">已发布作品链接</span><input className="field" type="url" required maxLength={4096} value={publication.url}
          placeholder="https://…" onChange={event => setPublication({ ...publication, url: event.target.value })} /></label>
        <p className="workshop-hint">关联当前成片 V{latestArtifact?.revision}。这项操作只保存发布记录。</p>
        {metadataError && <p className="workshop-error" role="alert">{metadataError}</p>}
        <div className="workshop-form-actions"><button type="button" className="btn btn-sm" onClick={closeForm} disabled={metadataBusy}>取消</button>
          <button className="btn btn-sm btn-primary" disabled={metadataBusy || !canRecordPublication || !publication.platform.trim() || !publication.url.trim()}>{metadataBusy ? '正在保存…' : '保存发布记录'}</button></div>
      </form> : <ProjectForm key={form === 'edit' ? editingProject?.id : 'new'}
        project={form === 'edit' ? editingProject || undefined : undefined} onSave={save} onCancel={closeForm} />}
    </div></div>}
    {!project ? <section className="workshop-project-list-page" aria-label="视频列表">
      <div className="workshop-list-panel">
        <div className="workshop-list-toolbar"><div><h2>{statusFilter === 'archived' ? '归档视频' : '视频项目'}</h2><span>{visibleProjects.length} 个主题</span></div>
          <div className="workshop-list-filters"><input className="field" type="search" aria-label="搜索视频主题或创作要求"
            value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索主题" />
            <button type="button" className="workshop-archive-link" aria-label="查看归档视频" aria-pressed={statusFilter === 'archived'}
              onClick={() => setStatusFilter(statusFilter === 'archived' ? 'all' : 'archived')}>{statusFilter === 'archived' ? '返回列表' : '查看归档'}</button>
          </div>
        </div>
        <div className="workshop-status-filters" role="group" aria-label="视频状态筛选">
          <button type="button" className={statusFilter === 'all' ? 'selected' : ''} aria-label="筛选：全部" aria-pressed={statusFilter === 'all'} onClick={() => setStatusFilter('all')}>
            <span>全部</span>{activeCount > 0 && <span className="workshop-filter-count">{activeCount}</span>}</button>
          {WORKSHOP_LIFECYCLE_STATES.map(item => <button type="button" key={item.key} className={statusFilter === item.key ? 'selected' : ''}
            aria-label={`筛选：${item.label}`} aria-pressed={statusFilter === item.key} onClick={() => setStatusFilter(item.key)}><span>{item.label}</span>
            {lifecycleCounts[item.key] > 0 && <span className="workshop-filter-count">{lifecycleCounts[item.key]}</span>}</button>)}
        </div>
        {loading && !projects.length ? <div className="workshop-list-empty" role="status">正在读取视频项目…</div>
          : visibleProjects.length ? <div className="workshop-table-scroll"><table className="workshop-project-table" aria-label="视频项目列表">
            <thead><tr><th scope="col">视频主题</th><th scope="col">状态</th><th scope="col">规格与版本</th><th scope="col">最近更新</th><th scope="col"><span className="workshop-sr-only">操作</span></th></tr></thead>
            <tbody>{visibleProjects.map(item => {
              const lifecycle = projectLifecycle(item);
              const delivered = item.deliverables?.at(-1);
              return <tr key={item.id}>
                <td data-label="视频主题"><div className="workshop-topic-cell"><div className="workshop-topic-mark"><IconVideo size={22} /></div>
                  <div><button type="button" className="workshop-topic-title" onClick={() => openProject(item.id)}>{item.title}</button>
                    <p>{item.brief || '尚未填写创作要求'}</p></div></div></td>
                <td data-label="状态"><span className={`workshop-lifecycle-status workshop-status-${lifecycle.tone}`}><i />{lifecycle.label}</span>
                  {lifecycle.detail && <span className="workshop-list-status-detail">{lifecycle.detail}</span>}</td>
                <td data-label="规格与版本"><span className="workshop-list-spec">{item.aspect_ratio}<span>V{item.revision}</span></span>
                  <span className="workshop-list-subtext">{delivered ? `${item.deliverables.length} 版成片` : '尚未导出成片'}</span></td>
                <td data-label="最近更新"><time className="workshop-list-updated" dateTime={item.updated_at || item.created_at}>{dateLabel(item.updated_at || item.created_at || '') || '—'}</time></td>
                <td className="workshop-list-row-action"><button className="btn btn-sm" type="button" data-project-id={item.id} aria-label={`进入项目：${item.title}`} onClick={() => openProject(item.id)}>进入项目 <IconChevron size={13} /></button></td>
              </tr>;
            })}</tbody>
          </table></div> : <div className="workshop-list-empty"><IconVideo size={34} />
            <h3>{projects.length ? '没有匹配的视频主题' : '创建你的第一个视频主题'}</h3>
            <p>{projects.length ? '换个关键词，或查看其他状态的项目。' : '写下主题和创作要求，从 AI 初稿开始制作。'}</p>
            {projects.length ? <button className="btn btn-sm" onClick={() => { setQuery(''); setStatusFilter('all'); }}>清空筛选</button>
              : canOperate ? <button className="btn btn-sm btn-primary" onClick={() => showForm('create')}>新建视频项目</button> : <p>当前没有可查看的视频。</p>}
          </div>}
      </div>
    </section> : <>
      <div className="workshop-project-breadcrumb"><button className="btn btn-sm btn-ghost" type="button" onClick={returnToList}>返回视频列表</button>
        <span>/</span><strong>{project.title}</strong></div>
      <div className="workshop-project-bar">
        <div className="workshop-project-selector"><label htmlFor="workshop-project">当前项目</label>
          <select id="workshop-project" value={project.id} onChange={event => {
            selectedProjectId.current = event.target.value;
            setSelectedId(event.target.value); setVerifiedSelectedId(''); setViewChoice(null); setForm(null); setCanvasError(''); void load();
          }}>
            {projects.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}
          </select></div>
        <span className="workshop-pill">{project.aspect_ratio}</span><span className="workshop-pill">工程 V{project.revision}</span>
        <span className="workshop-pill">{project.deliverables?.length && !latestDeliveryState?.canReview
          ? latestDeliveryState?.label : STAGES[project.stage] || project.stage || '创作准备'}</span>
        {canOperate && <button className="btn btn-sm btn-ghost" onClick={() => { setEditingProject(project); showForm('edit'); }}>项目信息</button>}
        {referenceLink(project.reference_url) && <a href={referenceLink(project.reference_url)!} target="_blank" rel="noopener noreferrer" className="workshop-reference">查看参考 ↗</a>}
      </div>
      <div className="workshop-project-management"><span className={`workshop-lifecycle-status workshop-status-${projectLifecycle(project).tone}`}><i />{projectLifecycle(project).label}</span>
        <span className="workshop-management-detail">{projectLifecycle(project).detail}</span>
        <div>{canRecordPublication && <button className="btn btn-sm" type="button" onClick={() => { setPublication({ platform: '', url: '' }); setMetadataError(''); showForm('publish'); }}>记录已发布</button>}
          {canOperate && <button className="btn btn-sm btn-ghost" type="button" disabled={metadataBusy} onClick={() => void archive()}>{metadataBusy ? '正在保存…' : project.archived_at ? '恢复项目' : '归档项目'}</button>}</div>
      </div>
      {metadataError && form !== 'publish' && <p className="workshop-error" role="alert">{metadataError}</p>}
      {!!project.publications?.length && <details className="workshop-publications"><summary>发布记录 · {project.publications.length}</summary>
        {project.publications.map(item => <div key={item.id}><strong>{item.platform}</strong><span>V{item.revision} · {dateLabel(item.published_at)}</span>
          {referenceLink(item.url) && <a href={referenceLink(item.url)!} target="_blank" rel="noopener noreferrer">查看作品 ↗</a>}</div>)}
      </details>}
      <div className="workshop-mode-bar"><div className="workshop-tabs" role="tablist" aria-label="视频工坊视图"
        onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === 'Home' ? 'canvas' : event.key === 'End' ? 'review' : tab === 'canvas' ? 'review' : 'canvas';
          setTab(next); document.getElementById(`workshop-${next}-tab`)?.focus();
        }}>
        {canEditCanvas && <button role="tab" tabIndex={tab === 'canvas' ? 0 : -1} aria-selected={tab === 'canvas'} aria-controls="workshop-canvas-panel" id="workshop-canvas-tab" onClick={() => setTab('canvas')}>画布创作</button>}
        <button role="tab" tabIndex={tab === 'review' ? 0 : -1} aria-selected={tab === 'review'} aria-controls="workshop-review-panel" id="workshop-review-tab" onClick={() => setTab('review')}>交付与审阅{project.deliverables?.length ? <span>{project.deliverables.length}</span> : null}</button>
      </div><p>{project.brief || '在项目信息中写下推广对象、卖点与创作要求。'}</p></div>
      {canOperate && <ExportControls key={project.id} project={project} connected={canEditCanvas ? running : true} onUpdate={update}
        observeOnly={!!initialTarget && project.id === initialTarget} onOpenReview={() => setTab('review')} />}
      {canEditCanvas && <section id="workshop-canvas-panel" role="tabpanel" aria-labelledby="workshop-canvas-tab" hidden={tab !== 'canvas'} className="workshop-canvas">
        <div className="workshop-canvas-toolbar"><span>BeefTV{status.version ? ` · ${status.version}` : ''}</span>
          <div><button className="btn btn-sm btn-ghost" disabled={!running} onClick={() => setFrameVersion(value => value + 1)}>重新载入画布</button>
            <button className="btn btn-sm btn-ghost" onClick={() => setExpanded(value => !value)}>{expanded ? '收起大画布' : '展开大画布'}</button>
            {activeCanvas && <a className="btn btn-sm" href={activeCanvas.url} target="_blank" rel="noopener noreferrer">新窗口打开 ↗</a>}</div></div>
        {project.runtime_message && !project.beeftv_canvas_id && <p className="workshop-canvas-message" role="status">项目画布尚未关联：{project.runtime_message}</p>}
        {activeCanvas && !running && <p className="workshop-canvas-message" role="status">创作服务暂时连接失败，当前画布保持打开。连接恢复后请确认保存状态。</p>}
        {activeCanvas ? <iframe key={activeCanvas.key} src={activeCanvas.url} title="BeefTV 视频创作画布"
          allow="clipboard-read; clipboard-write; fullscreen" sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-modals" />
          : <div className="workshop-service-empty"><IconVideo size={34} /><h2>{running ? '为这个项目连接创作画布' : status.state === 'starting' ? '正在连接创作服务' : '创作服务尚未就绪'}</h2>
            <p>{running ? '创作服务已就绪。连接后，可以在项目专属画布里组织参考、分镜与生成素材。' : status.message || '请启动 BeefTV 创作服务后刷新；项目信息与已有交付仍保留。'}</p>
            {running ? <button className="btn btn-sm btn-primary" disabled={connectingCanvas} onClick={() => void connectCanvas()}>{connectingCanvas ? '正在连接画布…' : '连接项目画布'}</button>
              : <button className="btn btn-sm" onClick={() => void load()}>重新检查连接</button>}
            {canvasError && <p className="workshop-error" role="alert">{canvasError}</p>}</div>}
      </section>}
      <section id="workshop-review-panel" role="tabpanel" aria-labelledby="workshop-review-tab" hidden={tab !== 'review'} className="workshop-review-scroll">
        <ReviewPanel key={project.id} project={project} onUpdate={update} />
      </section>
    </>}
  </div>;
}
