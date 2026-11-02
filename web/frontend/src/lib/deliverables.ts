import type { AgentArtifact, AgentEvent } from './agentEvents';
import type { Artifact } from './artifacts';
import { artifactFromUrl } from './artifacts';
import { ACTIVE_PROJECT_ID, projectUrl } from './projectScope';
import { basename, isPresentedFile } from '../vendor/deepseek-harness/deliverables/presented';
import { presentedForClosing } from '../vendor/deepseek-harness/deliverables/turn-deliverables';

export type ChatDeliverable = AgentArtifact;
const SHA256 = /^[a-f0-9]{64}$/;

function relativeOutputPath(path: string): boolean {
  let value = path;
  for (let pass = 0; pass < 4; pass++) {
    if (!value || value.startsWith('/') || /^[a-z][a-z\d+.-]*:/i.test(value)
        || Array.from(value).some(character => character === '\\' || character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
        || value.split('/').some(part => !part || part === '.' || part === '..')) return false;
    try {
      const decoded = decodeURIComponent(value);
      if (decoded === value) break;
      value = decoded;
    } catch { break; } // A literal percent in a real filename is not an URL escape.
  }
  return true;
}

/** Only opaque IDs on the current IP's authenticated content route are actionable. */
export function safeDeliverableHref(file: ChatDeliverable): string | null {
  if (file.projectId !== ACTIVE_PROJECT_ID || !/^[a-f0-9]{32}$/.test(file.artifactId)
      || !relativeOutputPath(file.path)) return null;
  const route = `/api/chat/artifacts/${file.artifactId}/content`;
  const scoped = projectUrl(route);
  return file.url === route || file.url === scoped ? scoped : null;
}

/** Validate persisted/streamed metadata before it reaches any preview or link. */
export function normalizeDeliverable(value: unknown): ChatDeliverable | null {
  if (!isPresentedFile(value)) return null;
  const file = value as unknown as ChatDeliverable;
  if (typeof file.artifactId !== 'string' || typeof file.projectId !== 'string'
      || typeof file.sessionId !== 'string' || !file.sessionId || typeof file.turnId !== 'string' || !file.turnId
      || typeof file.url !== 'string' || typeof file.name !== 'string' || typeof file.mime !== 'string'
      || !Number.isSafeInteger(file.size) || file.size < 0 || !Number.isFinite(file.createdAt) || file.createdAt < 0
      || (file.status !== 'available' && file.status !== 'unavailable') || !safeDeliverableHref(file)) return null;
  if (file.storageMode === 'snapshot') {
    if (!Number.isSafeInteger(file.revision) || (file.revision ?? 0) < 1 || typeof file.sha256 !== 'string'
        || !SHA256.test(file.sha256) || (file.bundleSha256 != null
          && (typeof file.bundleSha256 !== 'string' || !SHA256.test(file.bundleSha256)))) return null;
    return { ...file, name: basename(file.path), storageMode: 'snapshot', bundleSha256: file.bundleSha256 ?? null,
      reviewStatus: 'unreviewed' };
  }
  if (file.storageMode !== undefined && file.storageMode !== 'legacy-current') return null;
  return { ...file, name: basename(file.path), storageMode: 'legacy-current', revision: null,
    sha256: null, bundleSha256: null, reviewStatus: 'unknown' };
}

/**
 * Harness's first-path-order / last-description selection, scoped to the exact
 * IP, session and turn. No closing-prose or generic tool-output inference.
 */
export function deliverablesFromEvents(events: readonly AgentEvent[]): ChatDeliverable[] {
  const seen = new Set<string>();
  const declarations: { path: string; artifact: ChatDeliverable }[] = [];
  for (const event of events) {
    if (event.kind !== 'artifact' || typeof event.callId !== 'string' || !event.callId
        || !Array.isArray(event.artifacts) || seen.has(event.id)) continue;
    seen.add(event.id);
    for (const candidate of event.artifacts) {
      const artifact = normalizeDeliverable(candidate);
      if (artifact) declarations.push({
        path: JSON.stringify([artifact.projectId, artifact.sessionId, artifact.turnId, artifact.path]), artifact,
      });
    }
  }
  return presentedForClosing(declarations).map(item => item.artifact);
}

/** Reuse the existing preview interface while serving bytes by authorized ID. */
export function deliverablePreview(file: ChatDeliverable): Artifact | null {
  const href = safeDeliverableHref(file);
  if (!href || file.status !== 'available') return null;
  const classified = artifactFromUrl(`/api/media/${file.path.split('/').map(encodeURIComponent).join('/')}`);
  if (!classified) return null;
  const directory = file.path.split('/').slice(0, -1).map(encodeURIComponent).join('/');
  if (file.storageMode === 'snapshot') return { ...classified, name: file.name, url: href,
    storageMode: 'snapshot', sourcePath: file.path };
  return { ...classified, name: file.name, url: href, storageMode: 'legacy-current',
    ...(classified.kind === 'html' ? { baseUrl: projectUrl(`/api/media/${directory ? `${directory}/` : ''}`) } : {}),
  };
}

/** Only legacy ZIPs may still resolve their archive members from current source paths. */
export function legacyArchivesForDeliverables(archives: string[], declared: ChatDeliverable[], blockedPaths: readonly string[] = []): string[] {
  const fixed = new Set([...declared.filter(file => file.storageMode === 'snapshot').map(file => file.path), ...blockedPaths]);
  return archives.filter(path => !fixed.has(path));
}

function localLinkedPath(url: string): { path: string; suffix: string } | null {
  const prefixes = [projectUrl('/api/media/'), '/api/media/', '#/outputs/'];
  const prefix = prefixes.find(value => url.startsWith(value));
  if (!prefix) return null;
  const remainder = url.slice(prefix.length);
  const encoded = remainder.split(/[?#]/)[0];
  try {
    const path = decodeURIComponent(encoded);
    return relativeOutputPath(path) ? { path, suffix: remainder.slice(encoded.length) } : null;
  } catch { return null; }
}

/** Keep data-URL commas inside a URL token; descriptors end at a candidate separator. */
function pinSourceSet(value: string, resolve: (url: string) => string | null | undefined): string | null {
  const whitespace = (character: string) => [9, 10, 12, 13, 32].includes(character.charCodeAt(0));
  const candidates: string[] = [];
  let position = 0;
  let changed = false;
  while (position < value.length) {
    while (position < value.length && (whitespace(value[position]) || value[position] === ',')) position++;
    if (position >= value.length) break;
    const start = position;
    while (position < value.length && !whitespace(value[position])) position++;
    let url = value.slice(start, position);
    let descriptors = '';
    if (url.endsWith(',')) url = url.replace(/,+$/, '');
    else {
      const descriptorStart = position;
      let parentheses = 0;
      while (position < value.length) {
        const character = value[position];
        if (character === ',' && parentheses === 0) break;
        if (character === '(') parentheses++;
        if (character === ')' && parentheses > 0) parentheses--;
        position++;
      }
      descriptors = value.slice(descriptorStart, position);
      if (value[position] === ',') position++;
    }
    const target = resolve(url);
    if (target === undefined) candidates.push((url + descriptors).trim());
    else {
      changed = true;
      if (target !== null) candidates.push((target + descriptors).trim());
    }
  }
  return changed ? candidates.join(', ') : null;
}

/** Bind only exact paths in this declaration context; never guess from basenames. */
export function pinSnapshotLinks(html: string, declared: ChatDeliverable[], blockedPaths: readonly string[] = [], imagesAsLinks = false): string {
  const snapshots = declared.filter(file => file.storageMode === 'snapshot');
  if (!html || !snapshots.length && !blockedPaths.length) return html;
  const byPath = new Map<string, ChatDeliverable | null>();
  for (const file of snapshots) {
    const previous = byPath.get(file.path);
    byPath.set(file.path, previous && previous.artifactId !== file.artifactId ? null : previous === null ? null : file);
  }
  const blocked = new Set(blockedPaths);
  const resolve = (value: string, anchor: boolean) => {
    const reference = localLinkedPath(value);
    if (!reference || !byPath.has(reference.path) && !blocked.has(reference.path)) return undefined;
    const file = byPath.get(reference.path);
    const href = file && !blocked.has(reference.path) ? safeDeliverableHref(file) : null;
    if (!href) return { target: null, file };
    const fragmentAt = reference.suffix.indexOf('#');
    const query = fragmentAt < 0 ? reference.suffix : reference.suffix.slice(0, fragmentAt);
    const download = anchor && /(?:^\?|&)download=(?:1|true)(?:&|$)/i.test(query) ? '?download=1' : '';
    return { target: href + download + (fragmentAt < 0 ? '' : reference.suffix.slice(fragmentAt)), file };
  };
  // Inert template parsing prevents the parent from starting an image request
  // before the current-media reference has been rebound to the immutable ID.
  const template = document.createElement('template');
  template.innerHTML = html;
  for (const link of template.content.querySelectorAll('a,img,video,audio,source,image')) {
    const tag = link.localName.toLowerCase();
    const image = tag === 'img';
    const attributes = tag === 'a' ? ['href'] : tag === 'video' ? ['src', 'poster']
      : tag === 'image' ? ['href', 'xlink:href'] : ['src'];
    for (const attribute of attributes) {
      const resolution = resolve(link.getAttribute(attribute) || '', tag === 'a');
      if (!resolution) continue;
      const { target, file } = resolution;
      if (target === null) {
        link.removeAttribute(attribute); link.removeAttribute('download');
        if (image) link.removeAttribute('srcset');
        link.setAttribute('aria-disabled', 'true'); link.setAttribute('title', '请使用可验证的对应版本卡查看文件');
        continue;
      }
      if (image) link.removeAttribute('srcset');
      if (image && imagesAsLinks) {
        if (link.parentElement?.tagName === 'A') link.replaceWith(document.createTextNode(link.getAttribute('alt') || file?.name || '交付图片'));
        else {
          const anchor = document.createElement('a');
          anchor.href = target; anchor.target = '_blank'; anchor.rel = 'noreferrer';
          anchor.textContent = link.getAttribute('alt') || file?.name || '交付图片';
          link.replaceWith(anchor);
        }
      } else if (attribute === 'xlink:href') {
        link.removeAttribute(attribute);
        link.setAttributeNS('http://www.w3.org/1999/xlink', attribute, target);
      } else link.setAttribute(attribute, target);
    }
    const sourceSet = link.getAttribute('srcset');
    if ((tag === 'source' || image) && sourceSet) {
      const pinned = pinSourceSet(sourceSet, value => resolve(value, false)?.target);
      if (pinned === '') { link.removeAttribute('srcset'); link.setAttribute('aria-disabled', 'true'); }
      else if (pinned !== null) link.setAttribute('srcset', pinned);
    }
  }
  return template.innerHTML;
}

/** A malformed snapshot must not silently reveal the mutable source via prose/gallery fallback. */
export function rejectedSnapshotPaths(events: readonly AgentEvent[]): string[] {
  const paths = new Set<string>();
  for (const event of events) {
    if (event.kind !== 'artifact' || !Array.isArray(event.artifacts)) continue;
    for (const file of event.artifacts) if (file?.storageMode === 'snapshot' && file.projectId === ACTIVE_PROJECT_ID
      && typeof file.path === 'string' && relativeOutputPath(file.path) && !normalizeDeliverable(file)) paths.add(file.path);
  }
  return [...paths];
}

/** Host history stays in the original card's exact authority and file namespace. */
export function normalizeDeliverableVersions(anchor: ChatDeliverable, values: unknown[]): ChatDeliverable[] {
  const byId = new Map<string, ChatDeliverable>();
  for (const value of values) {
    const file = normalizeDeliverable(value);
    if (file && file.projectId === anchor.projectId && file.sessionId === anchor.sessionId && file.path === anchor.path) {
      byId.set(file.artifactId, file);
    }
  }
  return [...byId.values()].sort((left, right) => (right.revision ?? 0) - (left.revision ?? 0) || right.createdAt - left.createdAt);
}

export function deliverableVersionsUrl(file: ChatDeliverable): string | null {
  const href = safeDeliverableHref(file);
  return href ? href.replace(/\/content$/, '/versions') : null;
}

/** Prose links may remain useful, but never create a second gallery for a declaration. */
export function omitDeclaredArtifacts(legacy: Artifact[], declared: ChatDeliverable[], blockedPaths: readonly string[] = []): Artifact[] {
  const paths = new Set([...declared.map(file => file.path), ...blockedPaths]);
  const routes = new Set(declared.map(safeDeliverableHref).filter(Boolean));
  const prefixes = [projectUrl('/api/media/'), '/api/media/', '#/outputs/'];
  return legacy.filter(file => {
    if (routes.has(file.url.split(/[?#]/)[0])) return false;
    const prefix = prefixes.find(value => file.url.startsWith(value));
    if (!prefix) return true;
    try { return !paths.has(decodeURIComponent(file.url.slice(prefix.length).split(/[?#]/)[0])); }
    catch { return true; }
  });
}
