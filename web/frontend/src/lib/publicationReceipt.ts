/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Disclosure lifecycle adapted from deepseek-ai/deepseek-harness
 * packages/client/ui-workflow-run/src/client/WorkflowRunPanel.tsx
 * @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: publication receipt facts, scope/correlation checks and labels.
 * Notice: integrations/deepseek-harness/LICENSE.
 */
import type { PublicationApprovalRecord, PublicationApprovalSubject } from './publicationApproval';
import { validPublicationDigest, validPublicationSubject } from './publicationApproval';
import { ACTIVE_PROJECT_ID, ACTIVE_USER_ID } from './projectScope';

export type PublicationReceiptTone = 'neutral' | 'running' | 'warning' | 'error' | 'confirmed';
export type PublicationDisclosureMode = 'clean' | 'running' | 'abnormal';
export interface PublicationReceiptView {
  code: string;
  label: string;
  tone: PublicationReceiptTone;
  mode: PublicationDisclosureMode;
  publicConfirmed: boolean;
  note: string;
}
export interface PublicationDisclosureFacts {
  readonly mode: PublicationDisclosureMode;
  readonly activityCount: number;
}
export interface PublicationDisclosureState extends PublicationDisclosureFacts {
  readonly open: boolean;
  readonly pendingCleanCollapse: boolean;
}

const OPERATION = /^[a-f0-9]{32}$/;
const EXTERNAL_ID = /^[A-Za-z0-9][A-Za-z0-9_.+=-]{0,255}$/;
// Same-effect observation states accepted by PublicationApprovalStore.observe.
// A later ACK can resolve an unknown result without authorizing a new execution.
const OBSERVATION_STATES: Record<PublicationApprovalSubject['mode'], readonly string[]> = {
  publish_now: ['unknown', 'submitted', 'confirmed', 'failed'],
  scheduled: ['unknown', 'submitted', 'scheduled', 'confirmed', 'failed'],
  create_draft: ['unknown', 'draft_created', 'failed'],
  manual_export: ['unknown', 'exported', 'failed'],
};
const clean = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
  && value.length <= 512 && !Array.from(value).some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
const view = (code: string, label: string, tone: PublicationReceiptTone = 'neutral',
  mode: PublicationDisclosureMode = 'clean', note = '', publicConfirmed = false): PublicationReceiptView => (
  { code, label, tone, mode, note, publicConfirmed }
);

export function publicationReceiptInScope(record: PublicationApprovalRecord): boolean {
  return !!record && clean(record.id) && validPublicationDigest(record.payloadHash)
    && !!ACTIVE_USER_ID && record.actorId === ACTIVE_USER_ID && record.projectId === ACTIVE_PROJECT_ID
    && record.subject?.project?.id === record.projectId && validPublicationSubject(record.subject);
}

/** Fixed subject projection: a matching hash cannot authorize a changed caption or media version. */
function subjectBinding(subject: PublicationApprovalSubject): string {
  return JSON.stringify([subject.schemaVersion, subject.project.id, subject.project.name, subject.platform.id,
    subject.platform.name, subject.account.id, subject.account.name, subject.mode, subject.scheduledAt,
    subject.audience, subject.title, subject.body, subject.tags, subject.persona,
    subject.media.map(media => [media.artifactId, media.path, media.name, media.mime, media.revision,
      media.sha256, media.bundleSha256, media.size, media.url])]);
}

export function publicationReceiptIdentity(record: PublicationApprovalRecord): string {
  return JSON.stringify([record.actorId, record.projectId, record.id, record.payloadHash]);
}

function hasCorrelationMismatch(record: PublicationApprovalRecord): boolean {
  const result = record.result;
  return !!result && (['operationId', 'payloadHash', 'platform', 'mode', 'accountId'] as const).some(key => {
    const expected = ({ operationId: record.operationId, payloadHash: record.payloadHash,
      platform: record.subject.platform.id, mode: record.subject.mode, accountId: record.subject.account.id })[key];
    return Object.hasOwn(result, key) && result[key] !== undefined && result[key] !== expected;
  });
}

/** Only normalized platform evidence may produce the public-confirmed presentation. */
export function publicationReceiptView(record: PublicationApprovalRecord): PublicationReceiptView {
  if (!publicationReceiptInScope(record)) return view('restricted', '回执不属于当前用户或 IP', 'warning', 'abnormal');
  if (record.state !== 'approved') {
    const approval = ({ pending: '等待批准', rejected: '已拒绝', expired: '批准已过期',
      invalidated: '内容已变更，原批准已失效' } as Record<string, string>)[record.state] || '批准状态待核实';
    return record.operationId
      ? view(record.state, `${approval}，执行状态待核实`, 'warning', 'abnormal')
      : view(record.state, `${approval}，尚未执行`);
  }
  if (!record.operationId) return view('approved', '已批准，尚未执行', 'neutral', 'clean', '批准只针对当前固定版本，不代表已经提交或发布。');
  if (typeof record.operationId !== 'string' || !OPERATION.test(record.operationId)) return view('unknown', '执行编号待核实', 'warning', 'abnormal');
  const result = record.result;
  if (!result || hasCorrelationMismatch(record)) return view('unknown', '结果待核实', 'warning', 'abnormal', '尚未取得与此版本及操作一致的回执，请勿重复发布。');
  const status = result.status;
  if (status === 'executing') return view('executing', '正在执行', 'running', 'running', '尚未取得平台终态。');
  if (status === 'failed') return view('failed', '执行失败', 'error', 'abnormal',
    result.notStarted === true ? 'Host 已记录未启动平台提交。' : '请核对执行阶段与回执，不要把失败当作可以安全重发的依据。');
  if (status === 'draft_created' && record.subject.mode === 'create_draft') {
    return view(status, '草稿已创建，尚未公开', 'neutral', 'clean', '平台草稿与公开发布是不同结果。');
  }
  if (status === 'exported' && record.subject.mode === 'manual_export') {
    return view(status, '发布包已导出，尚未发布', 'neutral', 'clean', '导出不代表已经提交到社交平台。');
  }
  if (status === 'scheduled' && record.subject.mode === 'scheduled') {
    return view(status, '定时任务已安排，尚未确认公开', 'neutral', 'clean', '到达排期时间也需要平台公开回执。');
  }
  const publicAction = record.subject.audience === 'public'
    && (record.subject.mode === 'publish_now' || record.subject.mode === 'scheduled');
  if (status === 'submitted' && publicAction) return view(status, '已提交，等待公开回执', 'warning', 'clean', '平台接受提交不代表内容已经公开可见。');
  if (status === 'confirmed' && publicAction) {
    if (result.source === 'human' || result.evidenceSource === 'human' || result.evidenceMethod === 'human_declaration') {
      return view('human_declared', '人工声明已发布（未经平台自动验证）', 'warning', 'clean', '此结果是人工声明，未经平台自动验证。');
    }
    if (result.phase === 'readback' && result.evidenceMethod === 'authenticated_readback'
        && result.reason === 'public_readback_confirmed'
        && (result.identityVerification === undefined || result.identityVerification === 'authenticated_readback'
          || result.identityVerification === 'authenticated_submission')
        && ['source', 'evidenceSource'].every(key => result[key] === undefined
          || ['platform', 'provider_response', 'authenticated_readback'].some(source => source === result[key]))
        && result.publicationState === 'public' && clean(result.externalId) && EXTERNAL_ID.test(result.externalId)
        && !/^0+$/.test(result.externalId) && result.operationId === record.operationId
        && result.payloadHash === record.payloadHash && result.platform === record.subject.platform.id
        && result.mode === record.subject.mode && result.accountId === record.subject.account.id) {
      return view('confirmed', '平台已确认公开发布', 'confirmed', 'clean', '平台登录账号的独立读回已确认此作品公开可见。', true);
    }
  }
  return view('unknown', '结果待核实', 'warning', 'abnormal', '尚无足够证据确认公开发布；请核对平台，勿重复提交。');
}

/** A refresh is a read of this exact object; it cannot rebind to another operation or roll back a terminal result. */
export function publicationReceiptAccepts(current: PublicationApprovalRecord, next: PublicationApprovalRecord): boolean {
  if (!publicationReceiptInScope(current) || !publicationReceiptInScope(next)
      || publicationReceiptIdentity(current) !== publicationReceiptIdentity(next)
      || subjectBinding(current.subject) !== subjectBinding(next.subject)
      || current.operationId && current.operationId !== next.operationId || hasCorrelationMismatch(next)) return false;
  const invalidatedBeforeExecution = current.state === 'approved' && !current.operationId
    && next.state === 'invalidated' && !next.operationId;
  if (current.state !== 'pending' && current.state !== next.state && !invalidatedBeforeExecution) return false;
  const before = current.result?.status;
  const after = next.result?.status;
  if (before === 'unknown') {
    return current.state === 'approved' && next.state === 'approved'
      && typeof current.operationId === 'string' && OPERATION.test(current.operationId)
      && typeof after === 'string' && OBSERVATION_STATES[current.subject.mode].includes(after);
  }
  if (before && ['confirmed', 'failed', 'draft_created', 'exported'].includes(before)) return after === before;
  if (before && before !== 'executing' && after === 'executing') return false;
  return true;
}

export function publicationReceiptFacts(record: PublicationApprovalRecord): PublicationDisclosureFacts {
  return { mode: publicationReceiptView(record).mode, activityCount: record.operationId ? 1 : 0 };
}

// Lifecycle copied from the fixed upstream WorkflowRunPanel: updates preserve a
// reader's choice, abnormal edges open once, and clean completion waits for focus.
export function initialPublicationDisclosure(facts: PublicationDisclosureFacts): PublicationDisclosureState {
  return { ...facts, open: facts.mode !== 'clean', pendingCleanCollapse: false };
}
export function advancePublicationDisclosure(current: PublicationDisclosureState,
  facts: PublicationDisclosureFacts, focusWithin: boolean): PublicationDisclosureState {
  const sameFacts = current.mode === facts.mode && current.activityCount === facts.activityCount;
  if (sameFacts) {
    if (!current.pendingCleanCollapse || focusWithin) return current;
    return { ...current, open: false, pendingCleanCollapse: false };
  }
  if (facts.mode === 'clean') {
    const deferCollapse = current.open && focusWithin;
    return { ...facts, open: deferCollapse, pendingCleanCollapse: deferCollapse };
  }
  if (current.mode === 'clean' || (facts.mode === 'abnormal' && current.mode !== 'abnormal')) {
    return { ...facts, open: true, pendingCleanCollapse: false };
  }
  return { ...facts, open: current.open, pendingCleanCollapse: false };
}
export function collapsePublicationDisclosure(current: PublicationDisclosureState): PublicationDisclosureState {
  if (!current.pendingCleanCollapse) return current;
  return { ...current, open: false, pendingCleanCollapse: false };
}
