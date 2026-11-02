/** Easel transport/security bridge for the vendored DeepSeek opaque HTML packer. */
import type { Artifact } from './artifacts';
import { previewBaseUrl } from './artifacts';
import { projectFetch, projectUrl } from './projectScope';
import { createHtmlDocument } from '../vendor/deepseek-harness/html/bootstrap';
import { packHtml, type ReadHtmlRelative } from '../vendor/deepseek-harness/html/pack';

type HtmlArtifactSource = Pick<Artifact, 'url' | 'baseUrl'>;
const MAX_DOCUMENT_BYTES = 32 * 1024 * 1024;
const MAX_ASSET_BYTES = 4 * 1024 * 1024;
const STATIC_SUFFIX = /\.(?:js|css|png|jpe?g|gif|webp|avif|bmp|ico|svg)$/i;
const PRIVATE_SEGMENT = /^(?:_login|_sessions|_debug|_harness|_access|_inbox|_interactions|_analytics|_publish|_profile_build|auth|cookies?|credentials?|secrets?)(?:\.[^.]+)?$/i;
const CREDENTIAL_SUFFIX = /\.(?:pem|key|p12|pfx)$/i;

function unsafeCharacters(value: string): boolean {
  return Array.from(value).some(character => character === '\\' || character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
}

function assertPublicParts(parts: string[]): void {
  if (!parts.length || parts[0].startsWith('_') || parts.some(part => !part || part.startsWith('.')
      || PRIVATE_SEGMENT.test(part) || CREDENTIAL_SUFFIX.test(part))) throw new Error('HTML 资源不在当前 IP 的公开内容目录');
}

function sourceRoute(source: HtmlArtifactSource): { route: string; id?: string; document?: string } {
  const route = projectUrl(source.url.split(/[?#]/)[0]);
  const artifactPrefix = projectUrl('/api/chat/artifacts/');
  const id = route.startsWith(artifactPrefix) ? /^([a-f0-9]{32})\/content$/.exec(route.slice(artifactPrefix.length))?.[1] : undefined;
  if (id) return { route, id };
  const mediaPrefix = projectUrl('/api/media/');
  if (!route.startsWith(mediaPrefix)) throw new Error('HTML 来源不是当前 IP 的授权内容');
  const document = decodeURIComponent(route.slice(mediaPrefix.length));
  if (unsafeCharacters(document) || !/\.html?$/i.test(document)) throw new Error('HTML 来源路径无效');
  assertPublicParts(document.split('/'));
  return { route, document };
}

function sourceDirectory(source: HtmlArtifactSource, origin: string, document?: string): string[] {
  if (document) return document.split('/').slice(0, -1);
  const base = previewBaseUrl(source, origin);
  const prefix = new URL(projectUrl('/api/media/'), origin).href;
  if (!base.startsWith(prefix)) throw new Error('HTML 缺少可验证的资源目录');
  const directory = decodeURIComponent(base.slice(prefix.length).replace(/\/$/, ''));
  if (unsafeCharacters(directory)) throw new Error('HTML 资源目录无效');
  return directory ? directory.split('/') : [];
}

/** Consume a finite response, including servers that omit or misstate Content-Length. */
async function readBytes(response: Response, limit: number, signal: AbortSignal): Promise<Uint8Array<ArrayBuffer>> {
  if (!response.ok) throw new Error(`HTML 预览资源读取失败（${response.status}）`);
  const length = Number(response.headers.get('Content-Length'));
  if (Number.isFinite(length) && length > limit) throw new Error('HTML 预览资源超过大小限制');
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      signal.throwIfAborted();
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > limit) throw new Error('HTML 预览资源超过大小限制');
      chunks.push(chunk.value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally { reader.releaseLock(); }
  signal.throwIfAborted();
  const data = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.byteLength; }
  return data;
}

/**
 * Only the static packer owns this reader; it is never exposed to iframe messages.
 * The server revalidates canonical paths and rejects symlinks/private targets.
 */
export function createReadHtmlRelative(source: HtmlArtifactSource, lifetime: AbortSignal): ReadHtmlRelative {
  return async (reference, signal) => {
    const combined = AbortSignal.any([lifetime, signal]);
    combined.throwIfAborted();
    const suffix = reference.search(/[?#]/u);
    const path = decodeURIComponent(suffix === -1 ? reference : reference.slice(0, suffix));
    if (!path || /^(?:[a-z][a-z\d+.-]*:|[/\\])/iu.test(path) || unsafeCharacters(path) || !STATIC_SUFFIX.test(path)) {
      throw new Error('HTML 资源必须使用受支持的相对文件路径');
    }
    const coordinates = sourceRoute(source);
    const parts = sourceDirectory(source, window.location.origin, coordinates.document);
    for (const part of path.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') {
        if (!parts.length) throw new Error('HTML 资源不能越出当前 IP 内容目录');
        parts.pop();
      } else parts.push(part);
    }
    assertPublicParts(parts);
    const query = new URLSearchParams({ path });
    let endpoint: string;
    if (coordinates.id) endpoint = `/api/chat/artifacts/${coordinates.id}/related`;
    else {
      query.set('document', coordinates.document!);
      endpoint = '/api/chat/artifact-related';
    }
    const response = await projectFetch(projectUrl(`${endpoint}?${query}`), { signal: combined, redirect: 'error' });
    return { data: await readBytes(response, MAX_ASSET_BYTES, combined) };
  };
}

/** Pack complete bytes first; no success document is published on a failed read. */
export async function loadPackedHtmlArtifact(source: HtmlArtifactSource, token: string, signal: AbortSignal): Promise<string> {
  const coordinates = sourceRoute(source);
  const response = await projectFetch(coordinates.route, { signal, redirect: 'error' });
  const data = await readBytes(response, MAX_DOCUMENT_BYTES, signal);
  const bundle = await packHtml(data, createReadHtmlRelative(source, signal), signal);
  signal.throwIfAborted();
  // The bootstrap, not the authenticated parent, parses the complete document
  // and appends this reporting script. Parent discovery stays in an inert template.
  const script = `(() => {
    const token = ${JSON.stringify(token)};
    const parentApi = ${JSON.stringify(window.location.origin + '/api/')};
    const failed = resource => {
      const url = resource && (resource.currentSrc || resource.src || resource.href) || '';
      if (typeof url === 'string' && (url.startsWith('blob:') || url.startsWith(parentApi)))
        parent.postMessage({type:'easel-artifact-preview-error',token}, '*');
    };
    addEventListener('error', event => failed(event.target), true);
    const report = () => parent.postMessage({type:'easel-artifact-size',token,
      width:Math.max(document.documentElement.scrollWidth,document.body.scrollWidth),
      height:Math.max(document.documentElement.scrollHeight,document.body.scrollHeight)}, '*');
    addEventListener('load', () => { for (const image of document.images) if (image.complete && !image.naturalWidth) failed(image); report(); });
    new ResizeObserver(report).observe(document.body);
    document.fonts?.ready.then(report);
    report();
  })();`;
  return createHtmlDocument({ ...bundle, preview: {
    baseUrl: previewBaseUrl(source, window.location.origin), script,
  } });
}
