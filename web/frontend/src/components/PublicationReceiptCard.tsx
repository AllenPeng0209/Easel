/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Composition adapted from packages/client/ui-workflow-run/src/client/WorkflowRunPanel.tsx
 * @ ce7073d51254c26cd63b22a60835b8f948deb447; reuses the existing vendored DisclosureRow.
 * Easel changes: readonly publication subject/receipt and identity-pinned refresh.
 * Notice: integrations/deepseek-harness/LICENSE.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { FocusEvent, MouseEvent } from 'react';
import type { PublicationApprovalRecord } from '../lib/publicationApproval';
import {
  advancePublicationDisclosure, collapsePublicationDisclosure, initialPublicationDisclosure,
  publicationReceiptAccepts, publicationReceiptFacts, publicationReceiptIdentity,
  publicationReceiptInScope, publicationReceiptView,
} from '../lib/publicationReceipt';
import { DisclosureRow } from '../vendor/deepseek-harness/message/DisclosureRow';
import '../styles/publicationReceipt.css';

interface Props {
  record: PublicationApprovalRecord;
  onRefresh?: (id: string) => Promise<PublicationApprovalRecord>;
}
const detail = (value: unknown): string => typeof value === 'string' && value.trim()
  && value.length <= 512 && !Array.from(value).some(character => character.charCodeAt(0) < 32)
  ? value : '未提供';

/** No subject data or refresh action is rendered outside the current actor/IP. */
export default function PublicationReceiptCard(props: Props) {
  if (!publicationReceiptInScope(props.record)) return <div className="publication-receipt__restricted" role="status">
    回执不属于当前用户或 IP，未显示内容。
  </div>;
  return <ScopedPublicationReceipt key={publicationReceiptIdentity(props.record)} {...props} />;
}

function ScopedPublicationReceipt({ record, onRefresh }: Props) {
  const input = useRef(record);
  input.current = record;
  const mounted = useRef(true);
  const pending = useRef<{ input: PublicationApprovalRecord } | null>(null);
  const [busyFor, setBusyFor] = useState<PublicationApprovalRecord | null>(null);
  const [read, setRead] = useState<{ input: PublicationApprovalRecord; value: PublicationApprovalRecord } | null>(null);
  const [error, setError] = useState<{ input: PublicationApprovalRecord; message: string } | null>(null);
  const current = read?.input === record ? read.value : record;
  const status = publicationReceiptView(current);
  const facts = useMemo(() => publicationReceiptFacts(current), [current]);
  const [disclosure, setDisclosure] = useState(() => initialPublicationDisclosure(facts));
  const content = useRef<HTMLDivElement>(null);
  const busy = busyFor === record;
  const message = error?.input === record ? error.message : '';

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; pending.current = null; }; }, []);
  useLayoutEffect(() => {
    setDisclosure(previous => advancePublicationDisclosure(previous, facts,
      !!content.current?.contains(content.current.ownerDocument.activeElement)));
  }, [facts, disclosure.open]);

  const refresh = async () => {
    if (!onRefresh || input.current !== record || pending.current?.input === record) return;
    const owner = { input: record };
    const source = current;
    pending.current = owner; setBusyFor(record); setError(null);
    try {
      const next = await onRefresh(source.id);
      if (!mounted.current || pending.current !== owner || input.current !== record) return;
      if (!publicationReceiptAccepts(source, next)) throw new Error('receipt identity mismatch');
      setRead({ input: record, value: next });
    } catch {
      if (mounted.current && pending.current === owner && input.current === record) {
        setError({ input: record, message: '回执刷新未完成或与当前版本不符，请重试。' });
      }
    } finally {
      if (mounted.current && pending.current === owner) { pending.current = null; setBusyFor(null); }
    }
  };
  const settleBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (event.currentTarget.contains(event.relatedTarget)) return;
    setDisclosure(collapsePublicationDisclosure);
  };
  const preserveFocusedDetails = (event: MouseEvent<HTMLElement>) => {
    const header = event.currentTarget.querySelector('[data-disclosure-row]');
    if (header?.contains(event.target as Node)) event.preventDefault();
  };
  const result = current.result;
  return <section className="publication-receipt" data-publication-receipt={current.id}
    data-state={status.code} data-tone={status.tone} data-public-confirmed={status.publicConfirmed || undefined}
    aria-label="发布执行回执" aria-busy={busy}
    onMouseDownCapture={disclosure.pendingCleanCollapse ? preserveFocusedDetails : undefined}>
    <DisclosureRow title="发布回执" icon={<span className="publication-receipt__dot" aria-hidden="true" />}
      open={disclosure.open} expandable expandOnRowClick previewChevron={false} keepContentWhenOpen
      running={status.mode === 'running'}
      onToggle={() => setDisclosure(previous => ({ ...previous, open: !previous.open, pendingCleanCollapse: false }))}
      rowClassName="publication-receipt__header" contentClassName="publication-receipt__heading"
      contentLayoutClassName="publication-receipt__heading-content" titleClassName="publication-receipt__title"
      collapsedContent={<span className="publication-receipt__summary">
        <span>{current.subject.project.name}</span><span>{current.subject.platform.name}</span>
        <span>{current.subject.account.name}</span><span className="publication-receipt__caption">{current.subject.title || '无标题内容'}</span>
      </span>}
      headerAccessory={<div className="publication-receipt__tail"><span className="publication-receipt__status" role="status">{status.label}</span>
        {onRefresh && <button type="button" className="publication-receipt__refresh" aria-label="刷新发布回执" disabled={busy}
          onClick={() => { void refresh(); }}>{busy ? '刷新中…' : '刷新回执'}</button>}
      </div>}>
      <div className="publication-receipt__details" ref={content} onBlur={settleBlur}>
        {status.note && <p className="publication-receipt__note">{status.note}</p>}
        <dl className="publication-receipt__fields">
          <dt>IP / 平台 / 账号</dt><dd>{current.projectId} / {current.subject.platform.id} / {current.subject.account.id}</dd>
          <dt>固定版本摘要</dt><dd><code tabIndex={0}>{current.payloadHash}</code></dd>
          <dt>执行操作编号</dt><dd><code tabIndex={0}>{current.operationId || '尚未创建执行操作'}</code></dd>
          <dt>批准状态</dt><dd>{current.state}</dd>
          <dt>观测阶段</dt><dd><code tabIndex={0}>{detail(result?.phase)}</code></dd>
          <dt>观测原因</dt><dd><code tabIndex={0}>{detail(result?.reason)}</code></dd>
          <dt>平台作品 / 草稿 ID</dt><dd><code tabIndex={0}>{detail(result?.externalId)}</code></dd>
          <dt>平台可见状态</dt><dd>{detail(result?.publicationState)}</dd>
          <dt>身份核对</dt><dd>{detail(result?.identityVerification)}</dd>
          <dt>证据来源</dt><dd>{status.code === 'human_declared' ? '人工声明 · 未经平台自动验证' : detail(result?.evidenceMethod)}</dd>
        </dl>
        <div className="publication-receipt__media" aria-label="批准的固定媒体版本">
          {current.subject.media.length ? current.subject.media.map(media => <div key={media.artifactId}>
            <span>{media.name} · V{media.revision}</span><code tabIndex={0}>{media.sha256}</code>
            {media.bundleSha256 && <code tabIndex={0}>资源包：{media.bundleSha256}</code>}
          </div>) : <span>纯文本内容，无媒体附件。</span>}
        </div>
      </div>
    </DisclosureRow>
    {message && <p className="publication-receipt__error" role="alert">{message}</p>}
  </section>;
}
