/** Read-only publication approval contract. Decisions always go through the Host. */
import type { Artifact } from './artifacts';
import { artifactFromUrl } from './artifacts';
import { ACTIVE_PROJECT_ID, projectUrl } from './projectScope';

export type PublicationMode = 'publish_now' | 'create_draft' | 'manual_export' | 'scheduled';
export type PublicationApprovalState = 'pending' | 'approved' | 'rejected' | 'expired' | 'invalidated';
export interface PublicationApprovalMedia {
  artifactId: string; path: string; name: string; mime: string; revision: number;
  sha256: string; bundleSha256: string | null; size: number; url: string;
}
export interface PublicationApprovalSubject {
  schemaVersion: 1;
  project: { id: string; name: string };
  platform: { id: string; name: string };
  account: { id: string; name: string };
  mode: PublicationMode;
  scheduledAt: string | null;
  audience: 'public' | 'platform_draft' | 'manual';
  title: string; body: string; tags: string; persona: string;
  media: PublicationApprovalMedia[];
}
export interface PublicationOperationResult {
  status: 'executing' | 'submitted' | 'confirmed' | 'unknown' | 'failed' | 'draft_created' | 'exported' | 'scheduled';
  [key: string]: unknown;
}
export interface PublicationApprovalRecord {
  id: string; payloadHash: string; subject: PublicationApprovalSubject; state: PublicationApprovalState;
  createdAt: number; expiresAt: number; actorId: string; projectId: string;
  operationId: string | null; result: PublicationOperationResult | null;
}

const HASH = /^[a-f0-9]{64}$/;
const cleanId = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
  && !Array.from(value).some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);

export function validPublicationDigest(value: unknown): value is string { return typeof value === 'string' && HASH.test(value); }

export function publicationMediaUrl(subject: PublicationApprovalSubject, media: PublicationApprovalMedia): string | null {
  if (subject.project?.id !== ACTIVE_PROJECT_ID || !/^[a-f0-9]{32}$/.test(media.artifactId)
      || typeof media.path !== 'string' || !media.path || media.path.startsWith('/')
      || /^[a-z][a-z\d+.-]*:/i.test(media.path)
      || Array.from(media.path).some(character => character === '\\' || character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
      || media.path.split('/').some(part => !part || part === '.' || part === '..')
      || !Number.isSafeInteger(media.revision) || media.revision < 1 || !validPublicationDigest(media.sha256)
      || media.bundleSha256 != null && !validPublicationDigest(media.bundleSha256)
      || !Number.isSafeInteger(media.size) || media.size < 0) return null;
  const route = `/api/chat/artifacts/${media.artifactId}/content`;
  const scoped = projectUrl(route);
  return media.url === route || media.url === scoped ? scoped : null;
}

export function publicationMediaPreview(subject: PublicationApprovalSubject, media: PublicationApprovalMedia): Artifact | null {
  const url = publicationMediaUrl(subject, media);
  if (!url) return null;
  const kind = artifactFromUrl(`/api/media/${media.path.split('/').map(encodeURIComponent).join('/')}`)?.kind;
  return kind ? { url, name: media.name, kind, storageMode: 'snapshot', sourcePath: media.path } : null;
}

export function publicationActionLabel(subject: PublicationApprovalSubject): string {
  return ({ publish_now: '立即公开发布', create_draft: '创建平台草稿（尚未公开发布）',
    manual_export: '导出人工发布包（尚未发布）', scheduled: '定时公开发布' } as Record<string, string>)[subject.mode] || '动作待确认';
}

export function publicationApproveLabel(subject: PublicationApprovalSubject): string {
  return ({ publish_now: '批准此版本并执行', create_draft: '批准此版本并创建草稿',
    manual_export: '批准此版本并导出', scheduled: '批准此版本并安排定时发布' } as Record<string, string>)[subject.mode] || '动作待确认';
}

export function validPublicationSubject(subject: PublicationApprovalSubject): boolean {
  if (!subject || subject.schemaVersion !== 1 || !cleanId(subject.project?.id) || subject.project.id !== ACTIVE_PROJECT_ID
      || !cleanId(subject.project?.name) || !cleanId(subject.platform?.id) || !cleanId(subject.platform?.name)
      || !cleanId(subject.account?.id) || !cleanId(subject.account?.name)
      || typeof subject.title !== 'string' || typeof subject.body !== 'string'
      || typeof subject.tags !== 'string' || typeof subject.persona !== 'string' || !Array.isArray(subject.media)) return false;
  const audience = ({ publish_now: 'public', scheduled: 'public', create_draft: 'platform_draft', manual_export: 'manual' } as Record<string, string>)[subject.mode];
  if (!audience || subject.audience !== audience || !subject.title.trim() && !subject.body.trim() && !subject.media.length) return false;
  if (subject.mode === 'scheduled' && (typeof subject.scheduledAt !== 'string' || !Number.isFinite(Date.parse(subject.scheduledAt)))) return false;
  return subject.media.every(media => !!media && typeof media.name === 'string' && typeof media.mime === 'string'
    && !!publicationMediaUrl(subject, media));
}

/** Lifecycle eligibility is not a substitute for the Host's membership/role check. */
export function publicationCanDecide(record: PublicationApprovalRecord, now = Date.now()): boolean {
  return !!record && record.state === 'pending' && cleanId(record.id) && validPublicationDigest(record.payloadHash)
    && record.projectId === ACTIVE_PROJECT_ID && record.subject?.project?.id === record.projectId
    && Number.isFinite(record.expiresAt) && now < record.expiresAt;
}

export function publicationApprovalStatus(record: PublicationApprovalRecord): string {
  if (record.state === 'approved') {
    const result = ({ executing: '正在执行', submitted: '已提交，等待平台回执', confirmed: '平台回执已确认',
      unknown: '执行结果待核实', failed: '执行失败', draft_created: '草稿已创建，尚未公开发布',
      exported: '发布包已导出，尚未公开发布', scheduled: '定时任务已安排' } as Record<string, string>)[record.result?.status || ''];
    return `已批准 · ${result || '等待执行回执'}`;
  }
  return ({ pending: '等待你确认', rejected: '已拒绝', expired: '已过期，未批准', invalidated: '内容已变更，原确认已失效' } as Record<string, string>)[record.state] || '状态待确认';
}
