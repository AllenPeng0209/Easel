/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Business-owned keyed view composition adapted from deepseek-ai/deepseek-harness
 * packages/client/ui-tool/src/client/tool/ToolCallTree.tsx, src/client/apply.ts
 * and packages/client/ui-tool/src/client/tool/components/ToolRow.tsx
 * @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: existing Host DTO projections; no business-service or transcript ownership.
 * Notice: integrations/deepseek-harness/LICENSE.
 */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentType } from 'react';
import type { BusinessObject, BusinessObjectKind } from '../lib/businessObjects';
import { businessObjectCanRefresh, businessObjectDraftArtifact, businessObjectEventKey, businessObjectIdentity,
  businessObjectOpenHref, normalizeBusinessObject } from '../lib/businessObjects';
import { DisclosureRow } from '../vendor/deepseek-harness/message/DisclosureRow';
import DeliverableCards from './DeliverableCards';
import DraftSnapshotPreview from './DraftSnapshotPreview';
import { useIpPermissions } from './ProjectSpace';
import { IconIdea, IconOutputs, IconVideo } from './icons';
import '../styles/businessObjects.css';

interface Props {
  record: BusinessObject;
  onRefresh?: (kind: BusinessObjectKind, id: string) => Promise<BusinessObject>;
}
const KINDS = { idea: '选题', draft: '固定稿件', workshop: '视频工坊' };
const SOURCES = { stored: '已保存状态', computed: '按服务端观测推导', unknown: '状态来源未知' };
const ICONS: Record<BusinessObjectKind, ComponentType<{ size?: number }>> = { idea: IconIdea, draft: IconOutputs, workshop: IconVideo };
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const valueText = (value: unknown): string => typeof value === 'string' ? value : typeof value === 'number' || typeof value === 'boolean' ? String(value) : '未记录';
const entries = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.filter(isRecord) : [];
const revision = (value: unknown) => typeof value === 'number' ? `V${value}` : '版本未记录';
const VERIFICATION: { key: string; title: string; labels: Record<string, string> }[] = [
  { key: 'engineVerification', title: '引擎核验', labels: { not_checked: '引擎未核验' } },
  { key: 'publishEligibility', title: '发布条件', labels: { unverified: '发布条件待核对' } },
  { key: 'deliveryVerification', title: '成片核验', labels: { not_available: '没有可核验成片', stale_revision: '成片属于历史版本',
    current_file_verified: '当前成片文件摘要已核对', unavailable_or_changed: '成片缺失或文件摘要已变化' } },
  { key: 'publicationEvidence', title: '发布证据', labels: { stored_records_only: '仅保存登记记录，非平台自动验证' } },
  { key: 'canvasBinding', title: '画布归属', labels: { verified: '归属已核对', not_linked: '未关联', unverified: '归属待核对' } },
];

function IdeaDetails({ record }: { record: BusinessObject }) {
  const idea = isRecord(record.details.idea) ? record.details.idea : record.details;
  return <dl className="business-object__fields"><dt>选题备注</dt><dd>{valueText(idea.note)}</dd>
    <dt>来源记录</dt><dd>{valueText(idea.source)}</dd>
    {typeof idea.platform === 'string' && <><dt>来源平台</dt><dd>{idea.platform}</dd></>}
    {typeof idea.url === 'string' && !!idea.url && <><dt>来源链接</dt><dd>{idea.url}</dd></>}
    {typeof idea.collectedAt === 'number' && <><dt>采集时间</dt><dd>{new Date(idea.collectedAt * 1000).toLocaleString('zh-CN', { hour12: false })}</dd></>}
    <dt>保存状态</dt><dd>{valueText(idea.status)}</dd>
    <dt>创建记录</dt><dd>{valueText(idea.created ?? idea.createdAt)}</dd></dl>;
}
function DraftDetails({ record }: { record: BusinessObject }) {
  const file = businessObjectDraftArtifact(record);
  if (!file) return <p className="business-object__note">{record.version.revision === null
    ? '未固定历史版本，不能改用当前文件预览。' : '固定快照不可验证，未加载文件预览。'}</p>;
  return <>
    <DraftSnapshotPreview key={businessObjectEventKey(record)} file={file} />
    <DeliverableCards key={`${file.artifactId}:${file.revision}:${file.sha256}`} artifacts={[file]} />
  </>;
}
function VersionRelation({ current, observed }: { current: number | null; observed: unknown }) {
  return typeof observed === 'number' && current !== null && observed < current
    ? <small className="business-object__historical">历史版本 · {revision(observed)}</small> : <small>{revision(observed)}</small>;
}
function WorkshopDetails({ record }: { record: BusinessObject }) {
  const project = isRecord(record.details.project) ? record.details.project : {};
  const handoff = isRecord(record.details.handoff) ? record.details.handoff : null;
  const deliverables = entries(project.deliverables), renders = entries(project.renders), reviews = entries(project.reviews), publications = entries(project.publications);
  return <div className="business-object__workshop">
    <dl className="business-object__fields"><dt>工程 ID</dt><dd>{valueText(project.id)}</dd>
      <dt>保存工程版本</dt><dd>{revision(project.revision)}</dd><dt>保存阶段</dt><dd>{valueText(project.stage)}</dd>
      {handoff && typeof handoff.engineRevision === 'number' && <><dt>本轮画布版本</dt><dd>{revision(handoff.engineRevision)}</dd></>}
      {handoff && typeof handoff.artifactId === 'string' && <>
        <dt>本轮引用成片</dt><dd><code>{handoff.artifactId}</code> · {revision(handoff.artifactRevision)}</dd>
        <dt>成片校验值</dt><dd><code>{valueText(handoff.artifactSha256)}</code></dd>
      </>}
      {!!project.archived_at && <><dt>归档记录</dt><dd>历史工程 · {valueText(project.archived_at)}</dd></>}
      {VERIFICATION.map(({ key, title, labels }) => {
        const value = record.details[key];
        return typeof value === 'string' ? <Fragment key={key}><dt>{title}</dt><dd>{Object.hasOwn(labels, value) ? labels[value] : value}</dd></Fragment> : null;
      })}</dl>
    <section><h4>成片记录 · {deliverables.length}</h4>{deliverables.length ? <ul>{deliverables.map((item, index) => <li key={String(item.id ?? index)}>
      <strong>{valueText(item.id)}</strong><VersionRelation current={record.version.revision} observed={item.revision} />
      <code>{valueText(item.sha256)}</code></li>)}</ul> : <p>没有保存成片记录。</p>}</section>
    <section><h4>渲染记录 · {renders.length}</h4>{renders.length ? <ul>{renders.map((item, index) => <li key={String(item.id ?? index)}>
      <strong>{valueText(item.id)}</strong><span>{valueText(item.status)}</span>
      {typeof item.progress === 'number' && item.progress >= 0 && item.progress <= 1 && <span>{Math.round(item.progress * 100)}%</span>}
      <VersionRelation current={record.version.revision} observed={item.content_revision} />
      <span>引擎版本：{valueText(item.engine_revision)}</span>
      {typeof item.operation_id === 'string' && <span>操作编号：<code>{item.operation_id}</code></span>}
      {typeof item.task_id === 'string' && <span>渲染任务：<code>{item.task_id}</code></span>}
      {typeof item.submission_state === 'string' && <span>提交记录：{item.submission_state}</span>}
      {typeof item.artifact_id === 'string' && <span>对应成片：<code>{item.artifact_id}</code></span>}
      </li>)}</ul> : <p>没有保存渲染回执。</p>}</section>
    <section><h4>审核记录 · {reviews.length}</h4>{reviews.length ? <ul>{reviews.map((item, index) => {
      const actor = item.actorId ?? item.actor_id ?? item.reviewerId ?? item.reviewer_id ?? item.reviewer
        ?? (isRecord(item.actor) ? item.actor.display_name ?? item.actor.name ?? item.actor.id : undefined);
      return <li key={String(item.id ?? index)}><strong>{valueText(item.id)}</strong>
        <span>{item.kind === 'simulated' ? '模拟预审（不代表人工批准）' : item.kind === 'human' ? '人工审核记录' : '审核来源未记录'}</span>
        <span>{valueText(item.decision)}</span><VersionRelation current={record.version.revision} observed={item.revision} />
        <span>{actor === undefined ? '审核人未记录' : `审核人：${valueText(actor)}`}</span>
        <code>{valueText(item.artifact_id)}</code><code>{valueText(item.artifact_sha256)}</code>
        {typeof item.notes === 'string' && <p>{item.notes}</p>}</li>;
    })}</ul> : <p>没有保存审核记录。</p>}</section>
    <section><h4>发布登记 · {publications.length}</h4><p>人工发布登记，非平台自动验证。</p>
      {!!publications.length && <ul>{publications.map((item, index) => <li key={String(item.id ?? index)}><strong>{valueText(item.id)}</strong>
        <span>{valueText(item.platform)}</span><VersionRelation current={record.version.revision} observed={item.revision} />
        <code>{valueText(item.artifact_id)}</code><span>{valueText(item.published_at ?? item.created_at)}</span></li>)}</ul>}</section>
  </div>;
}
// The wire name is dispatched by the Host-owned panel. Only this business leaf
// selects its exact DTO kind, following ui-tool's keyed atomic view rule.
const DETAILS: Record<BusinessObjectKind, ComponentType<{ record: BusinessObject }>> = {
  idea: IdeaDetails, draft: DraftDetails, workshop: WorkshopDetails,
};

/** A malformed/foreign DTO never falls back to a plaintext dump of its subject. */
export default function BusinessObjectCard({ record, onRefresh }: Props) {
  const normalized = useMemo(() => normalizeBusinessObject(record), [record]);
  if (!normalized) return <p className="business-object__unavailable" role="status">对象信息不可用，或不属于当前用户或 IP。</p>;
  return <ScopedBusinessObject key={businessObjectIdentity(normalized)} record={normalized} onRefresh={onRefresh} />;
}

function ScopedBusinessObject({ record, onRefresh }: Props) {
  const { canOperate } = useIpPermissions();
  const input = useRef(record); input.current = record;
  const mounted = useRef(true);
  const pending = useRef<{ input: BusinessObject } | null>(null);
  const [open, setOpen] = useState(record.status.source === 'unknown');
  const [busyFor, setBusyFor] = useState<BusinessObject | null>(null);
  const [read, setRead] = useState<{ input: BusinessObject; value: BusinessObject } | null>(null);
  const [failure, setFailure] = useState<{ input: BusinessObject; missing: boolean; message: string } | null>(null);
  const current = read?.input === record ? read.value : record;
  const error = failure?.input === record ? failure : null;
  const busy = busyFor === record;
  const canUseIdea = canOperate && current.kind === 'idea' && current.status.code !== 'unavailable' && !error?.missing;
  const useIdea = () => {
    if (!canUseIdea || !mounted.current || input.current !== record) return;
    const { id, projectId, actorId } = current;
    window.dispatchEvent(new window.CustomEvent('easel:use-idea', { detail: { id, projectId, actorId } }));
  };
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; pending.current = null; }; }, []);
  const refresh = async () => {
    if (!onRefresh || input.current !== record || pending.current?.input === record) return;
    const owner = { input: record }; const source = current;
    pending.current = owner; setBusyFor(record); setFailure(null);
    try {
      const next = normalizeBusinessObject(await onRefresh(source.kind, source.id));
      if (!mounted.current || input.current !== record || pending.current !== owner) return;
      if (!next || !businessObjectCanRefresh(source, next)) throw new Error('invalid observation');
      setRead({ input: record, value: next });
    } catch (error) {
      if (!mounted.current || input.current !== record || pending.current !== owner) return;
      const missing = !!error && typeof error === 'object' && 'status' in error && (error.status === 404 || error.status === 403);
      setFailure({ input: record, missing, message: missing
        ? '原对象已删除或不可访问，保留此历史观测，无法刷新。'
        : '当前无法刷新或未取得更新观测，已保留上次快照，可重试。' });
    } finally {
      if (mounted.current && pending.current === owner) { pending.current = null; setBusyFor(null); }
    }
  };
  const Icon = ICONS[current.kind]; const Details = DETAILS[current.kind];
  const observedAt = new Date(current.observedAt);
  const versionHashLabel = current.kind === 'workshop' ? '最新成片' : '版本';
  const versionLabel = current.kind === 'idea' ? '无正式稿件版本'
    : current.version.revision === null ? current.kind === 'draft' ? '历史元数据 · 未固定版本' : '版本未记录' : revision(current.version.revision);
  return <section className="business-object" data-business-object={current.id} data-kind={current.kind}
    data-status={current.status.code} data-status-source={current.status.source} data-observed-at={current.observedAt}
    aria-label={`${KINDS[current.kind]}对象观测`} aria-busy={busy}>
    <DisclosureRow title={KINDS[current.kind]} icon={<Icon size={14} />} open={open} expandable expandOnRowClick keepContentWhenOpen
      onToggle={() => setOpen(value => !value)} rowClassName="business-object__header"
      contentClassName="business-object__heading" contentLayoutClassName="business-object__heading-content"
      collapsedContent={<span className="business-object__title">{current.title || '未命名对象'}</span>}
      headerAccessory={<span className="business-object__tail"><span role="status">{current.status.label}</span>
        {onRefresh && <button type="button" className="business-object__refresh" aria-label="刷新业务对象" disabled={busy}
          onClick={() => { void refresh(); }}>{busy ? '刷新中…' : '刷新对象'}</button>}</span>}>
      <div className="business-object__details">
        {current.summary && <p className="business-object__summary">{current.summary}</p>}
        <dl className="business-object__fields"><dt>对象身份</dt><dd>{current.projectId} / {current.kind} / {current.id}</dd>
          <dt>观测摘要{current.kind === 'idea' ? '（非稿件版本）' : ''}</dt><dd><code tabIndex={0}>{current.snapshotHash}</code></dd>
          <dt>业务版本</dt><dd>{versionLabel}</dd><dt>{versionHashLabel} SHA-256</dt><dd><code tabIndex={0}>{current.version.sha256 || '未记录摘要'}</code></dd>
          <dt>状态来源</dt><dd>{SOURCES[current.status.source]} · {current.status.code}</dd></dl>
        <Details record={current} />
      </div>
    </DisclosureRow>
    <div className="business-object__facts"><code>{current.kind} / {current.id}</code><span>{versionLabel}</span>
      <span>{SOURCES[current.status.source]} · <code>{current.status.code}</code></span>
      {current.version.sha256 && <span>{versionHashLabel}摘要 <code title={current.version.sha256}>{current.version.sha256.slice(0, 12)}…</code></span>}
      <span>观测摘要 <code title={current.snapshotHash}>{current.snapshotHash.slice(0, 12)}…</code></span>
      <time dateTime={observedAt.toISOString()} title={String(current.observedAt)}>观测于 {observedAt.toLocaleString('zh-CN', { hour12: false })}</time>
    </div>
    {error && <p className="business-object__error" role="alert">{error.message}</p>}
    {!error?.missing && <div className="business-object__actions">{current.actions.map(action => {
      const href = businessObjectOpenHref(current, action);
      return href ? <a key={`${action.target}:${action.entityId}`} href={href}>{action.label}</a> : null;
    })}{canUseIdea && <button type="button" className="btn btn-sm" onClick={useIdea}>用此选题创作</button>}</div>}
  </section>;
}
