import { useEffect, useMemo, useState, type ButtonHTMLAttributes } from 'react';
import { inspectToolValue } from '../lib/toolValueInspection';
import { JsonTree } from '../vendor/deepseek-harness/tool/JsonTree';
import { writeClipboard } from '../vendor/deepseek-harness/message/clipboard';
import css from './ToolValueInspector.module.css';

function CopyButton({ text, label, ...attributes }: {
  text: string; label: string;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const [state, setState] = useState<'idle' | 'pending' | 'copied' | 'failed'>('idle');
  useEffect(() => {
    if (state !== 'copied' && state !== 'failed') return;
    const timer = setTimeout(() => setState('idle'), 1500);
    return () => clearTimeout(timer);
  }, [state]);
  const feedback = state === 'copied' ? '已复制' : state === 'failed' ? '复制失败' : '';
  return <button {...attributes} type="button" className={css.copy} data-copy-state={state}
    aria-label={feedback ? `${label}，${feedback}` : label} aria-live="polite" disabled={state === 'pending'}
    onClick={() => {
      setState('pending');
      void writeClipboard(text).then(ok => setState(ok ? 'copied' : 'failed'), () => setState('failed'));
    }}>
    {feedback || '复制'}
  </button>;
}

/**
 * Adapts TrajectoryTable's payload section from DeepSeek @ ce7073d.
 * The projection is sanitized once for both display and clipboard.
 */
export default function ToolValueInspector({ label, value }: { label: string; value: unknown }) {
  const inspected = useMemo(() => inspectToolValue(value), [value]);
  return <section className={css.section} aria-label={label} data-tool-value-kind={inspected.kind}>
    <div className={css.header}>
      <h4>{label}</h4>
      {inspected.kind !== 'missing' && <CopyButton text={inspected.text} label={`复制${label}`} data-copy-tool-value />}
    </div>
    {inspected.taskIds.length > 0 && <div className={css.taskIds}>
      {inspected.taskIds.map(taskId => <span className={css.taskId} key={taskId}>
        任务 ID <code>{taskId}</code>{' '}
        <CopyButton text={taskId} label={`复制任务 ID ${taskId}`} data-copy-tool-task-id />
      </span>)}
    </div>}
    {inspected.kind === 'missing' ? <p className="agent-event__note">当时未记录</p>
      : inspected.kind === 'json' && inspected.value !== null && typeof inspected.value === 'object' ? (
        <JsonTree data={inspected.value} label={`${label} JSON`} renderCopy={(field, safeValue) =>
          <CopyButton text={typeof safeValue === 'string' ? safeValue : JSON.stringify(safeValue, null, 2)}
            label={`复制字段 ${field}`} />}
        />
      ) : <pre>{inspected.text || '（空文本）'}</pre>}
    {inspected.truncated && <p className="agent-event__note">详情已截断，仅显示保留的部分。</p>}
  </section>;
}
