/** Session-owned snapshots, matching Harness draft persistence ownership.
 * Integration adapter for Easel's user/IP storage scope and REST attachment refs.
 * Upstream: packages/client/ui-conversation/src/client/draft.ts, MIT,
 * ce7073d51254c26cd63b22a60835b8f948deb447. Raw File bytes are never persisted.
 */
import { projectStorageKey } from './projectScope';
import { validateChatReferences, type ChatReference } from './chatReferences';
import type { UploadAttachment } from './uploadQueue';

export interface InterruptedFile { name: string; size: number; mime: string; }
export interface ChatDraft {
  text: string;
  attachments: UploadAttachment[];
  interruptedFiles: InterruptedFile[];
  references?: ChatReference[];
}

const empty = (): ChatDraft => ({ text: '', attachments: [], interruptedFiles: [] });
const keyFor = (sessionId: string) => projectStorageKey(`easel_chat_draft:${sessionId}`);

export function loadChatDraft(sessionId: string): ChatDraft {
  try {
    const value = JSON.parse(localStorage.getItem(keyFor(sessionId)) || 'null');
    if (!value || typeof value !== 'object' || typeof value.text !== 'string') return empty();
    const attachments: UploadAttachment[] = [];
    for (const candidate of Array.isArray(value.attachments) ? value.attachments : []) {
      if (!candidate || typeof candidate.key !== 'string' || typeof candidate.name !== 'string') continue;
      const ref = candidate.reference;
      if (candidate.status !== 'ready' || !ref || typeof ref.id !== 'string' || typeof ref.path !== 'string'
        || typeof ref.name !== 'string') continue;
      attachments.push({ key: candidate.key, name: candidate.name, size: Number(candidate.size) || 0,
        mime: typeof candidate.mime === 'string' ? candidate.mime : '', status: 'ready',
        reference: { id: ref.id, path: ref.path, name: ref.name, size: Number(candidate.size) || 0,
          mime: typeof candidate.mime === 'string' ? candidate.mime : '' } });
    }
    const interruptedFiles = (Array.isArray(value.interruptedFiles) ? value.interruptedFiles : [])
      .filter((file: unknown): file is InterruptedFile => !!file && typeof file === 'object'
        && typeof (file as InterruptedFile).name === 'string')
      .map((file: InterruptedFile) => ({ name: file.name, size: Number(file.size) || 0,
        mime: typeof file.mime === 'string' ? file.mime : '' }));
    const references = validateChatReferences(value.text, value.references, attachments.map(item => item.reference!));
    return { text: value.text, attachments, interruptedFiles, ...(references?.length ? { references } : {}) };
  } catch { return empty(); }
}

/** Persist only complete receipts; unfinished file metadata becomes a reselect notice. */
export function saveChatDraft(sessionId: string, draft: ChatDraft, composerId?: string): boolean {
  try {
    const attachments = draft.attachments.filter(item => item.status === 'ready' && item.reference)
      .map(item => ({ key: item.key, name: item.name, size: item.size, mime: item.mime, status: 'ready',
        reference: { id: item.reference!.id, name: item.reference!.name, path: item.reference!.path } }));
    const interruptedFiles = [...draft.interruptedFiles, ...draft.attachments
      .filter(item => item.status !== 'ready').map(item => ({ name: item.name, size: item.size, mime: item.mime }))];
    const references = validateChatReferences(draft.text, draft.references, attachments.map(item => item.reference));
    if (!draft.text && !attachments.length && !interruptedFiles.length) localStorage.removeItem(keyFor(sessionId));
    else localStorage.setItem(keyFor(sessionId), JSON.stringify({ version: 1, composerId, text: draft.text, attachments, interruptedFiles,
      ...(references?.length ? { references } : {}) }));
    return true;
  } catch { return false; }
}

export function chatDraftOwner(sessionId: string): string | undefined {
  try {
    const stored = JSON.parse(localStorage.getItem(keyFor(sessionId)) || 'null');
    return typeof stored?.composerId === 'string' ? stored.composerId : undefined;
  } catch { return undefined; }
}

/** Consume the admitted text and receipts only while this composer owns the saved draft. */
export function consumeChatDraft(sessionId: string, composerId: string, reminders?: InterruptedFile[]): boolean {
  try {
    const key = keyFor(sessionId);
    const stored = JSON.parse(localStorage.getItem(key) || 'null');
    if (!stored || stored.composerId !== composerId) return true;
    const interruptedFiles = reminders ?? (Array.isArray(stored.interruptedFiles) ? stored.interruptedFiles : []);
    if (interruptedFiles.length) {
      localStorage.setItem(key, JSON.stringify({ version: 1, composerId, text: '', attachments: [], interruptedFiles }));
    } else {
      localStorage.removeItem(key);
    }
    return true;
  } catch { return false; }
}

export function clearChatDraft(sessionId: string): void {
  try { localStorage.removeItem(keyFor(sessionId)); } catch { /* Next successful save can retry cleanup. */ }
}
