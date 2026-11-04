import { useEffect, useState } from 'react';
import type { AgentArtifact } from '../lib/agentEvents';
import { loadSnapshotArchive, snapshotArchiveContentUrl, type SnapshotArchiveFile } from '../lib/snapshotArchive';
import ArtifactGallery from './ArtifactGallery';

type ArchiveState = {
  key: string;
  phase: 'loading' | 'ready' | 'error' | 'unavailable';
  files?: SnapshotArchiveFile[];
  error?: string;
};

/** Immutable ZIP observation, adapted from ui-sidebar-documentpreview reload policy (MIT, ce7073d). */
export default function SnapshotArchivePreview({ artifact }: { artifact: AgentArtifact }) {
  const [state, setState] = useState<ArchiveState>();
  const [retry, setRetry] = useState<{ key: string; attempt: number }>();
  const { artifactId, url, storageMode, status } = artifact;
  const key = JSON.stringify([artifactId, url, storageMode, status]);
  const attempt = retry?.key === key ? retry.attempt : 0;
  let download: string | undefined;
  try { download = snapshotArchiveContentUrl(artifact); } catch { /* Invalid metadata cannot supply a download URL. */ }
  useEffect(() => {
    if (!download) return;
    const controller = new AbortController();
    setState(previous => ({ key, phase: 'loading', files: previous?.key === key ? previous.files : undefined }));
    void loadSnapshotArchive({ artifactId, url, storageMode, status }, controller.signal).then(files => {
      if (!controller.signal.aborted) setState({ key, phase: 'ready', files });
    }).catch(error => {
      if (controller.signal.aborted) return;
      const unavailable = [401, 403, 404, 410].includes(error?.status);
      const message = error?.message === 'ZIP CRC 校验失败' ? error.message : 'ZIP 索引读取失败。';
      setState(previous => ({ key, phase: unavailable ? 'unavailable' : 'error', error: message,
        files: !unavailable && previous?.key === key ? previous.files : undefined }));
    });
    return () => controller.abort();
  }, [artifactId, url, storageMode, status, key, download, attempt]);
  const current = state?.key === key ? state : undefined;
  const phase = !download ? 'unavailable' : current?.phase ?? 'loading';
  const files = current?.files;
  const previews = files?.flatMap(file => file.preview ? [file.preview] : []) || [];
  const version = artifact.revision ? `固定版本 V${artifact.revision}` : '固定版本';
  return <section className="snapshot-archive-preview" aria-label={`ZIP 内容：${artifact.name}`}>
    <div className="artifact-footer"><span>{artifact.name}</span>
      {download && <a href={`${download}?download=1`} download={artifact.name}>下载原始 ZIP</a>}</div>
    <div className="artifact-recovery" data-snapshot-archive-status={phase}>
      {phase === 'loading' && <span role="status">{files ? `正在重新读取${version} ZIP，保留上次索引。` : '正在读取 ZIP 内容…'}</span>}
      {phase === 'ready' && <span role="status">{attempt ? `已重新读取${version} ZIP。` : `${version} ZIP 索引可用。`}</span>}
      {phase === 'error' && <span role="alert">{current?.error}{files && `保留上次索引（${version}）。`}</span>}
      {phase === 'unavailable' && <span role="alert">此固定 ZIP 暂不可用。</span>}
      {download && <button type="button" className="btn btn-sm" data-snapshot-archive-retry disabled={phase === 'loading'}
        onClick={() => setRetry({ key, attempt: attempt + 1 })}>{phase === 'ready' ? '重新读取索引' : '重试读取索引'}</button>}
    </div>
    {files && <>
      {previews.length > 0 && <ArtifactGallery key={key} artifacts={previews} />}
      {files.length === 0 ? <p className="artifact-hint">ZIP 中没有可读取的文件</p>
        : <ul aria-label="ZIP 文件列表">{files.map(file => <li key={file.index} data-archive-member={file.index}>
          <span title={file.path}>{file.path}</span>{' '}
          <a href={`${file.url}?download=1`} download={file.name}>下载</a>
        </li>)}</ul>}
    </>}
  </section>;
}
