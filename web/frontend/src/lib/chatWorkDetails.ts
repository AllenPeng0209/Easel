/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Preference values adapted from ui-chat/src/chat-settings.ts
 * @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Xiangya stores the two offered modes and independent fold timing per actor/IP.
 */
import { projectStorageKey } from './projectScope';

export type WorkDetailsMode = 'compact' | 'detailed';
export type WorkDetailsCollapseTiming = 'completion' | 'next-input';

export interface WorkDetailsPreferences {
  mode: WorkDetailsMode;
  collapseTiming: WorkDetailsCollapseTiming;
}

export const DEFAULT_WORK_DETAILS_PREFERENCES: Readonly<WorkDetailsPreferences> = {
  mode: 'compact',
  collapseTiming: 'completion',
};

const storageKey = projectStorageKey('easel_chat_work_details');

export function loadWorkDetailsPreferences(): WorkDetailsPreferences {
  try {
    const value: Partial<WorkDetailsPreferences> | null = JSON.parse(localStorage.getItem(storageKey) ?? 'null');
    const mode: unknown = value?.mode;
    return {
      mode: mode === 'detailed' || mode === 'normal' || mode === 'expanded' ? 'detailed' : 'compact',
      collapseTiming: value?.collapseTiming === 'next-input' ? 'next-input' : 'completion',
    };
  } catch {
    return { ...DEFAULT_WORK_DETAILS_PREFERENCES };
  }
}

export function saveWorkDetailsPreferences(preferences: WorkDetailsPreferences): boolean {
  const { mode, collapseTiming } = preferences;
  if ((mode !== 'compact' && mode !== 'detailed') ||
      (collapseTiming !== 'completion' && collapseTiming !== 'next-input')) return false;
  try {
    localStorage.setItem(storageKey, JSON.stringify({ mode, collapseTiming }));
    return true;
  } catch {
    return false;
  }
}
