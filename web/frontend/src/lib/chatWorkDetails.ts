/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Preference values adapted from ui-chat/src/chat-settings.ts
 * @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Xiangya stores the offered mode per actor/IP. Compact details stay folded
 * from the first observation; old completion/next-input timing is retired.
 */
import { projectStorageKey } from './projectScope';

export type WorkDetailsMode = 'compact' | 'detailed';
export interface WorkDetailsPreferences {
  mode: WorkDetailsMode;
}

export const DEFAULT_WORK_DETAILS_PREFERENCES: Readonly<WorkDetailsPreferences> = {
  mode: 'compact',
};

const storageKey = projectStorageKey('easel_chat_work_details');

export function loadWorkDetailsPreferences(): WorkDetailsPreferences {
  try {
    const value: Partial<WorkDetailsPreferences> | null = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
    const mode: unknown = value?.mode;
    return {
      mode: mode === 'detailed' || mode === 'normal' || mode === 'expanded' ? 'detailed' : 'compact',
    };
  } catch {
    return { ...DEFAULT_WORK_DETAILS_PREFERENCES };
  }
}

export function saveWorkDetailsPreferences(preferences: WorkDetailsPreferences): boolean {
  const { mode } = preferences;
  if (mode !== 'compact' && mode !== 'detailed') return false;
  try {
    localStorage.setItem(storageKey, JSON.stringify({ mode }));
    return true;
  } catch {
    return false;
  }
}
