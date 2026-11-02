/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from packages/client/ui-approval/src/client/ApprovalCard.tsx and
 * packages/client/ui-primitives/src/Modal.tsx @
 * ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel supplies the publication subject, Host decision callbacks and palette.
 * Bare Enter never grants a public action; Escape only dismisses the modal.
 * Original notice: integrations/deepseek-harness/LICENSE.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import { publicationActionLabel, publicationApproveLabel, publicationApprovalStatus,
  publicationCanDecide, publicationMediaPreview, publicationMediaUrl, validPublicationDigest, validPublicationSubject,
  type PublicationApprovalRecord, type PublicationApprovalSubject } from '../lib/publicationApproval';
import ArtifactGallery from './ArtifactGallery';
import '../styles/publicationApproval.css';

export interface PublicationApprovalDialogProps {
  approvals: PublicationApprovalRecord[];
  busy?: boolean;
  error?: string;
  onApprove: (id: string, payloadHash: string) => void | Promise<void>;
  onReject: (id: string, payloadHash: string) => void | Promise<void>;
  onResume?: (id: string, payloadHash: string) => void | Promise<void>;
  onClose: () => void;
}

function text(value: unknown, fallback = '未提供'): string { return typeof value === 'string' && value ? value : fallback; }
function time(value: number | string | null): string {
  const date = new Date(value ?? NaN);
  return Number.isFinite(date.getTime()) ? date.toLocaleString('zh-CN', { hour12: false }) : '时间未确认';
}

/** The same immutable subject surface is reused by publication pages and chat questions. */
export function PublicationSubjectView({ subject }: { subject: PublicationApprovalSubject }) {
  const media = useMemo(() => Array.isArray(subject.media) ? subject.media.filter(item => !!item) : [], [subject.media]);
  const previews = useMemo(() => media.flatMap(item => {
    const preview = publicationMediaPreview(subject, item);
    return preview ? [preview] : [];
  }), [subject, media]);
  const audience = ({ public: '公开', platform_draft: '仅平台草稿，尚未公开发布', manual: '人工发布包，尚未发布' } as Record<string, string>)[subject.audience] || '受众待确认';
  return <div className="publication-subject" aria-label="精确发布内容">
    <dl className="publication-subject__identity">
      <div><dt>IP</dt><dd><strong>{text(subject.project?.name)}</strong><code>IP ID：{text(subject.project?.id)}</code></dd></div>
      <div><dt>平台</dt><dd><strong>{text(subject.platform?.name)}</strong><code>平台 ID：{text(subject.platform?.id)}</code></dd></div>
      <div className="publication-subject__account"><dt>真实发布账号</dt><dd><strong>{text(subject.account?.name)}</strong><code>账号 ID：{text(subject.account?.id, '未提供真实账号 ID')}</code></dd></div>
      <div><dt>执行动作</dt><dd>{publicationActionLabel(subject)}</dd></div>
      <div><dt>受众</dt><dd>{audience}</dd></div>
      {subject.mode === 'scheduled' && <div><dt>计划时间</dt><dd>{time(subject.scheduledAt)}<code>{text(subject.scheduledAt, '未指定')}</code></dd></div>}
      <div><dt>创作画像</dt><dd>{text(subject.persona, '通用模式')}</dd></div>
    </dl>
    <section className="publication-subject__copy"><h4>标题</h4><p>{text(subject.title, '（未填写标题）')}</p>
      <h4>正文</h4><p>{text(subject.body, '（未填写正文）')}</p>
      <h4>标签原文</h4><p>{text(subject.tags, '（未填写标签）')}</p>
    </section>
    <section className="publication-subject__media"><h4>固定媒体版本</h4>
      {!media.length ? <p className="publication-approval-note">本次未附媒体文件。</p> : <ul>{media.map((item, index) => {
        const href = publicationMediaUrl(subject, item);
        return <li key={`${item.artifactId}:${index}`}>
          <div><strong>{text(item.name, '媒体文件')}</strong><span>{Number.isSafeInteger(item.revision) && item.revision > 0 ? `V${item.revision}` : '版本未确认'}</span></div>
          <dl><dt>文件 SHA-256</dt><dd><code>{validPublicationDigest(item.sha256) ? item.sha256 : '未提供有效校验值'}</code></dd>
            {item.bundleSha256 != null && <><dt>资源包 SHA-256</dt><dd><code>{validPublicationDigest(item.bundleSha256) ? item.bundleSha256 : '资源包校验值未确认'}</code></dd></>}
          </dl>
          {href ? <a href={`${href}?download=1`} download={item.name}>下载此固定版本</a>
            : <p className="publication-approval-note">不可预览：文件版本或链接无法安全验证。</p>}
        </li>;
      })}</ul>}
      {previews.length > 0 && <ArtifactGallery artifacts={previews} />}
    </section>
  </div>;
}

/** Request-keyed, one-shot waiting lifecycle copied from upstream ApprovalFlow. */
function PublicationApprovalFlow({ record, busy, error, onApprove, onReject, onResume, now }: {
  record: PublicationApprovalRecord; busy: boolean; error: string; now: number;
  onApprove: PublicationApprovalDialogProps['onApprove']; onReject: PublicationApprovalDialogProps['onReject'];
  onResume?: PublicationApprovalDialogProps['onResume'];
}) {
  const [answered, setAnswered] = useState(false);
  const [localError, setLocalError] = useState('');
  const waiting = useRef(false);
  const resuming = useRef(false);
  const active = useRef(true);
  const current = useRef({ record, busy });
  current.current = { record, busy };
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const eligible = publicationCanDecide(record, now);
  const valid = validPublicationSubject(record.subject);
  useEffect(() => {
    if (!busy && error && eligible) { waiting.current = false; setAnswered(false); }
  }, [busy, error, eligible]);
  const answer = (decision: 'approve' | 'reject'): void => {
    const latest = current.current;
    if (waiting.current || latest.busy || !active.current || !publicationCanDecide(latest.record)
        || latest.record.id !== record.id || latest.record.payloadHash !== record.payloadHash
        || decision === 'approve' && !validPublicationSubject(latest.record.subject)) return;
    waiting.current = true;
    setAnswered(true); setLocalError('');
    const send = decision === 'approve' ? onApprove : onReject;
    void Promise.resolve().then(() => send(record.id, record.payloadHash)).catch(cause => {
      if (!active.current || !publicationCanDecide(current.current.record)) return;
      waiting.current = false;
      setAnswered(false);
      setLocalError(cause instanceof Error ? cause.message : '提交失败，请重试。');
    });
  };
  const disabled = !eligible || busy || answered;
  const elapsed = record.state === 'pending' && now >= record.expiresAt;
  const awaitingHost = record.state === 'pending' && (busy || answered)
    || record.state === 'approved' && record.result?.status === 'executing';
  return <section className="publication-approval-flow" data-approval-id={record.id}
    data-approval-key={`${record.id}:${record.payloadHash}`} aria-busy={awaitingHost}>
    <div className="publication-approval-card publication-approval-with-subject">
      <div className="publication-approval-strip"><span className="publication-approval-dot" data-waiting={awaitingHost} aria-hidden="true" />
        {record.state === 'pending' && answered ? '决定已提交，等待服务端确认' : publicationApprovalStatus(record)}
      </div>
      <div className="publication-approval-body" data-approval-scroll="" tabIndex={0} role="group" aria-label="发布批准详情">
        <div className="publication-approval-subject"><strong>{text(record.subject.platform?.name)} · {text(record.subject.account?.name)}</strong></div>
        <p className="publication-approval-description">本次输入截止时间：{time(record.expiresAt)}</p>
        {elapsed && <p className="publication-approval-error" role="status">截止时间已到，请读取服务端状态或重新生成确认。</p>}
        <PublicationSubjectView subject={record.subject} />
        <details className="publication-approval-binding"><summary>确认编号与内容摘要</summary>
          <dl><dt>确认编号</dt><dd><code>{record.id}</code></dd><dt>内容 SHA-256</dt><dd><code>{record.payloadHash}</code></dd>
            <dt>申请人 ID</dt><dd><code>{record.actorId}</code></dd><dt>创建时间</dt><dd>{time(record.createdAt)}</dd>
            {record.operationId && <><dt>执行编号</dt><dd><code>{record.operationId}</code></dd></>}
          </dl>
        </details>
        {!valid && record.state === 'pending' && <p className="publication-approval-error">确认内容或固定媒体信息不完整，不能批准执行。</p>}
        {localError && <p className="publication-approval-error" role="alert">{localError}</p>}
      </div>
      {record.state === 'pending' && <div className="publication-approval-action-row">
        <button type="button" className="btn btn-secondary publication-approval-reject" data-decision="reject"
          disabled={disabled} onClick={() => answer('reject')}>拒绝此版本</button>
        <button type="button" className="btn btn-primary" data-decision="approve"
          disabled={disabled || !valid} onClick={() => answer('approve')}>{publicationApproveLabel(record.subject)}</button>
      </div>}
      {record.state === 'approved' && !record.result && onResume && <div className="publication-approval-action-row">
        <button type="button" className="btn btn-secondary" disabled={busy || !valid} onClick={() => {
          if (resuming.current || busy || !active.current) return;
          resuming.current = true; setLocalError('');
          void Promise.resolve().then(() => onResume(record.id, record.payloadHash))
            .catch(cause => { if (active.current) setLocalError(cause instanceof Error ? cause.message : '核对失败，请重试'); })
            .finally(() => { resuming.current = false; });
        }}>核对并继续此批准版本</button>
      </div>}
    </div>
  </section>;
}

/** Body portal and focus lifetime adapted from the upstream Modal. */
function PublicationModal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const dialog = useRef<HTMLDivElement>(null);
  useModalLayer(dialog, true, onClose);
  return createPortal(<div className="publication-modal-overlay" role="presentation">
    <div className="publication-modal-mask" aria-hidden="true" onClick={onClose} />
    <div ref={dialog} tabIndex={-1} className="publication-modal-dialog" role="dialog" aria-modal="true"
      aria-label="核对并批准发布版本" data-shortcut-modal="publication-approval">
      <header className="publication-modal-header"><h2>核对并批准发布版本</h2>
        <button type="button" className="publication-modal-close" aria-label="关闭发布确认" data-modal-autofocus onClick={onClose}><span aria-hidden="true">×</span></button>
      </header>
      <p className="publication-modal-description">每个平台独立确认。未确认的操作不会自动批准。</p>
      <div className="publication-modal-body">{children}</div>
    </div>
  </div>, document.body);
}

export default function PublicationApprovalDialog({ approvals, busy = false, error = '', onApprove, onReject, onResume, onClose }: PublicationApprovalDialogProps) {
  const [now, setNow] = useState(Date.now());
  const hasPending = approvals.some(record => record.state === 'pending');
  useEffect(() => {
    if (!hasPending) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasPending]);
  if (!approvals.length) return null;
  return <PublicationModal onClose={onClose}>
    {error && <p className="publication-approval-error" role="alert">{error}</p>}
    {approvals.map(record => <PublicationApprovalFlow key={`${record.id}:${record.payloadHash}`} record={record}
      busy={busy} error={error} onApprove={onApprove} onReject={onReject} onResume={onResume} now={now} />)}
  </PublicationModal>;
}
