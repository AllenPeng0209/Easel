import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { deliverablePreview, draftSnapshotHref, normalizeDeliverableVersions, safeDeliverableHref, type ChatDeliverable } from '../lib/deliverables';
import { projectFetch } from '../lib/projectScope';
import { inspectTerminalText } from '../lib/toolValueInspection';
import { projectUnifiedDiff, type ToolDiffProjection } from '../vendor/deepseek-harness/tool/file-diff';
import SnapshotTextDiff from './SnapshotTextDiff';
import css from './FixedVersionComparison.module.css';

type MediaMode = 'image' | 'video';
type ComparisonMode = 'text' | MediaMode;

type Generation = {
  workshopProjectId: string;
  renderId: string;
  taskId: string;
  contentRevision: number;
};

type BoundPair = { identical: boolean; before: ChatDeliverable; after: ChatDeliverable };

type ComparisonState = { kind: 'loading' } | { kind: 'error'; message: string }
  | (BoundPair & { kind: 'ready'; mode: 'text'; projection: ToolDiffProjection })
  | (BoundPair & { kind: 'ready'; mode: MediaMode; beforeGeneration: Generation | null; afterGeneration: Generation | null });

function comparisonMode(file: ChatDeliverable): ComparisonMode | undefined {
  if (file.storageMode !== 'snapshot' || file.status !== 'available' || !safeDeliverableHref(file)) return undefined;
  const kind = deliverablePreview(file)?.kind;
  if (kind === 'image' || kind === 'video') return file.mime.toLowerCase().startsWith(kind + '/') ? kind : undefined;
  return draftSnapshotHref(file) ? 'text' : undefined;
}

function readGeneration(value: unknown): Generation | null {
  if (!value || typeof value !== 'object') return null;
  const { source, workshopProjectId, renderId, taskId, contentRevision } = value as Record<string, unknown>;
  if (source !== 'native_workshop'
      || typeof workshopProjectId !== 'string' || !/^[a-f0-9]{32}$/.test(workshopProjectId)
      || typeof renderId !== 'string' || !/^[a-f0-9]{32}$/.test(renderId)
      || typeof taskId !== 'string' || !/^[A-Za-z0-9:_-]{1,160}$/.test(taskId)
      || typeof contentRevision !== 'number' || !Number.isSafeInteger(contentRevision) || contentRevision < 0) return null;
  return { workshopProjectId, renderId, taskId, contentRevision };
}

function matchesVersion(value: unknown, file: ChatDeliverable): boolean {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return item.artifactId === file.artifactId && item.revision === file.revision && item.sha256 === file.sha256;
}

function readComparison(value: unknown, before: ChatDeliverable, after: ChatDeliverable, mode: ComparisonMode): ComparisonState {
  if (!value || typeof value !== 'object') return { kind: 'error', message: '版本比较回执无效。' };
  const data = value as Record<string, unknown>;
  const identical = before.sha256 === after.sha256;
  const mismatch = { kind: 'error', message: '比较回执和所选固定版本不匹配，请刷新历史版本后重试。' } as const;
  if (data.path !== before.path || !matchesVersion(data.before, before) || !matchesVersion(data.after, after)
      || data.identical !== identical) return mismatch;

  if (mode !== 'text') {
    const left = data.before as Record<string, unknown>;
    const right = data.after as Record<string, unknown>;
    const mediaMatches = (item: Record<string, unknown>, file: ChatDeliverable) =>
      item.mime === file.mime && item.size === file.size && (item.url === file.url || item.url === safeDeliverableHref(file));
    if (data.kind !== 'media' || !mediaMatches(left, before) || !mediaMatches(right, after)) return mismatch;
    return { kind: 'ready', mode, identical, before, after, beforeGeneration: readGeneration(left.generation),
      afterGeneration: readGeneration(right.generation) };
  }

  if ((data.kind !== undefined && data.kind !== 'text') || typeof data.diff !== 'string' || (identical && data.diff !== '')) return mismatch;
  const safe = inspectTerminalText(data.diff);
  const projection: ToolDiffProjection = safe.truncated
    ? { kind: 'raw', reason: 'truncated', text: safe.text } : projectUnifiedDiff(safe.text);
  return { kind: 'ready', mode, identical, projection, before, after };
}

const errorLabels: Record<number, string> = {
  404: '其中一个固定版本已不可用，请刷新历史版本。',
  409: '无法核验所选固定版本，请刷新历史版本。',
  413: '所选固定版本超过文本比较的大小限制。',
  415: '所选版本没有可用的固定版本比较格式。',
};

export default function FixedVersionComparison({ anchor, versions }: { anchor: ChatDeliverable; versions: ChatDeliverable[] }) {
  const mode = comparisonMode(anchor);
  const files = useMemo(() => normalizeDeliverableVersions(anchor, versions)
    .filter(file => mode && comparisonMode(file) === mode).reverse(), [anchor, versions, mode]);
  const anchorIndex = files.findIndex(file => file.artifactId === anchor.artifactId);
  const initialBefore = files[Math.max(0, anchorIndex - 1)]?.artifactId ?? '';
  const initialAfter = files[Math.max(1, anchorIndex)]?.artifactId ?? '';
  const [beforeId, setBeforeId] = useState(initialBefore);
  const [afterId, setAfterId] = useState(initialAfter);
  const [result, setResult] = useState<ComparisonState>();
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  if (!mode || files.length < 2) return null;
  const before = files.find(file => file.artifactId === beforeId);
  const after = files.find(file => file.artifactId === afterId);
  const afterHref = after && safeDeliverableHref(after);
  const canCompare = !!before && !!afterHref && beforeId !== afterId;
  const choose = (side: 'before' | 'after', id: string) => {
    request.current?.abort();
    request.current = null;
    setResult(undefined);
    if (side === 'before') setBeforeId(id);
    else setAfterId(id);
  };
  const compare = async () => {
    if (!canCompare || !before || !after || !afterHref || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setResult({ kind: 'loading' });
    try {
      const url = afterHref.replace(/\/content$/, '/compare') + '?beforeId=' + encodeURIComponent(before.artifactId);
      const response = await projectFetch(url, { signal: controller.signal, cache: 'no-store' });
      const value: ComparisonState = response.ok ? readComparison(await response.json(), before, after, mode)
        : { kind: 'error', message: errorLabels[response.status] || '固定版本比较失败，请重试。' };
      if (!controller.signal.aborted && request.current === controller) setResult(value);
    } catch {
      if (!controller.signal.aborted && request.current === controller) {
        setResult({ kind: 'error', message: '固定版本比较失败，请重试。' });
      }
    } finally {
      if (request.current === controller) request.current = null;
    }
  };
  const ready = result?.kind === 'ready' && before && after &&
    matchesVersion(result.before, before) && matchesVersion(result.after, after) ? result : undefined;
  const options = () => files.map(file => <option value={file.artifactId} key={file.artifactId}>V{file.revision}</option>);

  return <div className={css.compare} role="group" aria-label="固定版本比较">
    <div className={css.controls}>
      <label>起始版本 <select aria-label="比较起始版本" value={beforeId}
        onChange={event => choose('before', event.target.value)}>{options()}</select></label>
      <label>目标版本 <select aria-label="比较目标版本" value={afterId}
        onChange={event => choose('after', event.target.value)}>{options()}</select></label>
      <button type="button" onClick={() => void compare()} disabled={!canCompare || result?.kind === 'loading'}>比较固定版本</button>
    </div>
    {beforeId === afterId && <p>请选择两个不同的固定版本。</p>}
    {result?.kind === 'loading' && <p role="status">正在比较固定版本…</p>}
    {result?.kind === 'error' && <p role="alert">{result.message}</p>}
    {ready && <section className={css.result}
      data-fixed-version-comparison data-fixed-comparison-mode={ready.mode} data-before-artifact={ready.before.artifactId} data-after-artifact={ready.after.artifactId}>
      <div className={css.versions}>
        <VersionIdentity file={ready.before} label="起始">
          {ready.mode !== 'text' && <FixedMedia key={ready.before.artifactId} file={ready.before} mode={ready.mode}
            side="起始" generation={ready.beforeGeneration} />}
        </VersionIdentity>
        <VersionIdentity file={ready.after} label="目标">
          {ready.mode !== 'text' && <FixedMedia key={ready.after.artifactId} file={ready.after} mode={ready.mode}
            side="目标" generation={ready.afterGeneration} />}
        </VersionIdentity>
      </div>
      {ready.identical ? <p role="status">两个固定版本的文件字节完全相同。</p>
        : ready.mode !== 'text' ? <p>预览和下载均使用所选固定版本。</p>
          : ready.projection.kind === 'missing' ? <p role="status">文件字节不同，未提供可展示的文本差异行。</p>
            : <><p>差异来自所选固定版本，展示时会隐藏敏感内容。</p><SnapshotTextDiff projection={ready.projection} /></>}
    </section>}
  </div>;
}

function VersionIdentity({ file, label, children }: { file: ChatDeliverable; label: string; children?: ReactNode }) {
  const href = safeDeliverableHref(file);
  return <div className={css.version}>
    <strong>{label}版本 V{file.revision}</strong>
    <span>文件 SHA-256 <code>{file.sha256}</code></span>
    {href && <a href={href + '?download=1'} download={file.name}>下载比较{label}版本 V{file.revision}</a>}
    {children}
  </div>;
}

function FixedMedia({ file, mode, side, generation }: {
  file: ChatDeliverable; mode: MediaMode; side: string; generation: Generation | null;
}) {
  const [failed, setFailed] = useState(false);
  const href = safeDeliverableHref(file);
  const label = `${side}版本 V${file.revision} · ${file.name}`;
  return <>
    {href && !failed
      ? mode === 'image' ? <img className={css.media} src={href} alt={label} loading="lazy" onError={() => setFailed(true)} />
        : <video className={css.media} src={href} aria-label={label} controls playsInline preload="metadata"
          onError={() => setFailed(true)} />
      : <p role="status">该固定版本暂无法预览，可下载查看。</p>}
    {generation ? <dl className={css.generation} data-fixed-media-generation>
      <dt>来源</dt><dd>原生工坊合成回执</dd>
      <dt>工程版本</dt><dd>V{generation.contentRevision}</dd>
      <dt>工坊工程</dt><dd>{generation.workshopProjectId}</dd>
      <dt>渲染回执</dt><dd>{generation.renderId}</dd>
      <dt>任务编号</dt><dd>{generation.taskId}</dd>
      <dt>其他生成参数</dt><dd>未记录</dd>
    </dl> : <p data-fixed-media-generation-unknown>未记录生成参数。</p>}
  </>;
}
