/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Line numbering from packages/client/ui-deliverables/src/client/FileDiff.tsx,
 * ce7073d51254c26cd63b22a60835b8f948deb447. See integrations/deepseek-harness/LICENSE.
 * Easel supplies validated engine-reported hunks, with a bounded text fallback.
 */

const MAX_DIFF_BYTES = 64 * 1024;
const MAX_RENDERED_LINES = 5000;
const ENGINE_TRUNCATION = '\n[truncated: remaining content omitted]';
const NO_NEWLINE = '\\ No newline at end of file';
const HUNK_HEADER = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$/u;

export interface DiffRow {
  kind: 'add' | 'del' | 'context'
  old: number | undefined
  new: number | undefined
  text: string
}

interface WorkspaceDiffHunk {
  oldStart: number;
  newStart: number;
  lines: string[];
}

interface ProjectedHunk {
  header: string;
  rows: DiffRow[];
}

export type ToolDiffProjection =
  | { kind: 'missing' }
  | { kind: 'raw'; reason: 'unstructured' | 'malformed' | 'truncated'; text: string }
  | { kind: 'unified'; hunks: ProjectedHunk[]; added: number; removed: number; text: string };

/** Upstream projection of a hunk already validated by its Host. */
export function hunkRows(hunk: WorkspaceDiffHunk): DiffRow[] {
  let oldNo = hunk.oldStart
  let newNo = hunk.newStart
  return hunk.lines.map((line) => {
    const text = line.slice(1)
    switch (line[0]) {
      case '+': return { kind: 'add', old: undefined, new: newNo++, text }
      case '-': return { kind: 'del', old: oldNo++, new: undefined, text }
      default: return { kind: 'context', old: oldNo++, new: newNo++, text }
    }
  })
}

function validPreamble(lines: string[], firstHunk: number): boolean {
  let index = lines[0].startsWith('diff --git ') ? 1 : 0;
  while (index < firstHunk && /^(?:index |new file mode |deleted file mode |old mode |new mode |similarity index |dissimilarity index |rename from |rename to |copy from |copy to )/.test(lines[index])) {
    index += 1;
  }
  if (lines[index]?.startsWith('--- ') && lines[index + 1]?.startsWith('+++ ')) index += 2;
  return index === firstHunk;
}

function projectHunks(lines: string[], firstHunk: number): ProjectedHunk[] | null {
  if (!validPreamble(lines, firstHunk)) return null;
  const hunks: ProjectedHunk[] = [];
  let index = firstHunk;
  while (index < lines.length) {
    const header = lines[index++];
    const match = HUNK_HEADER.exec(header);
    if (!match) return null;
    const oldStart = Number(match[1]);
    const newStart = Number(match[3]);
    let oldLeft = Number(match[2] ?? 1);
    let newLeft = Number(match[4] ?? 1);
    if (![oldStart, newStart, oldLeft, newLeft, oldStart + oldLeft, newStart + newLeft].every(Number.isSafeInteger) ||
        oldLeft > MAX_RENDERED_LINES || newLeft > MAX_RENDERED_LINES ||
        (oldStart === 0 && oldLeft !== 0) || (newStart === 0 && newLeft !== 0) ||
        oldLeft + newLeft === 0) return null;

    const body: string[] = [];
    const content: string[] = [];
    while (index < lines.length && !lines[index].startsWith('@@')) {
      const line = lines[index++];
      if (line === NO_NEWLINE) {
        if (!body.length || body[body.length - 1] === NO_NEWLINE) return null;
      } else {
        const prefix = line[0];
        if (prefix !== ' ' && prefix !== '+' && prefix !== '-') return null;
        if (prefix !== '+') oldLeft -= 1;
        if (prefix !== '-') newLeft -= 1;
        if (oldLeft < 0 || newLeft < 0) return null;
        content.push(line);
      }
      body.push(line);
    }
    if (oldLeft !== 0 || newLeft !== 0) return null;
    const numbered = hunkRows({ oldStart, newStart, lines: content });
    let rowIndex = 0;
    const rows = body.map(line => line === NO_NEWLINE
      ? { kind: 'context' as const, old: undefined, new: undefined, text: line }
      : numbered[rowIndex++]);
    hunks.push({ header, rows });
  }
  return hunks;
}

/** Project a single file's reported unified diff without reading or patching files. */
export function projectUnifiedDiff(value: unknown): ToolDiffProjection {
  if (typeof value !== 'string' || value === '') return { kind: 'missing' };
  // Bound encoding work before converting the string to bytes.
  const candidate = value.slice(0, MAX_DIFF_BYTES);
  const bytes = new TextEncoder().encode(candidate);
  const text = bytes.length > MAX_DIFF_BYTES
    ? new TextDecoder().decode(bytes.subarray(0, MAX_DIFF_BYTES), { stream: true })
    : candidate;
  const rawLines = text.split('\n');
  if (rawLines[rawLines.length - 1] === '') rawLines.pop();
  if (rawLines.length > MAX_RENDERED_LINES) {
    return { kind: 'raw', reason: 'truncated', text: rawLines.slice(0, MAX_RENDERED_LINES).join('\n') };
  }
  if (value.length > candidate.length || bytes.length > MAX_DIFF_BYTES || text.endsWith(ENGINE_TRUNCATION)) {
    return { kind: 'raw', reason: 'truncated', text };
  }
  if (!text.trim()) return { kind: 'missing' };

  const lines = rawLines.map(line => line.replace(/\r$/u, ''));
  const firstHunk = lines.findIndex(line => line.startsWith('@@'));
  if (firstHunk === -1) return { kind: 'raw', reason: 'unstructured', text };
  const hunks = projectHunks(lines, firstHunk);
  if (hunks === null) return { kind: 'raw', reason: 'malformed', text };

  let added = 0;
  let removed = 0;
  for (const hunk of hunks) {
    for (const row of hunk.rows) {
      if (row.kind === 'add') added += 1;
      else if (row.kind === 'del') removed += 1;
    }
  }
  return { kind: 'unified', hunks, added, removed, text };
}
