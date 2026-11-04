import { projectFetch } from './projectScope';
import type { UploadAttachment } from './uploadQueue';

const BATCH_SIZE = 100;

/** Check restored receipts without opening or downloading the files. */
export async function validateRestoredAttachments(sessionId: string, items: UploadAttachment[], signal: AbortSignal) {
  const invalid: UploadAttachment[] = [];
  for (let start = 0; start < items.length; start += BATCH_SIZE) {
    signal.throwIfAborted();
    const batch = items.slice(start, start + BATCH_SIZE);
    const attachments = batch.map(({ reference }) => ({
      id: reference?.id, path: reference?.path, name: reference?.name,
    }));
    const response = await projectFetch('/api/upload/validate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId, attachments }), signal,
    });
    if (!response.ok) throw new Error('暂时无法核验恢复的附件');
    const result = await response.json() as { valid?: unknown };
    const valid = result?.valid;
    if (!Array.isArray(valid) || valid.length !== batch.length || valid.some(item => typeof item !== 'boolean')) {
      throw new Error('附件核验回执无效');
    }
    for (const [index, item] of batch.entries()) if (!valid[index]) invalid.push(item);
  }
  return invalid;
}
