import { useMemo } from 'react';
import { deliverablePreview, deliverablePreviewCapability, normalizeDeliverable, safeDeliverableHref, type ChatDeliverable } from '../lib/deliverables';
import ArtifactGallery from './ArtifactGallery';
import SnapshotArchivePreview from './SnapshotArchivePreview';
import DraftSnapshotPreview from './DraftSnapshotPreview';
import DeliverableVersionNotice from './DeliverableVersionNotice';

/** Resolve previews from the same authorized artifact ID as the delivery card. */
export default function DeliverablePreviewBody({ file: input }: { file: ChatDeliverable }) {
  const file = useMemo(() => normalizeDeliverable(input), [input]);
  const media = useMemo(() => {
    const artifact = file && deliverablePreview(file);
    return artifact ? [artifact] : [];
  }, [file]);
  const href = file && safeDeliverableHref(file);
  if (!file || !href || file.status !== 'available') return <p role="status">文件暂不可用。</p>;
  const capability = deliverablePreviewCapability(file);
  const viewer = capability === 'archive' ? <SnapshotArchivePreview artifact={file} />
    : capability === 'media' && media.length ? <ArtifactGallery artifacts={media} />
    : capability === 'text' ? <DraftSnapshotPreview key={file.artifactId} file={file} initialOpen />
    : <div className="deliverable-preview__fallback">
    <p>此格式暂不支持预览，可下载查看。</p>
    <a href={`${href}?download=1`} download={file.name}>下载文件</a>
  </div>;
  return <><DeliverableVersionNotice key={`version:${file.artifactId}`} file={file} />{viewer}</>;
}
