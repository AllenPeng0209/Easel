import type { AgentEvent } from '../lib/agentEvents';
import { planSummary } from '../vendor/deepseek-harness/todo/plan-summary';
import { planDiff, type PlanStep } from '../vendor/deepseek-harness/todo/plan-diff';

const statusLabels: Record<string, string> = {
  pending: '待处理', in_progress: '进行中', completed: '已完成',
};

function readSteps(event: AgentEvent): PlanStep[] | null {
  const steps = event.steps ?? event.plan;
  if (event.truncated || !Array.isArray(steps)) return null;
  const occurrences = new Map<string, number>();
  const result: PlanStep[] = [];
  for (const step of steps) {
    if (!step || typeof step !== 'object') return null;
    const label = step.step ?? step.content ?? step.title ?? step.description;
    if (typeof label !== 'string' || !label.trim()) return null;
    const content = label.trim();
    const base = content;
    const occurrence = (occurrences.get(base) ?? 0) + 1;
    occurrences.set(base, occurrence);
    result.push({
      key: JSON.stringify([content, occurrence]),
      content,
      status: step.status === 'inProgress' ? 'in_progress' : String(step.status ?? ''),
    });
  }
  return result;
}

export default function AgentTodoChecklist({ events }: { events: AgentEvent[] }) {
  let current: PlanStep[] | null = null;
  const revisions = events.map(event => {
    const steps = readSteps(event);
    const changes = steps && current ? planDiff(current, steps) : [];
    const initial = steps !== null && current === null;
    if (steps) current = steps;
    return { id: event.id, steps, changes, initial };
  });
  const latest = revisions.findLast(revision => revision.steps !== null)?.steps;
  const summary = latest ? planSummary(latest) : undefined;
  const lastEvent = events[events.length - 1];
  return <section className="agent-plan" aria-label="任务清单">
    <header><strong>任务清单 · 引擎计划状态</strong>{summary && <span>{summary.done}/{summary.total} 已完成</span>}</header>
    {typeof lastEvent.explanation === 'string' && <p>{lastEvent.explanation}</p>}
    {revisions[revisions.length - 1].steps === null &&
      <p role="status">清单更新不完整{latest ? '，保留最近一次完整记录。' : '，等待完整记录。'}</p>}
    {latest && (latest.length ? <ol>{latest.map(item => <li key={item.key} data-state={item.status}>
      <span>{statusLabels[item.status] || '状态未报告'}</span>
      <div>{item.content}<small>执行者未报告</small></div>
    </li>)}</ol> : <p>清单已清空</p>)}
    {revisions.length > 1 && <details>
      <summary>变更记录（{revisions.length} 次更新）</summary>
      <ol>{revisions.map((revision, index) => <li key={revision.id}>
        <span>第 {index + 1} 次</span>
        <div>{revision.steps === null ? '清单更新不完整' : revision.initial ? '首次记录'
          : revision.changes.length ? revision.changes.map(({ item, labels }) =>
            <div key={item.key}>{labels.join('、')} · {item.content}</div>) : '无变更'}</div>
      </li>)}</ol>
    </details>}
  </section>;
}
