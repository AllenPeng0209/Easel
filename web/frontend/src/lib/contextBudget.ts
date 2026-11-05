/** Adapted from DeepSeek Harness ui-conversation/context-occupancy.ts
 * (MIT, ce7073d). Xiangya uses only explicitly attributed native observations.
 */
import type { AgentEvent } from './agentEvents';
import type { ChatMessage } from './store';

export interface ContextBudget {
  source?: 'codex' | 'acp' | 'api' | 'openclaw';
  usedTokens?: number;
  contextWindow?: number;
  percent?: number;
  freshness?: 'live' | 'settled';
}

function tokenCount(value: unknown, minimum: number): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum ? value : undefined;
}

export function readContextBudget(
  messages: readonly ChatMessage[], liveEvents?: readonly AgentEvent[],
): ContextBudget {
  const events = liveEvents ?? messages.findLast(message => message.role === 'assistant')?.events ?? [];
  for (let index = events.length - 1; index >= 0; index--) {
    const event = events[index];
    if (event?.kind === 'context' && event.contextReset === true && event.source === 'codex') return {};
    if (event?.kind !== 'usage' || !event.contextUsage || typeof event.contextUsage !== 'object') continue;
    const { source, usedTokens: used, capacityTokens } = event.contextUsage as Record<string, unknown>;
    if (source !== 'codex' && source !== 'acp') continue;
    const usedTokens = tokenCount(used, 0);
    const contextWindow = tokenCount(capacityTokens, 1);
    const percent = usedTokens === undefined || contextWindow === undefined
      ? undefined : Math.min(100, Math.round(usedTokens / contextWindow * 100));
    return { source, usedTokens, contextWindow, percent, freshness: liveEvents ? 'live' : 'settled' };
  }
  return {};
}
