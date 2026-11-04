/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Source: packages/client/ui-deliverables/src/client/turn-deliverables.ts
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: local dependency/type bridges; all file access remains on
 * Easel authenticated artifact routes. See integrations/deepseek-harness/LICENSE.
 */
import type { PresentedFile } from './presented';
export interface PresentedPath extends PresentedFile { readonly seq: number; readonly index: number }
// Easel supplies one validated turn's declarations instead of the Cordis owner.
export function presentedForClosing<T extends PresentedFile>(presented: readonly T[]): T[] {
  const files = new Map<string, T>()
  for (const file of presented) files.set(file.path, file)
  return [...files.values()]
}

export { basename } from './presented';
