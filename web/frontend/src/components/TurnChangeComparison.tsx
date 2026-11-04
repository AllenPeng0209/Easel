import { useEffect, useState } from 'react';
import { projectFetch } from '../lib/projectScope';
import { normalizeTurnChangeComparison, type TurnChangeTarget } from '../lib/turnChangeComparison';
import SnapshotTextDiff from './SnapshotTextDiff';

type Result = ReturnType<typeof normalizeTurnChangeComparison>;
const limits = {
  binary: '二进制文件没有文本差异。',
  oversized: '固定文本差异超过展示上限。',
  unavailable: '此轮未保留可比较的文本快照。',
};

export default function TurnChangeComparison({ sessionId, turnId, path, kind, onClose }: TurnChangeTarget & {
  onClose: () => void;
}) {
  const [result, setResult] = useState<Result | undefined>();
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setResult(undefined);
    const url = `/api/chat/turn-changes/${encodeURIComponent(turnId)}/compare?path=${encodeURIComponent(path)}`;
    void projectFetch(url, { cache: 'no-store', signal: controller.signal }).then(async response =>
      response.ok ? normalizeTurnChangeComparison(await response.json(), { sessionId, turnId, path, kind }) : null,
    ).then(value => {
      if (!controller.signal.aborted) setResult(value);
    }).catch(() => {
      if (!controller.signal.aborted) setResult(null);
    });
    return () => controller.abort();
  }, [sessionId, turnId, path, kind, retry]);

  return <section className="turn-change-comparison" aria-label="本轮固定差异" data-turn-change-comparison
    onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); }
    }}>
    <header><strong>本轮固定差异 · {path}</strong><button type="button" className="btn btn-sm" onClick={onClose}>关闭比较</button></header>
    {result === undefined && <p role="status">正在读取固定快照…</p>}
    {result === null && <p role="alert">无法核验这次比较的固定快照。
      <button type="button" className="btn btn-sm" onClick={() => setRetry(value => value + 1)}>重试</button>
    </p>}
    {result?.kind === 'limited' && <p role="status">{limits[result.reason]}</p>}
    {result?.kind === 'text' && <>
      {result.redacted && <p>差异中的敏感值已隐藏，校验值仍对应原始快照。</p>}
      <dl>
        <dt>回合开始</dt><dd>{result.before ? <code title={result.before.sha256}>{result.before.sha256}</code> : '文件尚不存在'}</dd>
        <dt>回合结束</dt><dd>{result.after ? <code title={result.after.sha256}>{result.after.sha256}</code> : '文件已删除'}</dd>
      </dl>
      {result.projection.kind === 'missing' ? <p role="status">文件字节有变化，未产生可展示的文本差异行。</p>
        : <SnapshotTextDiff projection={result.projection} />}
    </>}
  </section>;
}
