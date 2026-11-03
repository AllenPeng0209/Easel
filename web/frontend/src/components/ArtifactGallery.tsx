import { useCallback, useEffect, useRef, useState } from 'react';
import { artifactFromUrl, fitArtifact, mergeArtifacts } from '../lib/artifacts';
import { loadPackedHtmlArtifact } from '../lib/htmlPreview';
import { fetchArtifactArchive, mediaUrl } from '../lib/api';
import type { Artifact } from '../lib/artifacts';
import { projectFetch, projectUrl } from '../lib/projectScope';
import { IconChevron, IconOutputs } from './icons';
import '../styles/artifactRecovery.css';

type HtmlState = {
  key: string;
  phase: 'loading' | 'ready' | 'error' | 'unavailable';
  document?: { html: string; token: string };
  error?: string;
};

function unavailableRead(error: { status?: number } | null): boolean {
  return error?.status !== undefined && [401, 403, 404, 410].includes(error.status);
}

/** Retain the previously loaded view until the same resource reloads successfully. */
function HtmlArtifact({ artifact }: { artifact: Artifact }) {
  const { url, baseUrl, storageMode, sourcePath } = artifact;
  const frame = useRef<HTMLIFrameElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<HtmlState>();
  const [retry, setRetry] = useState<{ key: string; attempt: number }>();
  const [bounds, setBounds] = useState({ width: 800, height: 600 });
  const [available, setAvailable] = useState({ width: 640, height: 480 });
  const key = JSON.stringify([url, baseUrl, storageMode, sourcePath]);
  const attempt = retry?.key === key ? retry.attempt : 0;
  const current = state?.key === key ? state : undefined;
  const phase = current?.phase ?? 'loading';
  const activeDocument = useRef(current?.document);
  activeDocument.current = current?.document;

  useEffect(() => {
    const controller = new AbortController();
    const token = crypto.randomUUID();
    setState(previous => ({ key, phase: 'loading', document: previous?.key === key ? previous.document : undefined }));
    void loadPackedHtmlArtifact({ url, baseUrl, storageMode, sourcePath }, token, controller.signal).then(html => {
      if (!controller.signal.aborted) setState({ key, phase: 'ready', document: { html, token } });
    }).catch(error => {
      if (controller.signal.aborted) return;
      const unavailable = unavailableRead(error);
      const message = error && typeof error.message === 'string' ? error.message : 'HTML 预览加载失败。';
      setState(previous => ({ key, phase: unavailable ? 'unavailable' : 'error', error: message,
        document: !unavailable && previous?.key === key ? previous.document : undefined }));
    });
    return () => controller.abort();
  }, [url, baseUrl, storageMode, sourcePath, key, attempt]);

  useEffect(() => {
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(entries => {
      const rect = entries[0].contentRect;
      setAvailable({ width: rect.width, height: rect.height });
    });
    if (stage.current) observer?.observe(stage.current);
    const resize = (event: MessageEvent) => {
      const data = event.data;
      const active = activeDocument.current;
      if (!active || event.source !== frame.current?.contentWindow || data?.token !== active.token) return;
      if (data.type === 'easel-artifact-preview-error') {
        setState(previous => previous && previous.document?.token === active.token
          ? { ...previous, phase: 'error', error: 'HTML 预览加载失败。' } : previous);
        return;
      }
      if (data.type !== 'easel-artifact-size' || !Number.isFinite(data.width) || !Number.isFinite(data.height)
          || data.width <= 0 || data.height <= 0 || data.width > 20000 || data.height > 50000) return;
      setBounds(previous => previous.width === data.width && previous.height === data.height
        ? previous : { width: data.width, height: data.height });
    };
    window.addEventListener('message', resize);
    return () => { observer?.disconnect(); window.removeEventListener('message', resize); };
  }, []);
  const fitted = fitArtifact(bounds.width, bounds.height, available.width - 24, available.height - 44);
  return <div className="artifact-html-stage" ref={stage}>
    {current?.document && <div className="artifact-html-fit" style={{ width: fitted.width, height: fitted.height }}>
      <iframe ref={frame} srcDoc={current.document.html} title={artifact.name} sandbox="allow-scripts" allow="clipboard-write"
        style={{ width: bounds.width, height: bounds.height, transform: `scale(${fitted.scale})` }} />
    </div>}
    <div className="artifact-recovery artifact-html-recovery" data-html-preview-status={phase}>
      <span role={phase === 'error' || phase === 'unavailable' ? 'alert' : 'status'}>
        {phase === 'loading' ? current?.document ? '正在重新读取 HTML，保留上次预览。' : '正在加载预览…'
          : phase === 'ready' ? attempt ? '已重新读取 HTML 预览。' : 'HTML 预览可用。'
            : phase === 'unavailable' ? 'HTML 预览暂不可用。'
              : <>{current?.error}{current?.document && '保留上次预览。'}</>}
      </span>
      <button type="button" className="btn btn-sm" data-html-preview-retry disabled={phase === 'loading'}
        onClick={() => setRetry({ key, attempt: attempt + 1 })}>{phase === 'ready' ? '重新读取' : '重试预览'}</button>
    </div>
  </div>;
}

function MediaArtifact({ artifact }: { artifact: Artifact }) {
  const [state, setState] = useState<{ attempt: number; shown?: number; phase: 'loading' | 'ready' | 'error' | 'unavailable' }>({
    attempt: 0, phase: 'loading',
  });
  const fixedPdf = artifact.kind === 'pdf' && artifact.storageMode === 'snapshot';
  const [approvedAttempt, setApprovedAttempt] = useState<number>();
  const { attempt, phase } = state;
  useEffect(() => {
    if (!fixedPdf || phase !== 'loading') return;
    const route = projectUrl(artifact.url);
    const root = projectUrl('/api/chat/artifacts/');
    const reject = (unavailable: boolean) => setState(previous => previous.attempt === attempt
      ? { attempt, phase: unavailable ? 'unavailable' : 'error', shown: unavailable ? undefined : previous.shown } : previous);
    if (!route.startsWith(root) || !/^[a-f0-9]{32}(?:\/archive\/(?:0|[1-9]\d*))?\/content$/.test(route.slice(root.length))) {
      reject(true);
      return;
    }
    const controller = new AbortController();
    void projectFetch(route, { method: 'HEAD', signal: controller.signal, cache: 'no-store', redirect: 'error' }).then(response => {
      if (controller.signal.aborted) return;
      if (response.ok && response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() === 'application/pdf') {
        setApprovedAttempt(attempt);
      } else reject(unavailableRead(response));
    }).catch(() => { if (!controller.signal.aborted) reject(false); });
    return () => controller.abort();
  }, [artifact.url, fixedPdf, attempt, phase]);
  const renderMedia = (attempt: number) => {
    const loaded = () => setState(previous => previous.attempt === attempt
      ? { attempt, shown: attempt, phase: 'ready' } : previous);
    const failed = () => setState(previous => previous.attempt === attempt
      ? { attempt, shown: previous.shown === attempt ? undefined : previous.shown, phase: 'error' } : previous);
    const hidden = state.shown !== undefined && state.shown !== attempt;
    const common = { src: artifact.url, onError: failed, hidden };
    if (artifact.kind === 'image') return <img key={attempt} {...common} alt={artifact.name} onLoad={loaded} />;
    if (artifact.kind === 'video') return <video key={attempt} {...common} controls preload="metadata" onLoadedMetadata={loaded} />;
    if (artifact.kind === 'audio') return <audio key={attempt} {...common} controls preload="metadata" onLoadedMetadata={loaded} />;
    return <iframe key={attempt} {...common} title={artifact.name} sandbox="" onLoad={loaded} />;
  };
  const attempts = state.shown === undefined ? [] : [state.shown];
  if (phase === 'loading' && state.shown !== attempt && (!fixedPdf || approvedAttempt === attempt)) attempts.push(attempt);
  return <>
    {attempts.map(renderMedia)}
    <div className="artifact-recovery artifact-media-recovery" data-artifact-preview-status={state.phase}>
      <span role={phase === 'error' || phase === 'unavailable' ? 'alert' : 'status'}>
        {phase === 'loading' ? state.shown === undefined ? '正在加载预览…' : '正在重新加载，保留上次预览。'
          : phase === 'ready' ? artifact.kind === 'pdf' ? '已打开 PDF 查看器。' : attempt ? '已重新加载预览。' : '预览可用。'
            : phase === 'unavailable' ? '此固定资源暂不可用。'
              : state.shown === undefined ? '预览加载失败，可下载原文件。' : '预览重载失败，保留上次预览。'}
      </span>
      <button type="button" className="btn btn-sm" data-artifact-preview-retry disabled={state.phase === 'loading'}
        onClick={() => setState(previous => ({ ...previous, attempt: previous.attempt + 1, phase: 'loading' }))}>
        {state.phase === 'ready' ? '重新加载' : '重试预览'}
      </button>
    </div>
  </>;
}

function ArtifactPreview({ artifact }: { artifact: Artifact }) {
  return artifact.kind === 'html' ? <HtmlArtifact artifact={artifact} /> : <MediaArtifact artifact={artifact} />;
}

type LegacyArchiveState = {
  phase: 'loading' | 'ready' | 'error' | 'unavailable';
  items: Artifact[];
  refreshed?: boolean;
};

export default function ArtifactGallery({ artifacts, archives }: { artifacts: Artifact[]; archives?: string[] }) {
  const [index, setIndex] = useState(0);
  const [legacyArchives, setLegacyArchives] = useState<Record<string, LegacyArchiveState>>({});
  const archiveRequests = useRef(new Map<string, AbortController>());
  const paths = [...new Set(archives?.slice(0, 8) ?? [])];
  const archiveKey = JSON.stringify(paths);
  const loadArchive = useCallback((path: string, refreshed = false) => {
    if (archiveRequests.current.has(path)) return;
    const controller = new AbortController();
    archiveRequests.current.set(path, controller);
    setLegacyArchives(previous => ({ ...previous, [path]: { phase: 'loading', items: previous[path]?.items ?? [] } }));
    void fetchArtifactArchive(path, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (!Array.isArray(result?.paths) || result.paths.some(item => typeof item !== 'string')) {
        throw new Error('Invalid legacy archive index');
      }
      const items = result.paths.map(member => artifactFromUrl(mediaUrl(member))).filter((item): item is Artifact => !!item);
      setLegacyArchives(previous => ({ ...previous, [path]: { phase: 'ready', items, refreshed } }));
    }).catch(error => {
      if (controller.signal.aborted) return;
      const unavailable = unavailableRead(error);
      setLegacyArchives(previous => ({ ...previous, [path]: {
        phase: unavailable ? 'unavailable' : 'error',
        items: unavailable ? [] : previous[path]?.items ?? [],
      } }));
    }).finally(() => {
      if (archiveRequests.current.get(path) === controller) archiveRequests.current.delete(path);
    });
  }, []);
  useEffect(() => {
    const requests = archiveRequests.current;
    const current = JSON.parse(archiveKey) as string[];
    setLegacyArchives(previous => Object.fromEntries(current.filter(path => previous[path]).map(path => [path, previous[path]])));
    current.forEach(path => loadArchive(path));
    return () => {
      for (const request of requests.values()) request.abort();
      requests.clear();
    };
  }, [archiveKey, loadArchive]);
  const archiveArtifacts = paths.flatMap(path => legacyArchives[path]?.items ?? []);
  const items = mergeArtifacts(artifacts, archiveArtifacts);
  const current = Math.min(index, items.length - 1);
  const artifact = items[current];
  if (!artifact && !paths.length) return null;
  const move = (direction: number) => setIndex((current + direction + items.length) % items.length);
  return (
    <section className="artifact-gallery" aria-label="对话产物预览" aria-roledescription="轮播"
      tabIndex={items.length > 1 ? 0 : undefined}
      onKeyDown={event => {
        if (event.target !== event.currentTarget || items.length < 2) return;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault();
          move(event.key === 'ArrowLeft' ? -1 : 1);
        }
      }}>
      <div className="artifact-header"><IconOutputs size={16} /><span>创作产物</span>
        {artifact && <span className="artifact-count" aria-live="polite">{current + 1} / {items.length}</span>}</div>
      {paths.map(path => {
        const state = legacyArchives[path];
        const phase = state?.phase ?? 'loading';
        const retained = !!state?.items.length;
        return <div className="artifact-recovery" key={path} data-legacy-archive-status={phase}>
          <strong>{path.split('/').at(-1) || 'ZIP'}</strong>
          <span role={phase === 'error' || phase === 'unavailable' ? 'alert' : 'status'}>
            {phase === 'loading' ? retained ? '正在重新读取旧 ZIP，保留上次索引。' : '正在读取旧 ZIP…'
              : phase === 'unavailable' ? '旧 ZIP 暂不可用。'
                : phase === 'error' ? retained ? '旧 ZIP 索引读取失败，保留上次预览。' : '旧 ZIP 索引读取失败。'
                  : state?.refreshed ? '已重新读取旧 ZIP。' : retained ? '旧 ZIP 索引可用。' : '旧 ZIP 没有可预览文件。'}
          </span>
          <button type="button" className="btn btn-sm" data-legacy-archive-retry disabled={phase === 'loading'}
            onClick={() => loadArchive(path, true)}>{phase === 'ready' ? '重新读取索引' : '重试读取索引'}</button>
        </div>;
      })}
      {artifact && <>
      <div className="artifact-stage">
        <ArtifactPreview key={artifact.url} artifact={artifact} />
        {items.length > 1 && <>
          <button type="button" className="artifact-arrow previous" aria-label="上一个产物" onClick={() => move(-1)}>
            <IconChevron size={20} /></button>
          <button type="button" className="artifact-arrow next" aria-label="下一个产物" onClick={() => move(1)}>
            <IconChevron size={20} /></button>
        </>}
      </div>
      <div className="artifact-footer">
        <span className="artifact-name" title={artifact.name}>{artifact.name}</span>
        <a href={artifact.url} target="_blank" rel="noreferrer">打开 ↗</a>
        <a href={artifact.url} download={artifact.name}>下载</a>
      </div>
      </>}
    </section>
  );
}
