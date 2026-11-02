/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Source: packages/client/ui-deliverables/src/presented.ts
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: local dependency/type bridges; all file access remains on
 * Easel authenticated artifact routes. See integrations/deepseek-harness/LICENSE.
 */
export interface PresentedFile { path: string; description?: string }
export interface PresentedHost { name: string; available: boolean; fileManager: 'finder' | 'explorer' | 'directory' | null }
export function isPresentedFile(value: unknown): value is PresentedFile {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const { path, description } = value as Record<string, unknown>
  return typeof path === 'string' && path.trim().length > 0
    && (description === undefined || typeof description === 'string')
}

export function basename(path: string): string {
  const at = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return at === -1 ? path : path.slice(at + 1)
}
