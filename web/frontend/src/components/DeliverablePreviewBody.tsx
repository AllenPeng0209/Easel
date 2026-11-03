import { useMemo } from 'react';
import { deliverablePreview, draftSnapshotHref, normalizeDeliverable, safeDeliverableHref, type ChatDeliverable } from '../lib/deliverables';
import ArtifactGallery from './ArtifactGallery';
import SnapshotArchivePreview from './SnapshotArchivePreview';
import DraftSnapshotPreview from './DraftSnapshotPreview';

/** Resolve previews from the same authorized artifact ID as the delivery card. */
export default function DeliverablePreviewBody({ file: input }: { file: ChatDeliverable }) {
  const file = useMemo(() => normalizeDeliverable(input), [input]);
  const media = useMemo(() => {
    const artifact = file && deliverablePreview(file);
    return artifact ? [artifact] : [];
  }, [file]);
  const href = file && safeDeliverableHref(file);
  if (!file || !href || file.status !== 'available') return <p role="status">文件暂不可用。</p>;
  if (file.storageMode === 'snapshot' && /\.zip$/i.test(file.path)) return <SnapshotArchivePreview artifact={file} />;
  if (media.length) return <ArtifactGallery artifacts={media} />;
  if (draftSnapshotHref(file)) return <DraftSnapshotPreview key={file.artifactId} file={file} initialOpen />;
  return <div className="deliverable-preview__fallback">
    <p>此格式可下载查看。</p>
    <a href={`${href}?download=1`} download={file.name}>下载文件</a>
  </div>;
}
