/**
 * Easel's authenticated route adapter for the MIT DeepSeek Harness deliverables
 * surface. Card markup, two-column summary and four-card expansion are migrated
 * from ui-deliverables/PresentedFileCard.tsx and Deliverables.tsx @ ce7073d.
 */
import { useMemo, useState } from 'react';
import { deliverablePreview, normalizeDeliverable, safeDeliverableHref, type ChatDeliverable } from '../lib/deliverables';
import { PresentedFileCard } from '../vendor/deepseek-harness/deliverables/PresentedFileCard';
import { PresentRow } from '../vendor/deepseek-harness/deliverables/PresentRow';
import { fileSizeText } from '../vendor/deepseek-harness/message/file-size';
import css from '../vendor/deepseek-harness/deliverables/Deliverables.module.css';
import ArtifactGallery from './ArtifactGallery';
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

export default function DeliverableCards({ artifacts }: { artifacts: ChatDeliverable[] }) {
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const files = useMemo(() => artifacts.map(normalizeDeliverable).filter((file): file is ChatDeliverable => !!file), [artifacts]);
  const selected = files.find(file => file.artifactId === selectedId);
  const preview = useMemo(() => selected ? deliverablePreview(selected) : null, [selected]);
  const previewFiles = useMemo(() => preview ? [preview] : [], [preview]);
  if (!files.length) return null;
  const collapsible = files.length > 4;
  const presented = collapsible && !expanded ? files.slice(0, 4) : files;
  const openFile = (file: ChatDeliverable) => {
    const href = safeDeliverableHref(file);
    if (!href || file.status !== 'available') return;
    if (deliverablePreview(file)) setSelectedId(file.artifactId);
    else window.open(href, '_blank', 'noopener,noreferrer');
  };
  return <section className="deliverable-section" aria-label="交付文件">
    <PresentRow phase="started" t={t} block={{ kind: 'result',
      call: { argsRaw: JSON.stringify({ files: files.map(file => ({ path: file.name })) }) },
      content: [{ type: 'text', text: `本轮声明了 ${files.length} 个交付文件。` }],
    }} />
    <div className={css.root}>
      <div className={css.presented} data-presented-files-row data-single={files.length === 1 ? true : undefined}>
        {presented.map((file, index) => {
          const href = safeDeliverableHref(file);
          const available = file.status === 'available' && !!href;
          return <div key={`${file.turnId}:${file.path}`} data-artifact-id={file.artifactId}>
            <PresentedFileCard file={{ path: file.path, description: file.description, seq: file.createdAt, index }}
              cwd={undefined} phase={available ? undefined : 'error'} host={null} t={t} disabled={!available}
              onPreview={() => openFile(file)} contextMenuProps={{}}
              actions={<div className="deliverable-actions">
                <span className="deliverable-size">{fileSizeText(file.size)}</span>
                {available ? <a className="deliverable-download" href={`${href}?download=1`}
                  download={file.name} aria-label={`下载：${file.name}`}>下载</a>
                  : <span className="deliverable-unavailable" role="status">暂不可用</span>}
              </div>} />
          </div>;
        })}
      </div>
      {collapsible && <button type="button" className={css.toggle} aria-expanded={expanded}
        onClick={() => setExpanded(value => !value)}>{expanded ? '收起' : `查看全部 ${files.length} 个文件`}</button>}
    </div>
    {previewFiles.length > 0 && <div className="deliverable-preview">
      <div className="deliverable-preview__heading"><span>{selected?.name}</span>
        <button type="button" onClick={() => setSelectedId(undefined)}>收起预览</button></div>
      <ArtifactGallery artifacts={previewFiles} />
    </div>}
  </section>;
}
