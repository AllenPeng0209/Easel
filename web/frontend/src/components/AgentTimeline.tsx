/**
 * Chronological overview adapted from DeepSeek Harness
 * ui-trajectory/src/client/TrajectoryTimeline.tsx @ ce7073d (MIT).
 * Easel uses recorded transport order and exposes timestamps only when supplied.
 */
import { useMemo, useState } from 'react';
import type { AgentTimelineObservation } from '../lib/agentTimeline';
import { agentToolKey, mergeToolSnapshots, type AgentEvent } from '../lib/agentEvents';
import { inspectTerminalText } from '../lib/toolValueInspection';
import { projectAgentTimeline } from '../vendor/deepseek-harness/trajectory/timeline';
import { useWorkDisclosure } from './useWorkDisclosure';
import css from './AgentTimeline.module.css';

const kindLabels: Record<AgentTimelineObservation['kind'], string> = {
  tool: '工具调用', decision: '决策记录', plan: '计划更新', context: '上下文事件',
  subagent: '子任务', artifact: '交付记录', business: '业务记录', publication: '发布回执', steer: '运行中指导',
};

const statusLabels: Record<string, string> = {
  preparing: '准备参数', pending: '等待中', running: '执行中', in_progress: '执行中',
  completed: '已完成', failed: '失败', interrupted: '已中断', stopped: '已停止',
  cancelled: '已取消', denied: '未获授权', declined: '已拒绝', unconfirmed: '状态未确认',
  resolved: '已记录', answered: '答复已记录', blocked: '已受阻', accepted: '已接收',
  unknown: '结果待核实', executing: '正在执行', submitted: '已提交', confirmed: '回执状态已记录',
};

function observationTitle(event: AgentTimelineObservation): string {
  if (event.kind === 'decision' && event.status === 'pending') return '等待答复';
  if (event.kind === 'decision' && (event.status === 'resolved' || event.status === 'answered')) {
    if (event.resolvedBy === 'human') return '用户答复已记录';
    if (event.resolvedBy === 'default') return '默认答案已记录';
  }
  if (event.kind === 'decision' && event.resolvedBy === 'timeout') return '等待超时';
  const heading = kindLabels[event.kind];
  return event.kind === 'tool' && typeof event.name === 'string' && event.name
    ? `${heading} · ${inspectTerminalText(event.name).text}` : heading;
}

function ObservationTime({ observedAt }: { observedAt?: number }) {
  if (typeof observedAt !== 'number' || !Number.isSafeInteger(observedAt) || observedAt < 0) return null;
  const date = new Date(observedAt);
  if (!Number.isFinite(date.getTime())) return null;
  return <time dateTime={date.toISOString()}>{date.toLocaleTimeString('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  })}</time>;
}

function collapseToolPhases(observations: readonly AgentTimelineObservation[]): AgentTimelineObservation[] {
  const rows: AgentTimelineObservation[] = [];
  const tools = new Map<string, { index: number; snapshot: AgentEvent }>();
  for (const event of observations) {
    if (event.kind !== 'tool' || !event.callId) {
      rows.push(event);
      continue;
    }
    const key = agentToolKey(event);
    const snapshot: AgentEvent = { ...event, timestamp: event.observedAt };
    const previous = tools.get(key);
    if (!previous) {
      tools.set(key, { index: rows.length, snapshot });
      rows.push(event);
      continue;
    }
    const latest = mergeToolSnapshots(previous.snapshot, snapshot);
    previous.snapshot = latest;
    // Keep the call in its first recorded position, with its current phase.
    rows[previous.index] = { ...rows[previous.index], name: latest.name, status: latest.status };
  }
  return rows;
}

export default function AgentTimeline({ observations, pendingQuestions, onLocateTool, defaultOpen }: {
  observations: readonly AgentTimelineObservation[];
  pendingQuestions?: number;
  defaultOpen?: boolean;
  onLocateTool?: (runId: string | undefined, callId: string) => boolean;
}) {
  const [locateMissing, setLocateMissing] = useState(false);
  const [manual, setManual] = useState<boolean>();
  const disclosure = useWorkDisclosure(manual ?? defaultOpen ?? false, manual === undefined && defaultOpen !== undefined);
  const { observations: rows, summary } = useMemo(() => projectAgentTimeline(
    collapseToolPhases(observations.filter(event => event && typeof event.id === 'string' && Object.hasOwn(kindLabels, event.kind))),
  ), [observations]);
  if (!rows.length) return null;
  const waiting = Math.max(pendingQuestions ?? 0, summary.waiting);
  const omitted = rows[0].event.earlierObservationsOmitted === true;

  return <details className={css.timeline} data-agent-timeline aria-label="执行时间线" open={disclosure.open}
    onFocusCapture={disclosure.onFocusCapture} onBlurCapture={disclosure.onBlurCapture}>
    <summary onClick={event => {
      event.preventDefault();
      setManual(!disclosure.open);
    }}>
      <span>执行时间线 · {omitted ? '保留记录中 ' : ''}{summary.tools} 次工具调用</span>
      {summary.failed > 0 && <span className={css.failed} data-timeline-failed={summary.failed}>{summary.failed} 个失败</span>}
      {waiting > 0 && <span className={css.waiting} data-timeline-waiting={waiting}>{waiting} 项待答复</span>}
    </summary>
    {omitted && <p className={css.omitted} data-timeline-omitted>较早的观测已省略。</p>}
    <ol aria-label="按观测顺序记录">
      {rows.map(({ event, ordinal }) => {
        const status = event.status && Object.hasOwn(statusLabels, event.status) ? event.status : undefined;
        const callId = typeof event.callId === 'string' && event.callId.trim() ? event.callId : undefined;
        return <li key={JSON.stringify([event.runId ?? null, event.turnId ?? null, event.id, event.callId ?? null])}
          data-timeline-observation={ordinal} data-observation-kind={event.kind} data-observation-status={status}>
          <span className={css.title}>{observationTitle(event)}</span>
          {status && <span className={css.status}>{statusLabels[status]}</span>}
          <ObservationTime observedAt={event.observedAt} />
          {event.kind === 'tool' && status === 'failed' && callId && onLocateTool &&
            <button type="button" className={css.locate} data-locate-failed-tool
              onClick={() => setLocateMissing(!onLocateTool(event.runId, callId))}>定位失败工具</button>}
        </li>;
      })}
    </ol>
    {locateMissing && <p className={css.omitted} data-tool-locate-missing role="status">这条消息的历史记录中没有对应的工具详情。</p>}
  </details>;
}
