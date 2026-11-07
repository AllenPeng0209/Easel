import { useState } from 'react';
import type { FormEvent } from 'react';
import { useIpPermissions } from './ProjectSpace';
import { addWorkshopReview, deliveryReviewState, reviewSummary, prepareWorkshopPublication, publicationResultLabel, workshopMediaUrl } from '../lib/workshop';
import type { WorkshopProject, WorkshopReference } from '../lib/workshop';
type PanelProps = { project: WorkshopProject; onUpdate: (project: WorkshopProject) => void; onUseWorkshop?: (reference: WorkshopReference) => void | Promise<void> };
const message = (error: unknown) => error instanceof Error ? error.message : '操作未完成，请刷新后重试。';
export default function WorkshopReviewPanel({ project, onUpdate, onUseWorkshop, initialArtifactId }: PanelProps & { initialArtifactId?: string }) {
  const { canOperate } = useIpPermissions();
  const [selected, setSelected] = useState(initialArtifactId || ''), [notes, setNotes] = useState('');
  const [editingPublication, setEditingPublication] = useState(false);
  const [checked, setChecked] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [publishing, setPublishing] = useState({ platform: 'douyin', title: project.title, body: '', tags: '', aiGenerated: true });
  const artifact = selected ? project.deliverables.find(item => item.id === selected) : project.deliverables.at(-1);
  const eligibility = deliveryReviewState(project, artifact);
  const summary = reviewSummary(project, artifact);
  const preparation = project.publication_preparation;
  const bound = !!preparation && !!artifact && preparation.revision === project.revision && preparation.artifact_id === artifact.id;
  const token = artifact ? `${project.revision}:${artifact.id}:${artifact.sha256}:${preparation?.payload_hash || ''}` : '';
  const latestRun = project.publication_runs?.at(-1);
  const boundRun = project.publication_runs?.find(row => row.approval_id === preparation?.approval_id);
  const reviewLocked = !!boundRun?.result && ['executing', 'submitted', 'confirmed', 'unknown'].includes(boundRun.result.status);
  const review = async (decision: 'approved' | 'changes_requested') => {
    if (!artifact || checked !== token || busy || decision === 'approved' && (editingPublication || reviewLocked)) return;
    setBusy(true); setError('');
    try {
      const next = await addWorkshopReview(project.id, { expected_revision: project.revision, artifact_id: artifact.id,
        kind: 'human', decision, notes, ...(bound && decision === 'approved' ? {
          publication_approval_id: preparation.approval_id, publication_payload_hash: preparation.payload_hash } : {}) });
      onUpdate(next);
      if (decision === 'changes_requested' && onUseWorkshop) await onUseWorkshop({ id: next.id, revision: next.revision, artifactId: artifact.id, artifactSha256: artifact.sha256 });
      setChecked('');
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  };
  const prepare = async (event: FormEvent) => {
    event.preventDefault(); if (!artifact) return;
    setBusy(true); setError('');
    try { onUpdate((await prepareWorkshopPublication(project, artifact.id, publishing)).project); setChecked(''); setEditingPublication(false); }
    catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  };
  return <div className="workshop-review-layout"><section className="workshop-panel workshop-delivery"><div className="workshop-section-title"><h2>成片预览</h2><span>{summary.label}</span></div>
    {!!project.deliverables.length && <label><span className="field-label">交付版本</span><select className="field" value={artifact?.id || ''} onChange={event => { setSelected(event.target.value); setChecked(''); }}>
      {[...project.deliverables].reverse().map(item => <option key={item.id} value={item.id}>V{item.revision} · {item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : '成片'}</option>)}</select></label>}
    {artifact ? <><video className="workshop-player" key={artifact.id} src={workshopMediaUrl(artifact.path, true) || undefined} controls preload="metadata" />
      <div className="workshop-delivery-foot"><span>工程 V{artifact.revision}</span><a className="btn btn-sm" href={workshopMediaUrl(artifact.path) || undefined} download>下载成片</a>
        {canOperate && onUseWorkshop && <button className="btn btn-sm" onClick={() => void onUseWorkshop({ id: project.id, revision: project.revision, artifactId: artifact.id, artifactSha256: artifact.sha256 })}>带回对话继续修改</button>}</div>
      {!eligibility.canReview && <p className="workshop-hint">{eligibility.message}</p>}</> : <p className="workshop-hint">AI 完成制作后，成片会自动进入这里等待审核。</p>}
    <h3>审核与修改记录</h3>{project.reviews.map(row => <p key={row.id}><strong>V{row.revision} · {row.kind === 'human' ? '人工审核' : '模拟检查'} · {row.decision === 'approved' ? '通过' : '要求修改'}</strong><br />{row.notes}</p>)}
    {project.comments?.map(row => <p key={row.id}>{row.at_seconds.toFixed(1)} 秒 · {row.notes}</p>)}
  </section><div className="workshop-review-side">
    {bound && <section className="workshop-panel native-publication-subject"><h2>本次发布信息</h2><p><strong>{preparation.subject.platform.name} · {preparation.subject.account.name}</strong></p>
      <h3>{preparation.subject.title}</h3><p className="native-preserve-lines">{preparation.subject.body}</p><p>{preparation.subject.tags}</p>
      <p className="workshop-hint">{preparation.subject.aiGenerated ? '将声明 AI 生成内容。' : ''}审核通过后自动提交到此账号。</p>
      {canOperate && !reviewLocked && <button className="btn btn-sm" disabled={busy} onClick={() => {
        setPublishing({ platform: preparation.subject.platform.id, title: preparation.subject.title,
          body: preparation.subject.body, tags: preparation.subject.tags, aiGenerated: preparation.subject.aiGenerated !== false });
        setEditingPublication(true); setChecked('');
      }}>重新核对发布信息</button>}</section>}
    {canOperate && artifact && eligibility.canReview && (!bound || editingPublication) && <form className="workshop-panel" onSubmit={prepare}><h2>准备发布信息</h2>
      <p className="workshop-hint">AI 可以在交付时准备这些信息；也可以在这里补充，核对账号后一次审核并发布。</p>
      <label><span className="field-label">平台</span><select className="field" value={publishing.platform} onChange={event => setPublishing({ ...publishing, platform: event.target.value })}>
        <option value="douyin">抖音</option><option value="xiaohongshu">小红书</option><option value="bilibili">哔哩哔哩</option><option value="channels">视频号</option><option value="kuaishou">快手</option></select></label>
      <label><span className="field-label">标题</span><input className="field" value={publishing.title} required maxLength={1000} onChange={event => setPublishing({ ...publishing, title: event.target.value })} /></label>
      <label><span className="field-label">文案</span><textarea className="field" rows={3} value={publishing.body} onChange={event => setPublishing({ ...publishing, body: event.target.value })} /></label>
      <label><span className="field-label">话题</span><input className="field" value={publishing.tags} onChange={event => setPublishing({ ...publishing, tags: event.target.value })} /></label>
      <button className="btn btn-sm" disabled={busy}>核对账号并准备发布</button></form>}
    {latestRun?.result && <section className="workshop-panel" role="status"><h2>{publicationResultLabel(latestRun.result.status)}</h2><p>{latestRun.result.message}</p>
      {latestRun.result.url && <a href={latestRun.result.url} target="_blank" rel="noreferrer">查看平台作品</a>}</section>}
    {canOperate && <section className="workshop-panel"><h2>审核当前成片</h2><label><span className="field-label">审核意见</span><textarea className="field" rows={3} value={notes} onChange={event => setNotes(event.target.value)} placeholder="需要修改时，写清位置与要求。" /></label>
      <label className="workshop-check"><input type="checkbox" disabled={!eligibility.canReview} checked={!!token && checked === token} onChange={event => setChecked(event.target.checked ? token : '')} />
        <span>{bound ? '我已核对成片、文案和目标账号，批准发布这一版本' : '我已核对当前成片的画面、声音与字幕'}</span></label>
      <div className="workshop-review-actions"><button className="btn btn-sm" disabled={busy || !token || checked !== token || !notes.trim()} onClick={() => void review('changes_requested')}>要求修改</button>
        <button className="btn btn-sm btn-primary" disabled={busy || !token || checked !== token || !eligibility.canReview || editingPublication || reviewLocked} onClick={() => void review('approved')}>{reviewLocked ? '已审核并提交' : bound ? '审核通过并发布' : '记录审核通过'}</button></div>
      <p className="workshop-hint">内容或发布信息变化后，需要重新核对这一版本。</p></section>}
    {error && <p className="workshop-error" role="alert">{error}</p>}
  </div></div>;
}
