import { useEffect, useRef } from 'react';
import { workshopMediaUrl } from '../lib/workshop';
import type { WorkshopAsset, WorkshopProject } from '../lib/workshop';

const labels: Record<string, string> = {
  character: '角色', scene: '场景', prop: '道具', image: '图片', video: '视频',
  audio: '音轨', subtitle: '字幕', document: '制作文件',
};

function Material({ name, kind, path, description, current }: {
  name: string; kind: string; path?: string; description?: string; current?: boolean;
}) {
  const url = workshopMediaUrl(path || '');
  return <article className="workshop-material-card">
    <div className="workshop-material-title"><h3>{name}</h3><span>{current ? '当前选片' : labels[kind] || '素材'}</span></div>
    {url ? <>
      {kind === 'video' && <video src={url} controls preload="none" aria-label={name} />}
      {kind === 'audio' && <audio src={url} controls preload="none" aria-label={name} />}
      {['character', 'scene', 'prop', 'image'].includes(kind) && <img src={url} alt={name} loading="lazy" />}
      <a className="btn btn-sm" href={url} download>下载{name}</a>
    </> : <p className="workshop-hint">尚未生成文件</p>}
    {description && <p className="workshop-material-description">{description}</p>}
  </article>;
}

function Asset({ asset }: { asset: WorkshopAsset }) {
  const history = (asset.versions || []).filter(version => version.path !== asset.path);
  return <div><Material {...asset} />{history.length > 0 && <details className="workshop-material-history"><summary>历史版本 · {history.length}</summary>
    {[...history].reverse().map((version, index) => {
      const url = workshopMediaUrl(version.path);
      return url && <a key={version.path} href={url} download>{asset.name} · 历史版本 {history.length - index}</a>;
    })}
  </details>}</div>;
}

export default function WorkshopMaterialsPanel({ project, onClose }: { project: WorkshopProject; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    close.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); }
      if (event.key === 'Tab') {
        const targets = close.current?.closest('[role="dialog"]')?.querySelectorAll<HTMLElement>('button, a[href], video[controls], audio[controls], summary');
        const first = targets?.[0], last = targets?.[targets.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, [onClose]);
  return <div className="huobao-materials-overlay" role="dialog" aria-modal="true" aria-label="二修素材与制作文件">
    <header className="workshop-section-title"><div><h2>{project.title} · 二修素材</h2>
      <p>保留各版素材，下载后可继续剪辑、配音和调整字幕。</p></div><button ref={close} className="btn btn-sm" onClick={onClose}>关闭素材</button></header>
    <section aria-label="制作文件与参考素材"><h2>制作文件与参考素材 <small>{project.assets.length}</small></h2>
      {project.assets.length ? <div className="workshop-material-grid">{project.assets.map(asset => <Asset key={asset.id} asset={asset} />)}</div>
        : <p className="workshop-hint">AI 登记角色、净版、音轨、字幕和制作文件后，会显示在这里。</p>}
    </section>
    <section aria-label="镜头候选"><h2>镜头候选 <small>{project.shots.reduce((total, shot) => total + shot.candidates.length, 0)}</small></h2>
      {!project.shots.length && <p className="workshop-hint">生成并收取镜头后，可在这里查看原片和返修历史。</p>}
      {project.shots.map((shot, index) => <section className="workshop-material-shot" key={shot.id}>
        <h3>{String(index + 1).padStart(2, '0')} · {shot.title}</h3>
        {shot.candidates.length ? <div className="workshop-material-grid">{shot.candidates.map((candidate, candidateIndex) =>
          <Material key={candidate.id} name={`${shot.title} · 候选 ${candidateIndex + 1}`} kind="video" path={candidate.path}
            current={candidate.id === shot.selected_candidate_id} />)}</div> : <p className="workshop-hint">尚未生成候选</p>}
      </section>)}
    </section>
    <section aria-label="成片版本"><h2>成片版本 <small>{project.deliverables.length}</small></h2>
      {project.deliverables.length ? <div className="workshop-material-grid">{[...project.deliverables].reverse().map(artifact =>
        <Material key={artifact.id} name={`成片 V${artifact.revision}`} kind="video" path={artifact.path} />)}</div>
        : <p className="workshop-hint">完整成片交付后，可在这里预览和下载。</p>}
    </section>
  </div>;
}
