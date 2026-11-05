import { ACTIVE_PROJECT_ID, ACTIVE_USER_ID, projectUrl } from './projectScope';
import type { AgentArtifact } from './agentEvents';
import type { PublicationApprovalRecord } from './publicationApproval';

/** Navigation and draft references never authorize an external action. */
export const PUBLICATION_HANDOFF_EVENT = 'easel:publish-handoff';
const SHA256 = /^[a-f0-9]{64}$/;
const ARTIFACT_ID = /^[a-f0-9]{32}$/;
const APPROVAL_ID = /^[A-Za-z0-9_-]{1,160}$/;
const MEDIA_LIFETIME = 24 * 60 * 60 * 1000;
const NAVIGATION_LIFETIME = 5 * 60 * 1000;

export interface PublicationMediaReference {
  actorId: string; projectId: string; artifactId: string; storageMode: 'snapshot';
  path: string; name: string; mime: string; size: number; url: string;
  revision: number; sha256: string; bundleSha256: string | null;
  selectedAt: number; expiresAt: number;
}
export interface PublicationApprovalReference {
  actorId: string; projectId: string; approvalId: string; payloadHash: string;
}
export interface PublicationCopy {
  title?: string; body?: string; tags?: string; platform?: string;
}
interface HandoffScope { schemaVersion: 1; actorId: string; projectId: string; createdAt: number; expiresAt: number }
export type PublicationHandoff = HandoffScope & (
  { kind: 'media'; media: PublicationMediaReference[]; copy: PublicationCopy }
  | { kind: 'approval'; approvalId: string; payloadHash: string }
);

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const inScope = (value: { actorId?: unknown; projectId?: unknown }): boolean => !!ACTIVE_USER_ID
  && value.actorId === ACTIVE_USER_ID && value.projectId === ACTIVE_PROJECT_ID;
function confinedPath(path: string): boolean {
  let value = path;
  for (let pass = 0; pass < 4; pass++) {
    if (!value || value.startsWith('/') || /^[a-z][a-z\d+.-]*:/i.test(value)
        || Array.from(value).some(character => character === '\\' || character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
        || value.split('/').some(part => !part || part === '.' || part === '..')) return false;
    try { const decoded = decodeURIComponent(value); if (decoded === value) return true; value = decoded; }
    catch { return true; }
  }
  return false;
}

export function validPublicationMediaReference(value: unknown, now = Date.now()): value is PublicationMediaReference {
  if (!object(value) || !inScope(value) || value.storageMode !== 'snapshot'
      || typeof value.artifactId !== 'string' || !ARTIFACT_ID.test(value.artifactId)
      || typeof value.path !== 'string' || !confinedPath(value.path) || typeof value.name !== 'string' || !value.name
      || typeof value.mime !== 'string' || !/^(image|video)\/[a-z0-9.+-]+$/i.test(value.mime)
      || !Number.isSafeInteger(value.size) || (value.size as number) <= 0
      || !Number.isSafeInteger(value.revision) || (value.revision as number) < 1
      || typeof value.sha256 !== 'string' || !SHA256.test(value.sha256)
      || value.bundleSha256 !== null && (typeof value.bundleSha256 !== 'string' || !SHA256.test(value.bundleSha256))
      || typeof value.selectedAt !== 'number' || !Number.isFinite(value.selectedAt) || value.selectedAt > now + 1000
      || typeof value.expiresAt !== 'number' || !Number.isFinite(value.expiresAt) || value.expiresAt <= now
      || value.expiresAt <= value.selectedAt || value.expiresAt - value.selectedAt > MEDIA_LIFETIME) return false;
  const route = `/api/chat/artifacts/${value.artifactId}/content`;
  return value.url === route || value.url === projectUrl(route);
}

export function publicationMediaReference(file: AgentArtifact): PublicationMediaReference | null {
  const selectedAt = Date.now();
  const reference = { actorId: ACTIVE_USER_ID, projectId: file.projectId, artifactId: file.artifactId,
    storageMode: file.storageMode, path: file.path, name: file.name, mime: file.mime, size: file.size,
    revision: file.revision, sha256: file.sha256, bundleSha256: file.bundleSha256 ?? null,
    url: file.url, selectedAt, expiresAt: selectedAt + MEDIA_LIFETIME };
  if (file.status !== 'available' || !validPublicationMediaReference(reference)) return null;
  return { ...reference, url: projectUrl(`/api/chat/artifacts/${file.artifactId}/content`) } as PublicationMediaReference;
}

/** A local reference is re-read from the actor/IP-owned catalog before prepare. */
export function publicationMediaReferenceMatches(reference: PublicationMediaReference, metadata: unknown): boolean {
  if (!validPublicationMediaReference(reference) || !object(metadata)
      || metadata.status !== 'available' || metadata.storageMode !== 'snapshot') return false;
  return (['artifactId', 'projectId', 'path', 'name', 'mime', 'size', 'revision', 'sha256'] as const)
    .every(key => reference[key] === metadata[key])
    && reference.bundleSha256 === (metadata.bundleSha256 ?? null)
    && (metadata.url === `/api/chat/artifacts/${reference.artifactId}/content` || metadata.url === reference.url);
}

export function validPublicationApprovalReference(value: unknown): value is PublicationApprovalReference {
  return object(value) && inScope(value) && typeof value.approvalId === 'string' && APPROVAL_ID.test(value.approvalId)
    && typeof value.payloadHash === 'string' && SHA256.test(value.payloadHash);
}

export function normalizePublicationHandoff(value: unknown, now = Date.now()): PublicationHandoff | null {
  if (!object(value) || value.schemaVersion !== 1 || !inScope(value)
      || typeof value.createdAt !== 'number' || !Number.isFinite(value.createdAt) || value.createdAt > now + 1000
      || typeof value.expiresAt !== 'number' || !Number.isFinite(value.expiresAt) || value.expiresAt <= now
      || value.expiresAt <= value.createdAt || value.expiresAt - value.createdAt > NAVIGATION_LIFETIME) return null;
  if (value.kind === 'approval') return validPublicationApprovalReference(value) ? value as unknown as PublicationHandoff : null;
  if (value.kind !== 'media' || !Array.isArray(value.media) || !value.media.length || value.media.length > 32
      || !value.media.every(item => validPublicationMediaReference(item, now)) || !object(value.copy)) return null;
  for (const [key, limit] of [['title', 1000], ['body', 100000], ['tags', 2000], ['platform', 64]] as const) {
    if (value.copy[key] !== undefined && (typeof value.copy[key] !== 'string' || value.copy[key].length > limit)) return null;
  }
  return value as unknown as PublicationHandoff;
}

function dispatch(detail: PublicationHandoff): boolean {
  if (!normalizePublicationHandoff(detail)) return false;
  window.dispatchEvent(new window.CustomEvent(PUBLICATION_HANDOFF_EVENT, { detail }));
  return true;
}
function scope(): HandoffScope {
  const createdAt = Date.now();
  return { schemaVersion: 1, actorId: ACTIVE_USER_ID, projectId: ACTIVE_PROJECT_ID,
    createdAt, expiresAt: createdAt + NAVIGATION_LIFETIME };
}
export function requestDeliverablePublication(file: AgentArtifact, copy: PublicationCopy = {}): boolean {
  const reference = publicationMediaReference(file);
  const displayName = (file as AgentArtifact & { displayName?: unknown }).displayName;
  const filename = typeof displayName === 'string' && displayName.trim() ? displayName.trim() : file.name;
  // Descriptions are presentation/review notes, not publication copy.
  const title = copy.title?.trim() ? copy.title : filename.replace(/\.(?:mp4|mov|webm|m4v|mkv|avi|flv|ts|png|jpe?g|gif|webp|bmp|svg)$/i, '');
  return !!reference && dispatch({ ...scope(), kind: 'media', media: [reference],
    copy: { ...copy, title } });
}
export function requestApprovalPublication(record: PublicationApprovalRecord): boolean {
  if (!record || !inScope(record) || record.subject?.project?.id !== ACTIVE_PROJECT_ID) return false;
  return dispatch({ ...scope(), kind: 'approval', approvalId: record.id, payloadHash: record.payloadHash });
}
