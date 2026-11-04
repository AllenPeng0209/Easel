import { inspectTerminalText } from './toolValueInspection';
import { projectUnifiedDiff, type ToolDiffProjection } from '../vendor/deepseek-harness/tool/file-diff';

export interface TurnChangeTarget {
  sessionId: string;
  turnId: string;
  path: string;
  kind: 'added' | 'modified' | 'deleted';
}

interface FrozenSide {
  sha256: string;
  size: number;
}

function frozenSide(value: unknown): FrozenSide | null | undefined {
  if (value === null) return null;
  if (!value || typeof value !== 'object') return undefined;
  const { sha256, size } = value as Record<string, unknown>;
  return typeof sha256 === 'string' && /^[a-f0-9]{64}$/.test(sha256)
    && typeof size === 'number' && Number.isSafeInteger(size) && size >= 0
    ? { sha256, size } : undefined;
}

export function normalizeTurnChangeComparison(value: unknown, target: TurnChangeTarget):
  | { kind: 'text'; before: FrozenSide | null; after: FrozenSide | null; projection: ToolDiffProjection; redacted: boolean }
  | { kind: 'limited'; reason: 'binary' | 'oversized' | 'unavailable' }
  | null {
  if (!value || typeof value !== 'object') return null;
  const data = value as Record<string, unknown>;
  if (data.sessionId !== target.sessionId || data.turnId !== target.turnId || data.path !== target.path) return null;
  if ((data.diffStatus === 'binary' || data.diffStatus === 'oversized' || data.diffStatus === 'unavailable')
      && data.diff === undefined) return { kind: 'limited', reason: data.diffStatus };
  const before = frozenSide(data.before);
  const after = frozenSide(data.after);
  if (data.diffStatus !== 'text' || data.identical !== false || typeof data.diff !== 'string'
      || before === undefined || after === undefined || (before === null) !== (target.kind === 'added')
      || (after === null) !== (target.kind === 'deleted') || (before && after && before.sha256 === after.sha256)) return null;
  const safe = inspectTerminalText(data.diff);
  const projection: ToolDiffProjection = safe.truncated
    ? { kind: 'raw', reason: 'truncated', text: safe.text } : projectUnifiedDiff(safe.text);
  return { kind: 'text', before, after, projection, redacted: data.redacted === true };
}
