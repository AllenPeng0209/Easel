/** A file chip in the visible draft. Offsets are half-open UTF-16 indices. */
export interface ChatReference {
  start: number;
  end: number;
  attachmentId: string;
}

export const MAX_CHAT_REFERENCES = 100;

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

/** Trim surrounding text without splitting any validated reference label. */
export function trimChatReferenceText(
  text: string, references?: readonly ChatReference[],
): { text: string; references?: ChatReference[] } {
  if (!references?.length) return { text: text.trim() };
  const start = Math.min(text.length - text.trimStart().length, references[0].start);
  const end = Math.max(text.trimEnd().length, references[references.length - 1].end);
  return { text: text.slice(start, end),
    references: references.map(ref => ({ ...ref, start: ref.start - start, end: ref.end - start })) };
}
