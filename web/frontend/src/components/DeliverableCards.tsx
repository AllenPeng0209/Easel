/**
 * 响芽's authenticated route adapter for the MIT DeepSeek Harness deliverables
 * surface. Card markup, two-column summary and four-card expansion are migrated
 * from ui-deliverables/PresentedFileCard.tsx and Deliverables.tsx @ ce7073d.
 */
import { useEffect, useId, useMemo, useState, useSyncExternalStore } from 'react';
import type { MouseEvent } from 'react';
import { deliverablePreviewCapability, deliverableTypeLabel, deliverableVersionsUrl, normalizeDeliverable, normalizeDeliverableVersions,
  safeDeliverableHref, type ChatDeliverable } from '../lib/deliverables';
import { PresentedFileCard } from '../vendor/deepseek-harness/deliverables/PresentedFileCard';
import { PresentRow } from '../vendor/deepseek-harness/deliverables/PresentRow';
import { fileSizeText } from '../vendor/deepseek-harness/message/file-size';
import css from '../vendor/deepseek-harness/deliverables/Deliverables.module.css';
import DeliverablePreviewBody from './DeliverablePreviewBody';
import FixedVersionComparison from './FixedVersionComparison';
import { HostReadStore } from '../vendor/deepseek-harness/deliverables/HostReadStore';
import reviewCss from '../vendor/deepseek-harness/deliverables/ReviewTab.module.css';
import { publicationMediaReference, requestDeliverablePublication } from '../lib/publicationHandoff';
import '../styles/deliverables.css';

const labels: Record<string, string> = {
  'presented.file': '文件', 'presented.preview': '查看文件', 'presented.error': '文件暂不可用',
  'presented.opening': '正在打开', 'row.title': '交付文件', 'row.ok': '已交付',
  'row.running': '正在准备', 'row.error': '交付失败', 'row.stopped': '已停止',
  'row.preparing': '正在准备交付文件', 'row.inspect': '查看详情',
};

function t(key: string, params?: Record<string, string | number>): string {
  return key === 'presented.previewCard' ? `查看文件：${params?.name || ''}` : labels[key] || key;
}

type VersionHistory = 'loading' | 'error' | 'unavailable' | { versions: ChatDeliverable[] };

/** The upstream read cache supplies request deduplication, retry and cancellation. */
class ArtifactVersionStore extends HostReadStore<VersionHistory> {
  private readonly anchor: ChatDeliverable;
  constructor(anchor: ChatDeliverable) {
    super({ loading: 'loading', failed: 'error', retryable: state => state === 'error' || state === 'unavailable',
      decode: async response => {
        if ([401, 403, 404, 410].includes(response.status)) return 'unavailable';
        if (!response.ok) return 'error';
        const data: unknown = await response.json();
        if (!data || typeof data !== 'object' || !('versions' in data) || !Array.isArray(data.versions)) return 'error';
        return { versions: normalizeDeliverableVersions(anchor, data.versions) };
      },
    });
    this.anchor = anchor;
  }
  load() { const url = deliverableVersionsUrl(this.anchor); return url ? this.loadUrl(url) : Promise.resolve(); }
}

function DeliverableVersionDetails({ file }: { file: ChatDeliverable }) {
  const snapshot = file.storageMode === 'snapshot';
  const [open, setOpen] = useState(false);
  const [retained, setRetained] = useState<ChatDeliverable[]>();
  const [store] = useState(() => new ArtifactVersionStore(file));
  const states = useSyncExternalStore(store.state.subscribe, store.state.getSnapshot, store.state.getSnapshot);
  const url = deliverableVersionsUrl(file);
  const history = url ? states[url] : undefined;
  useEffect(() => { if (open && history === undefined) void store.load(); }, [open, history, store]);
  // Reset the request generation on unmount; unlike terminal dispose this also
  // permits React StrictMode's setup/cleanup/setup sequence safely.
  useEffect(() => () => store.reset(), [store]);
  useEffect(() => { if (history === 'unavailable') setRetained(undefined); }, [history]);
  const current = history && typeof history === 'object' ? history.versions : undefined;
  const previous = (history === 'loading' || history === 'error') ? retained : undefined;
  const versions = current ?? previous ?? [];
  const latest = versions.reduce((value, item) => Math.max(value, item.revision || 0), 0);
  return <div className="deliverable-version" data-storage-mode={file.storageMode}>
    <div className="deliverable-version__heading">
      <span>{snapshot ? `本卡交付 V${file.revision}` : '旧交付 · 当前文件，未固定历史'}</span>
      <span className="deliverable-review-state">{snapshot ? '未审核' : '未关联审核'}</span>
    </div>
    {snapshot && <details className="deliverable-version__details">
      <summary>版本与校验信息</summary>
      <dl><dt>文件 SHA-256</dt><dd><code>{file.sha256}</code></dd>
        {file.bundleSha256 && <><dt>资源包 SHA-256</dt><dd><code>{file.bundleSha256}</code></dd></>}
      </dl><p>未关联人工批准记录。</p>
    </details>}
    <button type="button" className={reviewCss.selectorButton} aria-expanded={open}
      aria-label={`查看历史交付版本：${file.name}`} onClick={() => setOpen(value => !value)}>历史版本</button>
    {open && <div className="deliverable-history">
      {history === 'loading' && <p role="status">正在读取历史交付…</p>}
      {(history === 'error' || history === 'unavailable') && <p role="alert">
        {history === 'error' ? '历史版本读取失败。' : '历史版本暂不可用。'}
        <button type="button" onClick={() => void store.load()}>重试</button>
      </p>}
      {previous && <p role="status">保留上次成功读取的交付记录。</p>}
      {(current || previous) && <>
        <p className="deliverable-history__note">按本次查询列出交付记录；本卡预览和下载不会自动切换。</p>
        <ul>{versions.map(item => {
          const href = safeDeliverableHref(item);
          return <li key={item.artifactId} className={reviewCss.item} data-history-artifact={item.artifactId}>
            <span className={reviewCss.itemPath}>{item.storageMode === 'snapshot' ? `V${item.revision}` : '旧交付 · 当前文件，未固定历史'}
              {item.artifactId === file.artifactId && ' · 本卡交付版本'}
              {item.storageMode === 'snapshot' && item.revision === latest && ' · 该路径最新交付'}
              <small>{item.storageMode === 'snapshot' ? '未审核' : '未关联审核'}</small>
            </span>
            {href && item.status === 'available' ? <a href={`${href}?download=1`} download={item.name}>下载此交付</a> : <span>内容不可用</span>}
          </li>;
        })}</ul>
        <FixedVersionComparison anchor={file} versions={versions} />
        {current && <button type="button" onClick={() => {
          setRetained(current);
          store.reset();
          void store.load();
        }}>刷新历史列表</button>}
      </>}
    </div>}
  </div>;
}

export default function DeliverableCards({ artifacts, onOpenDeliverable }: {
  artifacts: ChatDeliverable[];
  onOpenDeliverable?: (file: ChatDeliverable, trigger: HTMLElement) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const detailsId = useId();
  const files = useMemo(() => artifacts.map(normalizeDeliverable).filter((file): file is ChatDeliverable => !!file), [artifacts]);
  const selected = files.find(file => file.artifactId === selectedId);
  if (!files.length) return null;
  const hasOverview = files.length > 1;
  const presented = files.map(file => {
    const href = safeDeliverableHref(file);
    return { file, href, available: file.status === 'available' && !!href,
      previewAvailable: !!deliverablePreviewCapability(file), publishAvailable: !!publicationMediaReference(file) };
  });
  const openFile = (file: ChatDeliverable, trigger?: HTMLElement) => {
    if (!deliverablePreviewCapability(file)) return;
    if (onOpenDeliverable && trigger) onOpenDeliverable(file, trigger);
    else setSelectedId(file.artifactId);
  };
  return <section className="deliverable-section" aria-label="交付文件">
    <PresentRow phase="started" t={t} block={{ kind: 'result',
      call: { argsRaw: JSON.stringify({ files: files.map(file => ({ path: file.name })) }) },
      content: [{ type: 'text', text: `本轮声明了 ${files.length} 个交付文件。` }],
    }} />
    <div className={css.root}>
      {hasOverview && <ul className="deliverable-overview" data-deliverable-overview aria-label="本轮交付物总览">
        {presented.map(({ file, href, available, previewAvailable, publishAvailable }) =>
          <li key={file.artifactId} className="deliverable-overview__row" data-deliverable-overview-id={file.artifactId}>
            <div className="deliverable-overview__file">
              {previewAvailable ? <button type="button" className="deliverable-overview__preview"
                data-deliverable-overview-preview aria-label={`预览交付：${file.name}`}
                title={file.name} onClick={event => openFile(file, event.currentTarget)}>{file.name}</button>
                : <strong title={file.name}>{file.name}</strong>}
              <span className="deliverable-overview__purpose">{file.description || '未提供用途说明'}</span>
              <span className="deliverable-meta">
                <span>{deliverableTypeLabel(file)}</span>
                <span title={`${file.size.toLocaleString('zh-CN')} 字节`}>{fileSizeText(file.size)}</span>
                {available && !previewAvailable && <span>不支持预览</span>}
              </span>
            </div>
            <div className="deliverable-actions">
              {publishAvailable && <button type="button" className="btn btn-secondary"
                onClick={() => { requestDeliverablePublication(file); }}>用于发布</button>}
              {available ? <a className="deliverable-download" href={`${href}?download=1`}
                download={file.name} aria-label={`下载：${file.name}`}>下载</a>
                : <span className="deliverable-unavailable">暂不可用</span>}
            </div>
          </li>)}
      </ul>}
      {hasOverview && <button type="button" className={css.toggle} data-deliverable-details-toggle
        aria-expanded={expanded} aria-controls={detailsId}
        onClick={() => setExpanded(value => !value)}>{expanded ? '收起交付详情' : '展开交付详情与版本'}</button>}
      <div id={detailsId} className="deliverable-detail-cards" hidden={hasOverview && !expanded} data-deliverable-detail-cards>
        <div className={css.presented} data-presented-files-row data-single={!hasOverview || undefined}>
          {presented.map(({ file, href, available, previewAvailable, publishAvailable }, index) =>
            <div key={file.artifactId} data-artifact-id={file.artifactId}>
              <PresentedFileCard file={{ path: file.path, description: file.description, seq: file.createdAt, index }}
                cwd={undefined} phase={available ? undefined : 'error'} host={null} t={t} disabled={!available}
                previewAvailable={previewAvailable} onPreview={(event?: MouseEvent<HTMLButtonElement>) => openFile(file, event?.currentTarget)}
                contextMenuProps={{}} metadata={<span className="deliverable-meta">
                  <span data-deliverable-type>{deliverableTypeLabel(file)}</span>
                  <span className="deliverable-size" title={`${file.size.toLocaleString('zh-CN')} 字节`}>{fileSizeText(file.size)}</span>
                  {available && !previewAvailable && <span data-deliverable-download-only>不支持预览</span>}
                </span>}
                actions={<div className="deliverable-actions">
                  {publishAvailable && <button type="button" className="btn btn-secondary"
                    onClick={() => { requestDeliverablePublication(file); }}>用于发布</button>}
                  {available ? <a className="deliverable-download" href={`${href}?download=1`}
                    download={file.name} aria-label={`下载：${file.name}`}>下载</a>
                    : <span className="deliverable-unavailable" role="status">暂不可用</span>}
                </div>} />
              <DeliverableVersionDetails file={file} />
            </div>)}
        </div>
      </div>
    </div>
    {selected && <div className="deliverable-preview">
      <div className="deliverable-preview__heading"><span>{selected?.name}{selected?.storageMode === 'snapshot' ? ` · 交付 V${selected.revision}` : ''}</span>
        <button type="button" onClick={() => setSelectedId(undefined)}>收起预览</button></div>
      <DeliverablePreviewBody key={selected.artifactId} file={selected} />
    </div>}
  </section>;
}
