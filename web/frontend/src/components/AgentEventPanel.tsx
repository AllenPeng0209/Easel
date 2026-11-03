import { useMemo, useState, type ReactNode } from 'react';
import type { AgentEvent } from '../lib/agentEvents';
import { publicationEventKey, businessEventKey, normalizeAgentEvent, agentEventKey, agentToolKey, mergeToolSnapshots, commandStatus, isCommandExecution, fileMutationLabel, fileMutationState, toolLifecycle } from '../lib/agentEvents';
import AgentTodoChecklist from './AgentTodoChecklist';
import ToolValueInspector from './ToolValueInspector';
import FileMutationCard from './FileMutationCard';
import CommandExecutionCard from './CommandExecutionCard';
import { inspectToolValue } from '../lib/toolValueInspection';
import AgentSubagentLineage, { type OpenSubagent } from './AgentSubagentLineage';
import BusinessObjectCard from './BusinessObjectCard';
import { fetchBusinessObject } from '../lib/businessObjectApi';
import PublicationReceiptCard from './PublicationReceiptCard';
import { fetchPublicationApproval } from '../lib/api';
import { DisclosureRow } from '../vendor/deepseek-harness/message/DisclosureRow';
import { projectToolCallTree, type ToolCallBranch } from '../vendor/deepseek-harness/tool/tool-call-tree';

const statusText: Record<string, string> = {
  preparing: '准备参数', pending: '等待执行', running: '执行中',
  completed: '已完成', failed: '失败', interrupted: '已中断', cancelled: '已取消',
  stopped: '已停止', denied: '未授权', declined: '已拒绝', unconfirmed: '工具状态未确认',
};
const names: Record<string, string> = {
  commandExecution: '运行命令', fileChange: '修改文件', mcpToolCall: '调用工具',
  webSearch: '搜索网页', reasoning: '整理信息', todo_write: '任务清单',
};
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
  const isCommand = isCommandExecution(event);
  const mutationState = fileMutationState(event, settled);
  const reportedState = isCommand ? commandStatus(event, event.status || '') : event.status;
  const { status: state, phase, awaitingTerminal: unconfirmed } = toolLifecycle(reportedState, settled);
  const running = phase === 'running' || (phase === 'preparing' && state === 'preparing');
  const visualState = phase === 'unconfirmed' || mutationState === 'unconfirmed' ? 'unconfirmed' : state;
  const publication = event.name === 'easel_publish' || event.name === 'easel_publication_status';
  const stateLabel = mutationState ? fileMutationLabel[mutationState] : unconfirmed ? '未收到工具终态'
    : publication && state === 'completed' ? '调用已结束 · 查看发布回执' : statusText[state];
  const title = isCommand ? '运行命令' : mutationState ? '修改文件' : names[event.name || ''] || event.name || '工具调用';
  const duration = !isCommand && typeof event.startedAt === 'number' && typeof event.completedAt === 'number'
    ? Math.max(0, event.completedAt - event.startedAt) : undefined;
  return <div className="agent-tool" data-tool-call={event.callId || event.id}
    data-chat-anchor-key={`call:${agentToolKey(event)}`} data-run-id={event.runId} data-parent-call={event.parentCallId} data-state={visualState} data-tool-status={state} data-tool-phase={phase}>
    <DisclosureRow icon={<span className="agent-status-dot" data-state={visualState} />}
      title={title} open={open} expandable expandOnRowClick running={running}
      onToggle={onToggle}
      headerAccessory={<span className="agent-tool__status">{stateLabel}
        {duration !== undefined && <span> · {(duration / 1000).toFixed(1)}s</span>}
        {childCount > 0 && <span className="agent-tool__child-count">{childCount} 个子调用</span>}</span>}
      collapsedContent={event.parentCallId ? <span className="agent-tool__nested">子调用</span> : undefined}>
      {children}
      <div className="agent-tool__details">
        {unconfirmed && <p className="agent-event__note">本轮已结束，此工具的最终状态仍未确认。</p>}
        {publication && <p className="agent-event__note">平台结果以发布回执为准。</p>}
        {isCommand ? <CommandExecutionCard event={event} showExit={phase === 'terminal' || settled} />
          : mutationState ? <FileMutationCard event={event} state={mutationState} /> : <>
          <ToolValueInspector label="输入" value={event.input} />
          <ToolValueInspector label="结果" value={event.output} />
          {event.exitCode !== undefined && <p className="agent-tool__exit">退出码：{event.exitCode === null ? '未提供' : event.exitCode}</p>}
          {event.truncated && <p className="agent-event__note">引擎输出已截断，以上不是完整日志。</p>}
        </>}
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
    <pre>{inspectToolValue(event.answers || event.output || event.input || {}).text}</pre>
  </details>;
  if (event.kind === 'plan') return typeof event.text === 'string' && event.text ? <details className="agent-event-generic">
    <summary>计划说明</summary><pre>{inspectToolValue(event.text).text}</pre>
  </details> : null;
  const title = event.kind === 'usage' ? '本轮用量' : event.kind === 'context' ? '上下文已整理'
    : event.kind === 'artifact' ? '交付物信息' : '任务状态';
  return <details className="agent-event-generic"><summary>{title}</summary>
    <pre>{inspectToolValue(event.output || event.input || event).text}</pre>
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
