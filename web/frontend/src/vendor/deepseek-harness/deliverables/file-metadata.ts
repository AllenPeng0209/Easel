/** Copyright (c) 2026 DeepSeek. MIT. fileExtension copied from ui-primitives/src/FileTypeIcon.tsx @ ce7073d. */
import { basename } from './presented';
export function fileExtension(path: string): string {
  const name = basename(path)
  const dot = name.lastIndexOf('.')
  return dot < 0 ? '' : name.slice(dot + 1)
}

/** Easel exposes only already-validated outputs-relative titles, never host cwd. */
export function resolveWorkspacePath(_cwd: string | undefined, path: string): string { return path; }
