/** A file chip in the visible draft. Offsets are half-open UTF-16 indices. */
export interface ChatReference {
  start: number;
  end: number;
  attachmentId: string;
}

export const MAX_CHAT_REFERENCES = 100;
export const MAX_CHAT_SESSION_REFERENCES = 3;

export interface ChatSessionReference {
  start: number;
  end: number;
  sessionId: string;
  snapshotHash: string;
}

/** Display label from the host's preview, kept only in the local editor and draft. */
export interface ChatDraftSessionReference extends ChatSessionReference {
  label: string;
}

type AttachmentLabel = { id: string; name: string };

export function attachmentReferenceLabel(attachment: Pick<AttachmentLabel, 'name'>): string {
  return `@${attachment.name}`;
}

/** Accept only ordered, exact labels backed by the supplied attachment receipts. */
export function validateChatReferences(
  text: string, value: unknown, attachments: readonly AttachmentLabel[],
): ChatReference[] | null {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > MAX_CHAT_REFERENCES) return null;
  if (value.length === 0) return [];

  const labels = new Map<string, string | null>();
  for (const attachment of attachments) {
    const label = attachmentReferenceLabel(attachment);
    labels.set(attachment.id, labels.has(attachment.id) && labels.get(attachment.id) !== label ? null : label);
  }

  const references: ChatReference[] = [];
  let previousEnd = 0;
  for (const item of value) {
    if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).length !== 3) return null;
    const { start, end, attachmentId } = item as Record<string, unknown>;
    if (typeof start !== 'number' || !Number.isSafeInteger(start) || start < previousEnd ||
        typeof end !== 'number' || !Number.isSafeInteger(end) || end <= start || end > text.length ||
        typeof attachmentId !== 'string' || !attachmentId) return null;
    const label = labels.get(attachmentId);
    if (label === undefined || label === null || text.slice(start, end) !== label) return null;
    references.push({ start, end, attachmentId });
    previousEnd = end;
  }
  return references;
}

export function sessionReferenceLabel(label: string): string {
  return `@${label}`;
}

function readSessionReferences(
  text: string, value: unknown, files: readonly ChatReference[], draft: boolean,
): ChatDraftSessionReference[] | null {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > MAX_CHAT_SESSION_REFERENCES) return null;

  const references: ChatDraftSessionReference[] = [];
  let previousEnd = 0;
  let fileIndex = 0;
  for (const item of value) {
    if (!item || typeof item !== 'object' || Array.isArray(item) ||
        Object.keys(item).length !== (draft ? 5 : 4)) return null;
    const { start, end, sessionId, snapshotHash, label: storedLabel } = item as Record<string, unknown>;
    if (typeof start !== 'number' || !Number.isSafeInteger(start) || start < previousEnd ||
        typeof end !== 'number' || !Number.isSafeInteger(end) || end <= start + 1 || end > text.length ||
        typeof sessionId !== 'string' || !sessionId.trim() || [...sessionId].length > 256 ||
        /[\p{Cc}\p{Cs}]/u.test(sessionId) ||
        typeof snapshotHash !== 'string' || !/^[a-f0-9]{64}$/.test(snapshotHash)) return null;

    const label = text.slice(start + 1, end);
    // Reject controls and lone surrogate halves, including spans ending inside a character.
    if (text[start] !== '@' || !label.trim() || [...label].length > 120 ||
        /[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(label) || (draft && storedLabel !== label)) return null;
    while (fileIndex < files.length && files[fileIndex].end <= start) fileIndex += 1;
    if (files[fileIndex] && files[fileIndex].start < end) return null;
    references.push({ start, end, sessionId, snapshotHash, label });
    previousEnd = end;
  }
  return references;
}

export function validateChatSessionReferences(
  text: string, value: unknown, fileReferences: readonly ChatReference[] = [],
): ChatDraftSessionReference[] | null {
  return readSessionReferences(text, value, fileReferences, true);
}

export function toWireSessionReferences(references?: readonly ChatSessionReference[]): ChatSessionReference[] | undefined {
  return references?.map(({ start, end, sessionId, snapshotHash }) => ({ start, end, sessionId, snapshotHash }));
}

/** Check history spans before display. The backend owns snapshot and title authorization. */
export function validateChatSessionReferenceSpans(
  text: string, value: unknown, fileReferences: readonly ChatReference[] = [],
): ChatSessionReference[] | null {
  const checked = readSessionReferences(text, value, fileReferences, false);
  return checked === null ? null : toWireSessionReferences(checked)!;
}

/** Trim surrounding text without splitting any validated reference label. */
export function trimChatReferenceText<T extends ChatSessionReference = ChatSessionReference>(
  text: string, references?: readonly ChatReference[], sessionReferences?: readonly T[],
): { text: string; references?: ChatReference[]; sessionReferences?: T[] } {
  const start = Math.min(text.length - text.trimStart().length,
    references?.[0]?.start ?? Infinity, sessionReferences?.[0]?.start ?? Infinity);
  const end = Math.max(text.trimEnd().length, references?.at(-1)?.end ?? 0, sessionReferences?.at(-1)?.end ?? 0);
  const rebase = <T extends { start: number; end: number }>(ref: T): T =>
    ({ ...ref, start: ref.start - start, end: ref.end - start });
  return { text: text.slice(start, end),
    ...(references?.length ? { references: references.map(rebase) } : {}),
    ...(sessionReferences?.length ? { sessionReferences: sessionReferences.map(rebase) } : {}) };
}
