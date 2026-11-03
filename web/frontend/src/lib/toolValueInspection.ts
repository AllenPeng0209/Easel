const MAX_TEXT = 16000;
const MAX_SOURCE = MAX_TEXT * 4;
const MAX_DEPTH = 10;
const MAX_NODES = 600;
const MAX_ENTRIES = 100;
const HIDDEN = '[已隐藏]';
const OMITTED = '…';
const sensitiveKey = /(?:password|passwd|secret|api[_-]?key|(?:access|refresh|session|auth|id)[_-]?token|authorization|cookie|credential|private[_-]?key|^token$|^auth$)/i;
const assignment = /(\b(?:[\w-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?token|authorization|cookie|credential|private[_-]?key)[\w-]*|token)\b["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$)|[^\s,;&}\]]+)/gi;

function redactText(text: string): string {
  return text
    .replace(/-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----[\s\S]*?(?:-----END \1-----|$)/g, HIDDEN)
    .replace(/\b((?:cookie|set-cookie|authorization|proxy-authorization)\s*:\s*)[^\r\n]*/gi, `$1${HIDDEN}`)
    .replace(/\b(Bearer|Basic)\s+[\w.+/=-]+/gi, `$1 ${HIDDEN}`)
    .replace(/\bsk-[\w-]{12,}/g, HIDDEN)
    .replace(/\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b/g, HIDDEN)
    .replace(/(https?:\/\/)[^\s/@:]+:[^\s/@]+@/gi, `$1${HIDDEN}@`)
    .replace(/(--(?:api[-_]key|access[-_]token|refresh[-_]token|password|passwd|secret|token|cookie)\s+)(?:"(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$)|[^\s;&]+)/gi, `$1${HIDDEN}`)
    .replace(assignment, `$1${HIDDEN}`);
}

/** Strip terminal controls before scrubbing so color escapes cannot split a secret. */
export function inspectTerminalText(input: string, structured = false): { text: string; truncated: boolean } {
  const source = input.slice(0, MAX_SOURCE)
    // eslint-disable-next-line no-control-regex
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]/g, '')
    .replace(/\r\n/g, '\n')
    // eslint-disable-next-line no-control-regex
    .replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, '');
  if (structured) {
    const inspected = inspectToolValue(source);
    return { text: inspected.text, truncated: inspected.truncated || input.length > MAX_SOURCE };
  }
  const safe = redactText(source);
  return { text: safe.length > MAX_TEXT ? safe.slice(0, MAX_TEXT - OMITTED.length) + OMITTED : safe,
    truncated: safe.length > MAX_TEXT || input.length > MAX_SOURCE };
}

export interface ToolValueInspection {
  kind: 'missing' | 'json' | 'text';
  text: string;
  value?: unknown;
  truncated: boolean;
  taskIds: string[];
}

/** The rendered tree and clipboard share one redacted, bounded projection. */
export function inspectToolValue(input: unknown): ToolValueInspection {
  if (input === undefined) return { kind: 'missing', text: '', truncated: false, taskIds: [] };

  let truncated = false;
  let nodes = 0;
  let budget = MAX_TEXT;
  const taskIds = new Set<string>();

  function boundText(text: string, limit: number): string {
    if (text.length > MAX_SOURCE) truncated = true;
    const safe = redactText(text.slice(0, MAX_SOURCE));
    if (safe.length <= limit) return safe;
    truncated = true;
    return safe.slice(0, Math.max(0, limit - OMITTED.length)) + OMITTED;
  }

  function clean(value: unknown, depth: number): unknown {
    if (++nodes > MAX_NODES || depth > MAX_DEPTH || budget <= 0) {
      truncated = true;
      return OMITTED;
    }
    if (typeof value === 'string') {
      const safe = boundText(value, budget);
      budget -= safe.length;
      return safe;
    }
    if (value === null || typeof value === 'boolean' || typeof value === 'number') return value;
    if (Array.isArray(value)) {
      if (value.length > MAX_ENTRIES) truncated = true;
      return value.slice(0, MAX_ENTRIES).map(item => clean(item, depth + 1));
    }
    if (typeof value !== 'object') return String(value);

    const source = value as Record<string, unknown>;
    const result: Record<string, unknown> = Object.create(null);
    let count = 0;
    for (const key in source) {
      if (!Object.hasOwn(source, key)) continue;
      if (++count > MAX_ENTRIES || nodes >= MAX_NODES || budget <= 0) {
        truncated = true;
        break;
      }
      const label = boundText(key, Math.min(budget, 240));
      budget -= label.length;
      const field = sensitiveKey.test(key) ? HIDDEN : clean(source[key], depth + 1);
      result[label] = field;
      if ((key === 'taskId' || key === 'task_id') && typeof field === 'string'
          && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(field) && taskIds.size < 10) {
        taskIds.add(field);
      }
    }
    return result;
  }

  let value = input;
  if (typeof input === 'string') {
    if (input.length > MAX_SOURCE) {
      return { kind: 'text', text: boundText(input, MAX_TEXT), truncated, taskIds: [] };
    }
    try { value = JSON.parse(input); } catch {
      return { kind: 'text', text: boundText(input, MAX_TEXT), truncated, taskIds: [] };
    }
  }

  const projected = clean(value, 0);
  const text = JSON.stringify(projected, null, 2) ?? '';
  if (text.length > MAX_TEXT) {
    return { kind: 'text', text: text.slice(0, MAX_TEXT - OMITTED.length) + OMITTED,
      truncated: true, taskIds: [...taskIds] };
  }
  return { kind: 'json', value: projected, text, truncated, taskIds: [...taskIds] };
}
