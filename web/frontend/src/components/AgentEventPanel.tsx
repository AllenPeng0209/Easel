import { useMemo, useState, type ReactNode } from 'react';
import type { AgentEvent } from '../lib/agentEvents';
import { publicationEventKey, businessEventKey, normalizeAgentEvent, agentEventKey, agentToolKey, mergeToolSnapshots } from '../lib/agentEvents';
import AgentTodoChecklist from './AgentTodoChecklist';
import AgentSubagentLineage, { type OpenSubagent } from './AgentSubagentLineage';
import BusinessObjectCard from './BusinessObjectCard';
import { fetchBusinessObject } from '../lib/businessObjectApi';
import PublicationReceiptCard from './PublicationReceiptCard';
import { fetchPublicationApproval } from '../lib/api';
import { DisclosureRow } from '../vendor/deepseek-harness/message/DisclosureRow';
import { projectToolCallTree, type ToolCallBranch } from '../vendor/deepseek-harness/tool/tool-call-tree';

const statusText: Record<string, string> = {
  preparing: '准备中', pending: '等待中', running: '执行中', in_progress: '执行中',
  completed: '已完成', failed: '失败', interrupted: '已中断', cancelled: '已取消',
  stopped: '已停止', denied: '未授权', declined: '已拒绝',
};
const names: Record<string, string> = {
  commandExecution: '运行命令', fileChange: '修改文件', mcpToolCall: '调用工具',
  webSearch: '搜索网页', reasoning: '整理信息', todo_write: '任务清单',
};
const sensitive = /(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|credential)/i;

function redact(value: unknown, depth = 0): unknown {
  if (depth > 10) return '…';
  if (Array.isArray(value)) return value.map(item => redact(item, depth + 1));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .map(([key, item]) => [key, sensitive.test(key) ? '[已隐藏]' : redact(item, depth + 1)]));
  if (typeof value === 'string') return value.replace(/\bBearer\s+[\w.+/=-]+/gi, 'Bearer [已隐藏]')
    .replace(/\bsk-[\w-]{12,}/g, '[已隐藏]');
  return value;
}

function detailText(value: unknown): string {
  if (typeof value === 'string') {
    try { return JSON.stringify(redact(JSON.parse(value)), null, 2); } catch { /* Plain tool text. */ }
  }
  const text = typeof value === 'string' ? String(redact(value)) : JSON.stringify(redact(value), null, 2) || '';
  return text.length > 16000 ? `${text.slice(0, 16000)}\n… 内容较长，当前仅显示前 16000 个字符` : text;
}

function toolSnapshots(events: AgentEvent[]): AgentEvent[] {
  const result: AgentEvent[] = [];
  const indexes = new Map<string, number>();
  const seenEvents = new Set<string>();
  for (const event of events) {
    const key = agentEventKey(event);
    const business = businessEventKey(event);
    const receipt = publicationEventKey(event);
    if (!business && !receipt) {
      if (seenEvents.has(key)) continue;
      seenEvents.add(key);
    }
    const identity = business ? `business:${business}` : receipt ? `publication:${receipt}` : event.kind === 'tool' ? `tool:${agentToolKey(event)}` : key;
    const index = event.kind === 'tool' || receipt || business ? indexes.get(identity) : undefined;
    if (index === undefined) {
      if (event.kind === 'tool' || receipt || business) indexes.set(identity, result.length);
      result.push(event);
    } else if (business) {
      if ((event.business?.observedAt || 0) > (result[index].business?.observedAt || 0)) result[index] = event;
    } else if (receipt) {
      if ((event.completedAt || 0) >= (result[index].completedAt || 0)) result[index] = event;
    } else {
      result[index] = mergeToolSnapshots(result[index], event);
    }
  }
  return result;
}

function ToolEvent({ event, open, settled, children, childCount, onToggle }: {
  event: AgentEvent; open: boolean; settled: boolean; children?: ReactNode; childCount: number; onToggle: () => void;
}) {
  const state = event.status || 'pending';
  const active = state === 'running' || state === 'in_progress' || state === 'preparing' || state === 'pending';
  const unconfirmed = settled && active;
  const running = !settled && active;
  const title = names[event.name || ''] || event.name || '工具调用';
  const duration = typeof event.startedAt === 'number' && typeof event.completedAt === 'number'
    ? Math.max(0, event.completedAt - event.startedAt) : undefined;
  return <div className="agent-tool" data-tool-call={event.callId || event.id}
    data-chat-anchor-key={`call:${agentToolKey(event)}`} data-run-id={event.runId} data-parent-call={event.parentCallId} data-state={unconfirmed ? 'unconfirmed' : state} data-tool-status={state}>
    <DisclosureRow icon={<span className="agent-status-dot" data-state={unconfirmed ? 'unconfirmed' : state} />}
      title={title} open={open} expandable expandOnRowClick running={running}
      onToggle={onToggle}
      headerAccessory={<span className="agent-tool__status">{unconfirmed ? '未收到工具终态' : statusText[state] || state}
        {duration !== undefined && <span> · {(duration / 1000).toFixed(1)}s</span>}
        {childCount > 0 && <span className="agent-tool__child-count">{childCount} 个子调用</span>}</span>}
      collapsedContent={event.parentCallId ? <span className="agent-tool__nested">子调用</span> : undefined}>
      {children}
      <div className="agent-tool__details">
        {unconfirmed && <p className="agent-event__note">本轮已结束，此工具的最终状态仍未确认。</p>}
        {event.input !== undefined && <div><h4>输入</h4><pre>{detailText(event.input)}</pre></div>}
        {event.output !== undefined && <div><h4>结果</h4><pre>{detailText(event.output)}</pre></div>}
        {event.exitCode !== undefined && <p className="agent-tool__exit">退出码：{event.exitCode === null ? '未提供' : event.exitCode}</p>}
        {event.truncated && <p className="agent-event__note">引擎输出已截断，以上不是完整日志。</p>}
        {event.input === undefined && event.output === undefined && <p className="agent-event__note">引擎尚未提供调用详情。</p>}
      </div>
    </DisclosureRow>
  </div>;
}

function OtherEvent({ event }: { event: AgentEvent }) {
  if (event.kind === 'steer' && event.status === 'accepted' && typeof event.text === 'string') {
    return <div className="agent-decision" aria-label="本轮指导已接收">
      <strong>本轮指导已接收</strong><pre>{event.text}</pre>
    </div>;
  }
  if (event.kind === 'business') {
    if (!businessEventKey(event) || !event.business) {
      return <p className="agent-event__note" role="status">业务记录格式无效，未显示详情。</p>;
    }
    const isTopicSource = event.callId?.startsWith('host:topic:');
    const isWorkshopSource = event.callId?.startsWith('host:workshop:');
    return <>
      {isTopicSource && <p className="agent-event__note">本轮创作的选题快照</p>}
      {isWorkshopSource && <p className="agent-event__note">本轮引用的工坊快照</p>}
      <BusinessObjectCard record={event.business} onRefresh={isTopicSource || isWorkshopSource ? undefined : fetchBusinessObject} />
    </>;
  }
  if (event.kind === 'publication' && event.publication) return <PublicationReceiptCard record={event.publication}
    onRefresh={fetchPublicationApproval} />;
  const automatic = event.source === 'default' || event.resolvedBy === 'default';
  const source = automatic ? 'default' : event.source || event.resolvedBy || 'unknown';
  const decisionTitle = event.status === 'blocked' ? '等待确认超时，任务受阻'
    : event.status === 'declined' || event.status === 'denied' ? '未获授权，操作未执行'
    : automatic ? '已按默认答案继续' : source === 'human' ? '已收到你的答复' : '决策已记录';
  if (event.kind === 'decision') return <details className="agent-decision" data-decision-source={String(source)}>
    <summary><span aria-hidden="true">✓</span> {decisionTitle}</summary>
    <pre>{detailText(event.answers || event.output || event.input || {})}</pre>
  </details>;
  if (event.kind === 'plan') return typeof event.text === 'string' && event.text ? <details className="agent-event-generic">
    <summary>计划说明</summary><pre>{detailText(event.text)}</pre>
  </details> : null;
  const title = event.kind === 'usage' ? '本轮用量' : event.kind === 'context' ? '上下文已整理'
    : event.kind === 'artifact' ? '交付物信息' : '任务状态';
  return <details className="agent-event-generic"><summary>{title}</summary>
    <pre>{detailText(event.output || event.input || event)}</pre>
  </details>;
}

/** Adapted from DeepSeek ui-tool ToolCallTree.tsx: one atomic tool card per branch. */
function ToolBranch({ branch, settled, isOpen, onToggle }: {
  branch: ToolCallBranch<AgentEvent>; settled: boolean;
  isOpen: (branch: ToolCallBranch<AgentEvent>) => boolean;
  onToggle: (key: string, open: boolean) => void;
}) {
  const open = isOpen(branch);
  return <ToolEvent event={branch.value} open={open} settled={settled} childCount={branch.children.length}
    onToggle={() => onToggle(branch.key, !open)}>
    {branch.children.length > 0 && <div className="agent-tool__children" data-subcalls>
      {branch.children.map(child => <ToolBranch key={child.key} branch={child} settled={settled}
        isOpen={isOpen} onToggle={onToggle} />)}
    </div>}
  </ToolEvent>;
}

/** The Easel transport boundary feeds the vendored Harness disclosure surface. */
export default function AgentEventPanel({ events, expandedCallId, expandedRunId, sessionId = '', settled = false, onOpenSubagent }: {
  events: AgentEvent[]; expandedCallId?: string; expandedRunId?: string; sessionId?: string; settled?: boolean; onOpenSubagent?: OpenSubagent;
}) {
  const [openCalls, setOpenCalls] = useState(() => new Map<string, boolean>());
  const snapshots = useMemo(() => toolSnapshots(events), [events]);
  const { calls, parents } = useMemo(() => projectToolCallTree(
    snapshots.filter(event => event.kind === 'tool').map(event => ({
      key: agentToolKey(event), value: event,
      parentKey: typeof event.parentCallId === 'string' && event.parentCallId
        ? agentToolKey(event, event.parentCallId) : undefined,
    }))), [snapshots]);
  if (!snapshots.length) return null;
  const isToolOpen = ({ key, value, children }: ToolCallBranch<AgentEvent>) => openCalls.get(key) ??
    (children.length > 0 || (value.callId === expandedCallId && (expandedRunId === undefined || value.runId === expandedRunId)));
  const toggleTool = (key: string, open: boolean) => setOpenCalls(previous => new Map(previous).set(key, open));
  const plans = new Map<string, AgentEvent[]>();
  const subagents = new Map<string, { first: AgentEvent; events: AgentEvent[] }>();
  const turnKey = (event: AgentEvent) => JSON.stringify([event.runId ?? null, event.turnId ?? null]);
  for (const event of snapshots) {
    if (event.kind === 'subagent') {
      const normalized = normalizeAgentEvent(event);
      if (normalized) {
        const lineage = subagents.get(turnKey(event)) || { first: event, events: [] };
        lineage.events.push(normalized);
        subagents.set(turnKey(event), lineage);
      }
    }
    if ((event.kind !== 'plan' || typeof event.text === 'string')) continue;
    const revisions = plans.get(turnKey(event)) || [];
    revisions.push(event);
    plans.set(turnKey(event), revisions);
  }
  return <div className="agent-events" role="region" aria-label="任务执行记录">
    {snapshots.map(event => {
      if (event.kind === 'subagent') {
        const lineage = subagents.get(turnKey(event));
        return lineage?.first === event ? <AgentSubagentLineage key={JSON.stringify([sessionId, turnKey(event)])}
          events={lineage.events} sessionId={sessionId} settled={settled} onOpenSubagent={onOpenSubagent} /> : null;
      }
      if (event.kind === 'tool') {
        const key = agentToolKey(event);
        return parents.has(key) ? null : <ToolBranch key={`tool:${key}`} branch={calls.get(key)!}
          settled={settled} isOpen={isToolOpen} onToggle={toggleTool} />;
      }
      if ((event.kind !== 'plan' || typeof event.text === 'string')) return <OtherEvent key={agentEventKey(event)} event={event} />;
      const revisions = plans.get(turnKey(event))!;
      return revisions[0] === event ? <AgentTodoChecklist key={agentEventKey(event)} events={revisions} /> : null;
    })}
  </div>;
}
