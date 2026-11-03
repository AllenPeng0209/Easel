/** Exact, current actor/IP/session review references for the plan surface. */
import { ACTIVE_PROJECT_ID, ACTIVE_USER_ID, projectFetch } from './projectScope';

export interface ChatPlanReference { planId: string; revision: number; digest: string }
export interface ChatPlanRecord extends ChatPlanReference {
  text: string;
  state: 'proposed' | 'approved' | 'claimed' | 'superseded';
  actorId: string;
  projectId: string;
  sessionId: string;
}
export interface ChatPlanRoster { plans: ChatPlanRecord[]; latest: ChatPlanRecord | null }

const states = new Set(['proposed', 'approved', 'claimed', 'superseded']);

export function chatPlanReference({ planId, revision, digest }: ChatPlanReference): ChatPlanReference {
  return { planId, revision, digest };
}

export function chatPlanKey(plan: ChatPlanReference): string {
  return JSON.stringify([plan.planId, plan.revision, plan.digest]);
}

function parsePlan(value: unknown, sessionId: string): ChatPlanRecord {
  if (!value || typeof value !== 'object') throw new Error('Invalid plan');
  const plan = value as Record<string, unknown>;
  if (!ACTIVE_USER_ID || plan.actorId !== ACTIVE_USER_ID || plan.projectId !== ACTIVE_PROJECT_ID || plan.sessionId !== sessionId
      || typeof plan.planId !== 'string' || !/^[a-f0-9]{32}$/.test(plan.planId)
      || typeof plan.revision !== 'number' || !Number.isSafeInteger(plan.revision) || plan.revision < 1
      || typeof plan.digest !== 'string' || !/^[a-f0-9]{64}$/.test(plan.digest)
      || typeof plan.text !== 'string' || !plan.text.trim() || typeof plan.state !== 'string' || !states.has(plan.state)) {
    throw new Error('Invalid plan');
  }
  return { planId: plan.planId, revision: plan.revision, digest: plan.digest, text: plan.text,
    state: plan.state as ChatPlanRecord['state'], actorId: ACTIVE_USER_ID, projectId: ACTIVE_PROJECT_ID, sessionId };
}

export class ChatPlanReadError extends Error {
  readonly status: number;
  constructor(status: number) {
    super('Plan request did not complete');
    this.status = status;
  }
}

async function requestPlan(sessionId: string, signal: AbortSignal, body?: unknown, action = ''): Promise<unknown> {
  const url = `/api/chat/plans/${encodeURIComponent(sessionId)}${action}`;
  const response = await projectFetch(url, body === undefined ? { signal, cache: 'no-store' } : {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new ChatPlanReadError(response.status);
  return response.json();
}

export async function fetchChatPlans(sessionId: string, signal: AbortSignal): Promise<ChatPlanRoster> {
  const value = await requestPlan(sessionId, signal);
  if (!value || typeof value !== 'object' || !('plans' in value) || !Array.isArray(value.plans)
      || !('latest' in value)) throw new Error('Invalid plan roster');
  const plans = value.plans.map(plan => parsePlan(plan, sessionId)).sort((a, b) => b.revision - a.revision);
  const latest = value.latest === null ? null : parsePlan(value.latest, sessionId);
  if (new Set(plans.map(plan => plan.revision)).size !== plans.length ||
      (latest ? !plans[0] || chatPlanKey(plans[0]) !== chatPlanKey(latest) ||
        plans[0].state !== latest.state || plans[0].text !== latest.text : plans.length !== 0)) {
    throw new Error('Invalid latest plan');
  }
  return { plans, latest };
}

export async function saveChatPlan(sessionId: string, draft: { brief: string } | { text: string },
  base: ChatPlanReference | null, signal: AbortSignal): Promise<ChatPlanRecord> {
  const plan = parsePlan(await requestPlan(sessionId, signal, { ...draft, ...(base && chatPlanReference(base)) }), sessionId);
  if (plan.state !== 'proposed' || (base ? plan.planId !== base.planId || plan.revision <= base.revision : plan.revision !== 1)) {
    throw new Error('Invalid draft result');
  }
  return plan;
}

export async function approveChatPlan(sessionId: string, exact: ChatPlanRecord, signal: AbortSignal): Promise<ChatPlanRecord> {
  const plan = parsePlan(await requestPlan(sessionId, signal, chatPlanReference(exact), '/approve'), sessionId);
  if (plan.state !== 'approved' || chatPlanKey(plan) !== chatPlanKey(exact) || plan.text !== exact.text) {
    throw new Error('Invalid approval result');
  }
  return plan;
}
