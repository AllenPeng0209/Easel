/** Host-owned sending attempts adapted from DeepSeek Harness input/machine.ts
 * (MIT, ce7073d). The original draft remains editable until a matching ACK.
 */
import { projectStorageKey } from './projectScope';
import type { UploadedFile } from './api';
import type { ChatMessage, ChatSession } from './store';
import { trimChatReferenceText, type ChatReference } from './chatReferences';

export interface PromptAttempt {
  turnId: string;
  state: 'sending' | 'unconfirmed' | 'accepted';
  wireText: string;
  persona?: string;
  message: ChatMessage;
  composer: boolean;
  truncateAt?: number;
  baseline?: string;
}

const memory = new Map<string, PromptAttempt>();
const keyFor = (sessionId: string) => projectStorageKey(`easel_prompt_attempt:${sessionId}`);

export function loadPromptAttempt(sessionId: string): PromptAttempt | undefined {
  const key = keyFor(sessionId);
  if (memory.has(key)) return memory.get(key);
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null') as PromptAttempt | null;
    if (value && typeof value.turnId === 'string' && /^[A-Za-z0-9_.-]{1,160}$/.test(value.turnId)
        && ['sending', 'unconfirmed', 'accepted'].includes(value.state)
        && typeof value.wireText === 'string' && typeof value.composer === 'boolean'
        && value.message?.role === 'user' && typeof value.message.content === 'string'
        && (value.message.attachments === undefined || Array.isArray(value.message.attachments))) return value;
  } catch { /* Storage is optional for an open tab. */ }
  return undefined;
}

export function savePromptAttempt(sessionId: string, attempt: PromptAttempt): void {
  const key = keyFor(sessionId);
  try {
    localStorage.setItem(key, JSON.stringify(attempt));
    memory.delete(key);
  } catch { memory.set(key, attempt); }
}

export function forgetPromptAttempt(sessionId: string, turnId: string): void {
  if (loadPromptAttempt(sessionId)?.turnId !== turnId) return;
  const key = keyFor(sessionId);
  memory.delete(key);
  try {
    const saved = JSON.parse(localStorage.getItem(key) || 'null');
    if (saved?.turnId === turnId) localStorage.removeItem(key);
  } catch { /* Ignore unavailable storage. */ }
}

const fileIdentity = (files: UploadedFile[] = []) => files.map(file => [file.id, file.name, file.path]);

export function sameComposerDraft(attempt: PromptAttempt, text: string, files: UploadedFile[], references?: ChatReference[]): boolean {
  const trimmed = trimChatReferenceText(text, references);
  return attempt.composer && attempt.message.content === trimmed.text
    && JSON.stringify(attempt.message.references || []) === JSON.stringify(trimmed.references || [])
    && JSON.stringify(fileIdentity(attempt.message.attachments)) === JSON.stringify(fileIdentity(files));
}

function promptIdentity(attempt: PromptAttempt): string {
  const { topicRef, workshopRef, attachments } = attempt.message;
  return JSON.stringify([attempt.wireText, attempt.persona || null, attempt.message.content,
    fileIdentity(attachments), attempt.message.references || [], topicRef ? [topicRef.id, topicRef.snapshotHash] : null,
    workshopRef ? [workshopRef.id, workshopRef.revision, workshopRef.engineRevision,
      workshopRef.artifactId, workshopRef.artifactSha256] : null, attempt.truncateAt ?? null]);
}

export function samePromptAttempt(first: PromptAttempt, second: PromptAttempt): boolean {
  return promptIdentity(first) === promptIdentity(second);
}

export function applyPromptAdmission(session: ChatSession, attempt: PromptAttempt): ChatSession {
  if (session.messages.some(message => (message as ChatMessage & { clientTurnId?: string }).clientTurnId === attempt.turnId)) {
    return session;
  }
  const truncate = attempt.truncateAt !== undefined && Number.isSafeInteger(attempt.truncateAt)
    && attempt.truncateAt >= 0 && attempt.baseline === JSON.stringify(session.messages.slice(attempt.truncateAt));
  const base = truncate ? session.messages.slice(0, attempt.truncateAt) : session.messages;
  const message = { ...attempt.message, clientTurnId: attempt.turnId };
  return { ...session, messages: [...base, message] };
}
