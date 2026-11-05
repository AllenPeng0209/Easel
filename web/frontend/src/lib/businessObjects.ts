/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Business-owned keyed view boundary adapted from deepseek-ai/deepseek-harness
 * packages/client/ui-tool/src/client/tool/ToolCallTree.tsx and src/client/apply.ts
 * @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Xiangya changes: validate existing Host business DTOs, scoped observations and navigation.
 * Notice: integrations/deepseek-harness/LICENSE.
 */
import { ACTIVE_PROJECT_ID, ACTIVE_USER_ID } from './projectScope';
import { normalizeDeliverable } from './deliverables';
import type { ChatDeliverable } from './deliverables';
import { normalizeWorkshopSubmissionAttempts } from './workshop';

export type BusinessObjectKind = 'draft' | 'workshop';
export interface BusinessObjectAction {
  kind: 'open'; target: 'artifact' | 'workshop'; entityId: string; label: string;
}
export interface BusinessObject {
  schemaVersion: 1;
  actorId: string; projectId: string; kind: BusinessObjectKind; id: string;
  title: string; summary: string;
  status: { code: string; label: string; source: 'stored' | 'computed' | 'unknown' };
  version: { revision: number | null; sha256: string | null };
  snapshotHash: string; observedAt: number;
  details: Record<string, unknown>;
  actions: BusinessObjectAction[];
}

export const BUSINESS_OBJECT_WIRE_NAME = 'easel_business_objects';
const HASH = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9_-]{1,160}$/;
const FIXED_ID = /^[a-f0-9]{32}$/;
const KINDS = new Set<unknown>(['draft', 'workshop']);
const STATUS_SOURCES = new Set<unknown>(['stored', 'computed', 'unknown']);
const TARGETS: Record<BusinessObjectKind, BusinessObjectAction['target']> = {
  draft: 'artifact', workshop: 'workshop',
};
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
function text(value: unknown, maximum = 4000): value is string {
  if (typeof value !== 'string' || value.length > maximum * 2) return false;
  const characters = Array.from(value);
  return characters.length <= maximum && !characters.some(character => {
    const code = character.charCodeAt(0);
    return code === 127 || code < 32 && !['\r', '\n', '\t'].includes(character);
  });
}
const id = (value: unknown): value is string => typeof value === 'string' && ID.test(value);
const entityId = (_kind: BusinessObjectKind, value: unknown): value is string => typeof value === 'string'
  && FIXED_ID.test(value);
const nonempty = (value: unknown, maximum = 160): value is string => text(value, maximum) && !!value.trim();

/** Current-user markers select presentation scope; Host authorization remains mandatory. */
export function businessObjectInScope(value: unknown): boolean {
  return isRecord(value) && !!ACTIVE_USER_ID && value.actorId === ACTIVE_USER_ID && value.projectId === ACTIVE_PROJECT_ID;
}

/** Only explicit business fields survive; engine/config/token objects are never generic JSON output. */
function fields(value: unknown, keys: readonly string[]): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  if (!isRecord(value)) return result;
  for (const key of keys) {
    const item = value[key];
    if (item === null || typeof item === 'boolean' || typeof item === 'number' && Number.isFinite(item)
        || text(item, 16000)) result[key] = item;
  }
  return result;
}

const ARTIFACT_FIELDS = ['artifactId', 'projectId', 'sessionId', 'turnId', 'path', 'name', 'mime', 'size', 'description',
  'status', 'createdAt', 'url', 'storageMode', 'revision', 'sha256', 'bundleSha256', 'reviewStatus'];
const PROJECT_FIELDS = ['id', 'title', 'brief', 'aspect_ratio', 'revision', 'stage', 'archived_at', 'created_at', 'updated_at'];
const DELIVERABLE_FIELDS = ['id', 'name', 'path', 'revision', 'sha256', 'render_id', 'created_at'];
const REVIEW_FIELDS = ['id', 'revision', 'artifact_id', 'artifact_sha256', 'kind', 'decision', 'notes', 'created_at',
  'actorId', 'actor_id', 'reviewerId', 'reviewer_id', 'reviewer'];
const RENDER_FIELDS = ['id', 'operation_id', 'task_id', 'content_revision', 'status', 'submission_state', 'progress', 'progress_known', 'artifact_id',
  'collected_revision', 'created_at', 'updated_at', 'collected_at'];
const PUBLICATION_FIELDS = ['id', 'revision', 'artifact_id', 'artifact_sha256', 'platform', 'created_at', 'published_at', 'source', 'kind'];
const VERIFICATION_FIELDS = ['publishEligibility', 'deliveryVerification', 'publicationEvidence'];

function details(kind: BusinessObjectKind, value: unknown): Record<string, unknown> {
  if (!isRecord(value)) return {};
  if (kind === 'draft') return isRecord(value.artifact) ? { artifact: fields(value.artifact, ARTIFACT_FIELDS) } : {};
  if (!isRecord(value.project)) return {};
  const project = fields(value.project, PROJECT_FIELDS);
  for (const [key, allowed] of [['deliverables', DELIVERABLE_FIELDS], ['reviews', REVIEW_FIELDS],
    ['renders', RENDER_FIELDS], ['publications', PUBLICATION_FIELDS]] as const) {
    const entries = value.project[key];
    if (Array.isArray(entries)) project[key] = entries.filter(isRecord).map(entry => ({ ...fields(entry, allowed),
      ...(key === 'reviews' && isRecord(entry.actor) ? { actor: fields(entry.actor, ['id', 'name', 'display_name']) } : {}),
      ...(key === 'renders' && isRecord(entry.cancellation) ? { cancellation: fields(entry.cancellation,
        ['status', 'taskId', 'renderId', 'jobStatus', 'requestedAt', 'updatedAt']) } : {}),
      ...(key === 'renders' && Array.isArray(entry.submission_attempts)
        ? { submission_attempts: normalizeWorkshopSubmissionAttempts(entry.submission_attempts) } : {}) }));
  }
  return { project, ...fields(value, VERIFICATION_FIELDS),
    ...(isRecord(value.handoff) ? { handoff: fields(value.handoff,
      ['revision', 'shotId', 'artifactId', 'artifactSha256', 'artifactRevision']) } : {}) };
}

export function normalizeBusinessObject(value: unknown): BusinessObject | null {
  if (!isRecord(value) || value.schemaVersion !== 1 || !businessObjectInScope(value)
      || !id(value.actorId) || !id(value.projectId) || !KINDS.has(value.kind)
      || !text(value.title, 1024) || !text(value.summary, 8000) || !isRecord(value.status)
      || !nonempty(value.status.code, 80) || !nonempty(value.status.label, 300)
      || !STATUS_SOURCES.has(value.status.source)
      || !isRecord(value.version) || !isRecord(value.details) || !Array.isArray(value.actions)
      || typeof value.snapshotHash !== 'string' || !HASH.test(value.snapshotHash)
      || !Number.isSafeInteger(value.observedAt) || (value.observedAt as number) <= 0
      || !Number.isFinite(new Date(value.observedAt as number).getTime())) return null;
  const kind = value.kind as BusinessObjectKind;
  if (!entityId(kind, value.id)) return null;
  const revision = value.version.revision;
  const sha256 = value.version.sha256;
  if (revision !== null && (!Number.isSafeInteger(revision) || (revision as number) < 0)
      || sha256 !== null && (typeof sha256 !== 'string' || !HASH.test(sha256))) return null;
  const actions: BusinessObjectAction[] = [];
  const seen = new Set<string>();
  const target = TARGETS[kind];
  for (const action of value.actions) {
    if (!isRecord(action) || action.kind !== 'open' || action.target !== target
        || action.entityId !== value.id || !nonempty(action.label)) continue;
    const key = `${action.kind}:${action.target}:${action.entityId}`;
    if (seen.has(key)) continue;
    seen.add(key);
    actions.push({ kind: 'open', target, entityId: value.id, label: action.label });
  }
  return { schemaVersion: 1, actorId: value.actorId, projectId: value.projectId, kind, id: value.id,
    title: value.title, summary: value.summary, status: { code: value.status.code, label: value.status.label,
      source: value.status.source as BusinessObject['status']['source'] },
    version: { revision: revision as number | null, sha256: sha256 as string | null },
    snapshotHash: value.snapshotHash, observedAt: value.observedAt as number,
    details: details(kind, value.details), actions };
}

/** Identity excludes mutable versions; newer Host observations may expose their true revision. */
export function businessObjectIdentity(record: BusinessObject): string {
  return JSON.stringify([record.actorId, record.projectId, record.kind, record.id]);
}
export function businessObjectEventKey(record: BusinessObject): string {
  return JSON.stringify(['business', record.actorId, record.projectId, record.kind, record.id, record.observedAt, record.snapshotHash]);
}
export function businessObjectCanRefresh(current: BusinessObject, next: BusinessObject): boolean {
  return businessObjectInScope(current) && businessObjectInScope(next)
    && businessObjectIdentity(current) === businessObjectIdentity(next) && next.observedAt > current.observedAt;
}

/** Navigation is built locally and never accepts a server href or a different entity. */
export function businessObjectOpenHref(record: BusinessObject, action: BusinessObjectAction): string | null {
  if (!businessObjectInScope(record) || record.kind !== 'workshop'
      || !id(record.projectId) || !entityId(record.kind, record.id) || action.kind !== 'open'
      || action.target !== TARGETS[record.kind] || action.entityId !== record.id) return null;
  const selected = record.kind === 'workshop' && isRecord(record.details.handoff) ? record.details.handoff.artifactId : null;
  const suffix = typeof selected === 'string' && /^[a-f0-9]{32}$/.test(selected) ? `/artifact/${selected}` : '';
  return `#/business/${encodeURIComponent(record.projectId)}/${record.kind}/${encodeURIComponent(record.id)}${suffix}`;
}

export function businessObjectDraftArtifact(record: BusinessObject): ChatDeliverable | null {
  if (record.kind !== 'draft' || !businessObjectInScope(record)) return null;
  const file = normalizeDeliverable(record.details.artifact);
  return file && file.storageMode === 'snapshot' && file.projectId === record.projectId && file.artifactId === record.id
    && file.revision === record.version.revision && file.sha256 === record.version.sha256 ? file : null;
}
