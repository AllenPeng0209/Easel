/**
 * Sequence projection adapted from DeepSeek Harness
 * packages/client/ui-trajectory/src/client/timeline.ts @ ce7073d (MIT).
 * Easel supplies ordered transport observations instead of trajectory cells.
 */
import { agentToolKey, mergeToolSnapshots } from '../../../lib/agentEvents';
import type { AgentEvent } from '../../../lib/agentEvents';
import type { AgentTimelineObservation } from '../../../lib/agentTimeline';

export interface AgentTimelineRow {
  ordinal: number;
  event: AgentTimelineObservation;
}

export interface AgentTimeline {
  observations: AgentTimelineRow[];
  summary: { tools: number; failed: number; waiting: number };
}

const questionSettlements = new Set(['resolved', 'answered', 'declined', 'denied', 'blocked', 'cancelled']);

/** Project recorded order independently of time data and the latest tool snapshot. */
export function projectAgentTimeline(events: readonly AgentTimelineObservation[]): AgentTimeline {
  const tools = new Map<string, AgentEvent>();
  const questions = new Map<string, boolean>();
  const observations = events.map((event, ordinal): AgentTimelineRow => {
    const snapshot: AgentEvent = { ...event, timestamp: event.observedAt };
    if (event.kind === 'tool') {
      const key = agentToolKey(snapshot);
      const previous = tools.get(key);
      tools.set(key, previous ? mergeToolSnapshots(previous, snapshot) : snapshot);
    }
    if (event.kind === 'decision' && event.questionId && event.status) {
      const key = agentToolKey(snapshot, event.questionId);
      if (questionSettlements.has(event.status)) questions.set(key, false);
      if (event.status === 'pending' && !questions.has(key)) questions.set(key, true);
    }
    return { ordinal, event };
  });

  let failed = 0;
  for (const event of tools.values()) {
    if (event.status === 'failed') failed += 1;
  }

  return {
    observations,
    summary: { tools: tools.size, failed, waiting: [...questions.values()].filter(Boolean).length },
  };
}
