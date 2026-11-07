import type { PublishDraft } from './store';
import { normalizePublicationHandoff, validPublicationMediaReference } from './publicationHandoff';

/** A draft is editable content, never evidence of platform approval. */
export type PublicationEditorDraft = PublishDraft & { mediaPaths?: string[] };
const PLATFORMS = new Set(['xiaohongshu', 'douyin', 'kuaishou', 'weixin-channels', 'zhihu', 'juejin',
  'bilibili', 'toutiao', 'wechat-oa', 'twitter-x']);

function mediaVersion(draft: PublicationEditorDraft): string {
  return JSON.stringify((draft.mediaReferences || []).filter(item => validPublicationMediaReference(item)).map(item => [
    item.actorId, item.projectId, item.artifactId, item.path, item.mime, item.size, item.revision, item.sha256, item.bundleSha256,
  ]));
}

/** Preserve unrelated fields, but an edited subject cannot reuse a fixed approval. */
export function editPublicationDraft(draft: PublicationEditorDraft, patch: Partial<PublicationEditorDraft>): PublicationEditorDraft {
  const next = { ...draft, ...patch };
  const subject = ({ approvalReference: _approval, ...content }: PublicationEditorDraft) => content;
  if (JSON.stringify(subject(draft)) !== JSON.stringify(subject(next))) next.approvalReference = undefined;
  return next;
}

export function applyPublicationHandoff(draft: PublicationEditorDraft, value: unknown): {
  draft: PublicationEditorDraft; status: 'invalid' | 'unchanged' | 'updated'; message: string;
} {
  const handoff = normalizePublicationHandoff(value);
  if (!handoff) return { draft, status: 'invalid', message: '此发布入口已过期或不属于当前用户 / IP，请回原对话重新选择。' };
  if (handoff.kind === 'approval') {
    const approvalReference = { actorId: handoff.actorId, projectId: handoff.projectId,
      approvalId: handoff.approvalId, payloadHash: handoff.payloadHash };
    if (JSON.stringify(draft.approvalReference) === JSON.stringify(approvalReference)) return { draft, status: 'unchanged', message: '' };
    // An approval belongs to its server-frozen subject, never to the previous editable draft.
    return { status: 'updated', message: '已载入固定版本确认，需核对服务端记录。', draft: {
      title: '', body: '', tags: '', platforms: [], overrides: {}, mediaReferences: [], mediaPaths: [], aiGenerated: false,
      approvalReference,
    } };
  }
  const platforms = handoff.copy.platform && PLATFORMS.has(handoff.copy.platform) ? [handoff.copy.platform] : [];
  const sameMedia = mediaVersion(draft) === mediaVersion({ ...draft, mediaReferences: handoff.media });
  if (sameMedia) {
    if (!platforms.length || JSON.stringify(platforms) === JSON.stringify(draft.platforms)) {
      return { draft, status: 'unchanged', message: '' };
    }
    return { draft: editPublicationDraft(draft, { platforms }), status: 'updated', message: '已切换目标平台，保留当前文案与媒体；请重新核对。' };
  }
  return { status: 'updated', message: '已载入新的成片版本，文案与媒体已替换，请重新核对。', draft: {
    title: handoff.copy.title || '', body: handoff.copy.body || '', tags: handoff.copy.tags || '', platforms,
    overrides: {}, mediaReferences: handoff.media, mediaPaths: [], aiGenerated: false, approvalReference: undefined,
  } };
}
