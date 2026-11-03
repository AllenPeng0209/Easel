import { useEffect, useMemo, useRef, useState } from 'react';
import { draftSnapshotHref, normalizeDeliverableVersions, type ChatDeliverable } from '../lib/deliverables';
import { projectFetch } from '../lib/projectScope';
import { inspectTerminalText } from '../lib/toolValueInspection';
import { projectUnifiedDiff, type ToolDiffProjection } from '../vendor/deepseek-harness/tool/file-diff';
import { FileDiff } from '../vendor/deepseek-harness/tool/FileDiff';
import css from './FixedVersionComparison.module.css';

type ComparisonState = { kind: 'loading' } | { kind: 'error'; message: string }
  | { kind: 'ready'; identical: boolean; projection: ToolDiffProjection;
    before: ChatDeliverable; after: ChatDeliverable };

function matchesVersion(value: unknown, file: ChatDeliverable): boolean {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  return item.artifactId === file.artifactId && item.revision === file.revision && item.sha256 === file.sha256;
}

function readComparison(value: unknown, before: ChatDeliverable, after: ChatDeliverable): ComparisonState {
  if (!value || typeof value !== 'object') return { kind: 'error', message: '版本比较回执无效。' };
  const data = value as Record<string, unknown>;
  const identical = before.sha256 === after.sha256;
  if (data.path !== before.path || !matchesVersion(data.before, before) || !matchesVersion(data.after, after)
      || typeof data.diff !== 'string' || data.identical !== identical || (identical && data.diff !== '')) {
    return { kind: 'error', message: '比较回执和所选固定版本不匹配，请刷新历史版本后重试。' };
  }
  const safe = inspectTerminalText(data.diff);
  const projection: ToolDiffProjection = safe.truncated
    ? { kind: 'raw', reason: 'truncated', text: safe.text } : projectUnifiedDiff(safe.text);
  return { kind: 'ready', identical, projection, before, after };
}

const errorLabels: Record<number, string> = {
  404: '其中一个固定版本已不可用，请刷新历史版本。',
  409: '无法核验所选固定版本，请刷新历史版本。',
  413: '所选固定版本超过文本比较的大小限制。',
  415: '所选版本无法作为 UTF-8 文本比较。',
};

export default function FixedVersionComparison({ anchor, versions }: { anchor: ChatDeliverable; versions: ChatDeliverable[] }) {
  const files = useMemo(() => normalizeDeliverableVersions(anchor, versions)
    .filter(file => draftSnapshotHref(file) !== null).reverse(), [anchor, versions]);
  const anchorIndex = files.findIndex(file => file.artifactId === anchor.artifactId);
  const initialBefore = files[Math.max(0, anchorIndex - 1)]?.artifactId ?? '';
  const initialAfter = files[Math.max(1, anchorIndex)]?.artifactId ?? '';
  const [beforeId, setBeforeId] = useState(initialBefore);
  const [afterId, setAfterId] = useState(initialAfter);
  const [result, setResult] = useState<ComparisonState>();
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  if (!draftSnapshotHref(anchor) || files.length < 2) return null;
  const before = files.find(file => file.artifactId === beforeId);
  const after = files.find(file => file.artifactId === afterId);
  const afterHref = after && draftSnapshotHref(after);
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
      const value: ComparisonState = response.ok ? readComparison(await response.json(), before, after)
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
      data-fixed-version-comparison data-before-artifact={ready.before.artifactId} data-after-artifact={ready.after.artifactId}>
      <div className={css.versions}>
        <VersionIdentity file={ready.before} label="起始" />
        <VersionIdentity file={ready.after} label="目标" />
      </div>
      {ready.identical ? <p role="status">两个固定版本的文件字节完全相同。</p>
        : ready.projection.kind === 'missing' ? <p role="status">文件字节不同，未提供可展示的文本差异行。</p>
          : <><p>差异来自所选固定版本，展示时会隐藏敏感内容。</p><FileDiff projection={ready.projection} source="snapshot" /></>}
    </section>}
  </div>;
}

function VersionIdentity({ file, label }: { file: ChatDeliverable; label: string }) {
  const href = draftSnapshotHref(file);
  return <div className={css.version}>
    <strong>{label}版本 V{file.revision}</strong>
    <span>文件 SHA-256 <code>{file.sha256}</code></span>
    {href && <a href={href + '?download=1'} download={file.name}>下载比较{label}版本 V{file.revision}</a>}
  </div>;
}
