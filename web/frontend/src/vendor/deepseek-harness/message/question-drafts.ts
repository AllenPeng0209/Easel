/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Source: packages/client/ui-user-questions/src/client/draft-store.ts
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: replace Cordis defineStore/persistence with the existing
 * user+IP projectStorageKey adapter; retain request-keyed draft actions/model.
 * See integrations/deepseek-harness/LICENSE.
 */
import { projectStorageKey } from '../../../lib/projectScope';

export interface QuestionDraftAnswer { selected: string[]; custom: string; skipped: boolean }
export interface QuestionDraftProgress { index: number; drafts: QuestionDraftAnswer[]; wait?: 'editing' | 'waiting' }
interface QuestionDraftState { progressByRequest: Record<string, QuestionDraftProgress> }

export function createQuestionDraftStore(sessionId: string) {
  const key = projectStorageKey(`easel_question_drafts:${sessionId}`);
  const read = (): QuestionDraftState => {
    try {
      const value = JSON.parse(localStorage.getItem(key) || '{}');
      return value && typeof value.progressByRequest === 'object' && value.progressByRequest !== null
        ? value : { progressByRequest: {} };
    } catch { return { progressByRequest: {} }; }
  };
  const write = (draft: QuestionDraftState) => {
    try { localStorage.setItem(key, JSON.stringify(draft)); } catch { /* Drafts stay usable in memory. */ }
  };
  return {
    get(requestKey: string): QuestionDraftProgress | undefined {
      const progress = read().progressByRequest[requestKey];
      return progress && Array.isArray(progress.drafts) && progress.drafts.every(item => item
        && Array.isArray(item.selected) && item.selected.every(value => typeof value === 'string')
        && typeof item.custom === 'string') ? progress : undefined;
    },
    replace(requestKey: string, progress: QuestionDraftProgress) {
      const draft = read();
      draft.progressByRequest[requestKey] = progress;
      write(draft);
    },
    clear(requestKey: string) {
      const draft = read();
      draft.progressByRequest = Object.fromEntries(
        Object.entries(draft.progressByRequest).filter(([key]) => key !== requestKey),
      );
      write(draft);
    },
    prune(keep: readonly string[]) {
      const draft = read();
      const live = new Set(keep);
      draft.progressByRequest = Object.fromEntries(
        Object.entries(draft.progressByRequest).filter(([key]) => live.has(key)),
      );
      write(draft);
    },
  };
}
