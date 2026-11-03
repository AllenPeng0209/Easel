/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from ui-tool/src/client/tool/models/web-card-model.ts @ ce7073d.
 * Easel uses only the Host's marked Codex action and preserves opaque results.
 * License: integrations/deepseek-harness/LICENSE.
 */
import type { AgentEvent } from '../../../lib/agentEvents';
import { inspectTerminalText } from '../../../lib/toolValueInspection';

export type WebAction = 'search' | 'openPage' | 'findInPage' | 'other';
export type WebEvidence = 'absent' | 'empty' | 'observed' | 'failed';

export const webActionTitle: Record<WebAction, string> = {
  search: '网页搜索', openPage: '打开网页', findInPage: '页内查找', other: '网页工具',
};

export interface WebCardModel {
  action: WebAction;
  queries: string[];
  emptyQueryList: boolean;
  url?: string;
  hasUrl: boolean;
  pattern?: string;
  evidence: WebEvidence;
  results?: unknown[];
  truncated: boolean;
}

const MAX_QUERY_ROWS = 20;
const MAX_QUERY_CHARS = 2000;

function safeText(value: unknown): string | undefined {
  if (typeof value !== 'string' || !value.trim()) return undefined;
  const { text } = inspectTerminalText(value);
  return text.length > MAX_QUERY_CHARS ? text.slice(0, MAX_QUERY_CHARS - 1) + '…' : text;
}

/** The WebBlock allowlist, tightened to exclude credentials and ambiguous URL syntax. */
function safeWebHref(value: unknown): string | undefined {
  if (typeof value !== 'string' || !/^https?:\/\/[^/?#]/i.test(value)
      || /[\p{Cc}\p{Cf}\s\\]/u.test(value) || /\[REDACTED[^\]]*\]|\[已隐藏\]/i.test(value)) return undefined;
  const safe = inspectTerminalText(value);
  if (safe.truncated || safe.text !== value) return undefined;
  try {
    const url = new URL(value);
    if (!url.hostname || url.username || url.password) return undefined;
    const sensitive = /(?:api[_-]?key|token|secret|password|passwd|cookie|credential|authorization|^auth$)/i;
    if ([...url.searchParams.keys()].some(key => sensitive.test(key))) return undefined;
    return value;
  } catch {
    return undefined;
  }
}

export function webCardModel(event: AgentEvent): WebCardModel | undefined {
  if (event.name !== 'web_search' || event.nativeWebSearch !== true) return undefined;
  const input = event.input && typeof event.input === 'object' && !Array.isArray(event.input)
    ? event.input as Record<string, unknown> : {};
  const action = input.action && typeof input.action === 'object' && !Array.isArray(input.action)
    ? input.action as Record<string, unknown> : {};
  const type = action.type === 'search' || action.type === 'openPage' || action.type === 'findInPage' ? action.type : 'other';
  const batch = type === 'search' && Array.isArray(action.queries) ? action.queries : undefined;
  const suppliedQueries = [input.query, type === 'search' ? action.query : undefined, ...(batch?.slice(0, MAX_QUERY_ROWS) ?? [])]
    .filter((value): value is string => typeof value === 'string' && !!value.trim());
  const queries = suppliedQueries.slice(0, MAX_QUERY_ROWS).map(safeText).filter((value): value is string => value !== undefined);
  const clipped = (batch?.length ?? 0) > MAX_QUERY_ROWS || suppliedQueries.length > MAX_QUERY_ROWS
    || suppliedQueries.some(query => query.length > MAX_QUERY_CHARS)
    || (typeof action.pattern === 'string' && action.pattern.length > MAX_QUERY_CHARS);
  const hasUrl = (type === 'openPage' || type === 'findInPage') && typeof action.url === 'string' && action.url.length > 0;
  const results = Array.isArray(event.output) ? event.output : undefined;
  const evidence = event.status === 'failed' ? 'failed'
    : results === undefined ? 'absent' : results.length === 0 ? 'empty' : 'observed';
  return { action: type, queries, emptyQueryList: batch?.length === 0,
    url: hasUrl ? safeWebHref(action.url) : undefined, hasUrl,
    pattern: type === 'findInPage' ? safeText(action.pattern) : undefined,
    evidence, results, truncated: event.truncated === true || clipped };
}
