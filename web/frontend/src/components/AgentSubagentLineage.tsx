/**
 * Copyright (c) 2026 DeepSeek. MIT, see integrations/deepseek-harness/LICENSE.
 * Adapted from packages/client/ui-subagent/src/client/SubagentHeaderLineage.tsx,
 * sidebar-chat/index.tsx and SubagentReadOnlyComposer.tsx at
 * ce7073d51254c26cd63b22a60835b8f948deb447.
 * 响芽 binds the lineage catalog and abortable child conversation to a scoped
 * read-only Host route and reuses the shared DisclosureRow primitive.
 */
import { useEffect, useState } from 'react';
import { isSubagentRouteSegment, mergeAgentSubagent, normalizeAgentSubagent, type AgentEvent, type AgentSubagent } from '../lib/agentEvents';
import { projectFetch } from '../lib/projectScope';
import { DisclosureRow } from '../vendor/deepseek-harness/message/DisclosureRow';
import '../styles/agentSubagents.css';

const statuses: Record<string, string> = {
  queued: '排队中', pending: '等待中', preparing: '准备中', pendingInit: '准备中', running: '执行中',
  completed: '已完成', failed: '失败', errored: '失败', interrupted: '已中断',
  cancelled: '已取消', stopped: '已停止', shutdown: '已关闭', notFound: '无法确认',
  closed: '已关闭', unavailable: '会话不可用', unconfirmed: '未收到终态', unknown: '状态未报告',
};
const active = new Set(['queued', 'pending', 'preparing', 'pendingInit', 'running']);

export type OpenSubagent = (child: AgentSubagent, trigger: HTMLElement) => void;

interface ChildRead {
  messages: { role: 'user' | 'assistant'; content: string }[];
  children: AgentSubagent[];
  hasMore: boolean;
}

function readConversation(value: unknown): ChildRead {
  if (!value || typeof value !== 'object') throw new Error();
  const { messages, children, hasMore } = value as Partial<ChildRead>;
  if (!Array.isArray(messages)) throw new Error();
  return {
    messages: messages.filter(item => item && (item.role === 'user' || item.role === 'assistant') && typeof item.content === 'string'),
    children: Array.isArray(children) ? children.map(normalizeAgentSubagent).filter(child => child !== null) : [],
    hasMore: hasMore === true,
  };
}

function ChildConversation({ sessionId, child, known, path, settled, onOpenSubagent }: NodeProps) {
  const [read, setRead] = useState<ChildRead | 'error'>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const response = await projectFetch(`/api/chat/subagents/${encodeURIComponent(sessionId)}/${encodeURIComponent(child.id)}`,
          { method: 'GET', signal: controller.signal });
        if (!response.ok) throw new Error();
        const conversation = readConversation(await response.json());
        if (!controller.signal.aborted) setRead(conversation);
      } catch {
        if (!controller.signal.aborted) setRead('error');
      }
    };
    void load();
    return () => controller.abort();
  }, [sessionId, child.id, attempt]);

  const reload = () => { setRead(undefined); setAttempt(value => value + 1); };
  if (!read) return <p role="status">正在读取子会话…</p>;
  if (read === 'error') return <div role="status">无法读取子会话。
    <button type="button" onClick={reload}>重试</button>
  </div>;
  const children = new Map(read.children.filter(item => !path.includes(item.id) && !known.has(item.id)
    && item.rootSessionId === sessionId && item.parentId === child.id).map(item => [item.id, item]));
  return <>
    <div className="agent-subagent-readonly"><span>独立子会话 · 只读</span>
      <button type="button" onClick={reload}>刷新消息</button></div>
    {read.hasMore && <p>当前仅显示最近的消息。</p>}
    {read.messages.length ? <div className="agent-subagent-transcript" tabIndex={0} aria-label="子会话消息">
      {read.messages.map((message, index) => <div key={index}>
        <strong>{message.role === 'assistant' ? '子 Agent' : '任务输入'}</strong><p>{message.content}</p>
      </div>)}
    </div> : <p>暂无消息</p>}
    {path.length < 12 && children.size > 0 && <ul>{[...children.values()].map(item =>
      <SubagentNode key={item.id} child={item} known={children} path={path} sessionId={sessionId} settled={settled}
        onOpenSubagent={onOpenSubagent} />)}</ul>}
  </>;
}

interface NodeProps {
  child: AgentSubagent;
  sessionId: string;
  known: ReadonlyMap<string, AgentSubagent>;
  path: readonly string[];
  settled: boolean;
  onOpenSubagent?: OpenSubagent;
}

function SubagentNode({ child, sessionId, known, path, settled, onOpenSubagent }: NodeProps) {
  const [open, setOpen] = useState(false);
  const lineage = [...path, child.id];
  const children = lineage.length < 12
    ? [...known.values()].filter(item => item.parentId === child.id && !lineage.includes(item.id)) : [];
  const running = active.has(child.status);
  const status = running && settled ? 'unconfirmed' : Object.hasOwn(statuses, child.status) ? child.status : 'unknown';
  const canOpen = isSubagentRouteSegment(sessionId);
  return <li data-subagent-id={child.id} data-subagent-status={status}>
    <DisclosureRow title={child.title} icon={<span className="agent-status-dot" data-state={status} />}
      open={open} expandable={canOpen && !onOpenSubagent} expandOnRowClick running={running && !settled}
      onToggle={() => setOpen(value => !value)} headerAccessory={<span className="agent-subagent-actions">
        <span className="agent-tool__status">{statuses[status]}</span>
        {canOpen && onOpenSubagent && <button type="button" aria-label={`查看子会话：${child.title}`}
          onClick={event => onOpenSubagent(child, event.currentTarget)}>查看</button>}
      </span>}>
      {open && <div className="agent-subagent-details">
        <ChildConversation child={child} known={known} path={lineage} sessionId={sessionId} settled={settled} />
      </div>}
    </DisclosureRow>
    {children.length > 0 && <ul>{children.map(item =>
      <SubagentNode key={item.id} child={item} known={known} path={lineage} sessionId={sessionId} settled={settled}
        onOpenSubagent={onOpenSubagent} />)}</ul>}
  </li>;
}

export function SubagentTranscript({ sessionId, child, onOpenSubagent }: {
  sessionId: string; child: AgentSubagent; onOpenSubagent?: OpenSubagent;
}) {
  const normalized = normalizeAgentSubagent(child);
  if (!normalized || normalized.rootSessionId !== sessionId) return <p role="status">无法读取子会话。</p>;
  return <div className="agent-subagent-lineage agent-subagent-pane-body">
    <ChildConversation key={JSON.stringify([sessionId, normalized.id])} sessionId={sessionId}
      child={normalized} known={new Map()} path={[normalized.id]} settled={false} onOpenSubagent={onOpenSubagent} />
  </div>;
}

export default function AgentSubagentLineage({ events, sessionId, settled, onOpenSubagent }: {
  events: AgentEvent[]; sessionId: string; settled: boolean; onOpenSubagent?: OpenSubagent;
}) {
  const known = new Map<string, AgentSubagent>();
  for (const event of events) {
    const child = event.subagent;
    if (!child || child.rootSessionId !== sessionId) continue;
    known.set(child.id, mergeAgentSubagent(known.get(child.id), child));
  }
  const nodes = [...known.values()];
  const roots = nodes.filter(child => !child.parentId || !known.has(child.parentId));
  const running = settled ? 0 : nodes.filter(child => child.status === 'running').length;
  if (!roots.length) return null;
  return <section className="agent-subagent-lineage" aria-label="子 Agent 树">
    <h4>子 Agent · {known.size} 个{running > 0 && ` · ${running} 个运行中`}</h4>
    <ul>{roots.map(child =>
      <SubagentNode key={child.id} child={child} known={known} path={[]} sessionId={sessionId} settled={settled}
        onOpenSubagent={onOpenSubagent} />)}</ul>
  </section>;
}
