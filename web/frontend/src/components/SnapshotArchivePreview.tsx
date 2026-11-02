import { useEffect, useState } from 'react';
import type { AgentArtifact } from '../lib/agentEvents';
import { loadSnapshotArchive, snapshotArchiveContentUrl, type SnapshotArchiveFile } from '../lib/snapshotArchive';
import ArtifactGallery from './ArtifactGallery';

/** The selected delivery's ZIP is indexed by immutable ID, never its current path. */
export default function SnapshotArchivePreview({ artifact }: { artifact: AgentArtifact }) {
  const [state, setState] = useState<{ key: string; files?: SnapshotArchiveFile[]; error?: string }>();
  const { artifactId, url, storageMode, status } = artifact;
  const key = `${artifactId}:${url}:${storageMode}:${status}`;
  let download: string | undefined;
  try { download = snapshotArchiveContentUrl(artifact); } catch { /* Invalid metadata cannot supply a download URL. */ }
  useEffect(() => {
    const controller = new AbortController();
    setState({ key });
    void loadSnapshotArchive({ artifactId, url, storageMode, status }, controller.signal).then(files => {
      if (!controller.signal.aborted) setState({ key, files });
    }).catch(error => {
      const message = error && typeof error === 'object' && typeof error.message === 'string' ? error.message : 'ZIP 索引读取失败';
      if (!controller.signal.aborted) setState({ key, error: message });
    });
    return () => controller.abort();
  }, [artifactId, url, storageMode, status, key]);
  const current = state?.key === key ? state : undefined;
  const previews = current?.files?.flatMap(file => file.preview ? [file.preview] : []) || [];
  return <section className="snapshot-archive-preview" aria-label={`ZIP 内容：${artifact.name}`}>
    <div className="artifact-footer"><span>{artifact.name}</span>
      {download && <a href={`${download}?download=1`} download={artifact.name}>下载原始 ZIP</a>}</div>
    {current?.error ? <p className="artifact-hint" role="alert">{current.error}</p>
      : !current?.files ? <p className="artifact-hint" role="status">正在读取 ZIP 内容…</p>
        : <>
          {previews.length > 0 && <ArtifactGallery key={key} artifacts={previews} />}
          {current.files.length === 0 ? <p className="artifact-hint">ZIP 中没有可读取的文件</p>
            : <ul aria-label="ZIP 文件列表">{current.files.map(file => <li key={file.index} data-archive-member={file.index}>
              <span title={file.path}>{file.path}</span>{' '}
              <a href={`${file.url}?download=1`} download={file.name}>下载</a>
            </li>)}</ul>}
        </>}
  </section>;
}
