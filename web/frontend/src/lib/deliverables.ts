import type { AgentArtifact, AgentEvent } from './agentEvents';
import type { Artifact } from './artifacts';
import { artifactFromUrl } from './artifacts';
import { ACTIVE_PROJECT_ID, projectUrl } from './projectScope';
import { basename, isPresentedFile } from '../vendor/deepseek-harness/deliverables/presented';
import { presentedForClosing } from '../vendor/deepseek-harness/deliverables/turn-deliverables';

export type ChatDeliverable = AgentArtifact;

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
  return { ...file, name: basename(file.path) };
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
  return { ...classified, name: file.name, url: href,
    ...(classified.kind === 'html' ? { baseUrl: projectUrl(`/api/media/${directory ? `${directory}/` : ''}`) } : {}),
  };
}

/** Prose links may remain useful, but never create a second gallery for a declaration. */
export function omitDeclaredArtifacts(legacy: Artifact[], declared: ChatDeliverable[]): Artifact[] {
  const paths = new Set(declared.map(file => file.path));
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
