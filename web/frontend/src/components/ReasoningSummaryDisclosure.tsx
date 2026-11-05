/**
 * Public summary disclosure adapted from DeepSeek Harness
 * ui-trajectory/src/client/TrajectoryTable.tsx's reasoning quote @ ce7073d (MIT).
 * 响芽 renders only the typed public summary and keeps the retained text inert.
 */
import { useId, useMemo, useState } from 'react';
import type { ReasoningSummary } from '../lib/store';
import { inspectTerminalText } from '../lib/toolValueInspection';
import { useWorkDisclosure } from './useWorkDisclosure';
import css from './ReasoningSummaryDisclosure.module.css';

export default function ReasoningSummaryDisclosure({ summary, defaultOpen }: {
  summary: ReasoningSummary; defaultOpen?: boolean;
}) {
  const [manual, setManual] = useState<boolean>();
  const disclosure = useWorkDisclosure(manual ?? defaultOpen ?? false, manual === undefined && defaultOpen !== undefined);
  const open = disclosure.open;
  const bodyId = useId();
  const safe = useMemo(() => inspectTerminalText(summary.text), [summary.text]);
  const truncated = summary.truncated || safe.truncated;
  const hasText = !!safe.text.trim();
  if (!hasText && !truncated) return null;
  return <section className={css.quote} data-reasoning-summary aria-label="公开推理摘要"
    onFocusCapture={disclosure.onFocusCapture} onBlurCapture={disclosure.onBlurCapture}>
    <div className={css.heading}>
      <strong>公开推理摘要</strong>
      {hasText && <button type="button" className={css.toggle} aria-expanded={open}
        aria-controls={open ? bodyId : undefined} onClick={() => setManual(!open)}>
        {open ? '收起公开推理摘要' : truncated ? '查看已保存部分' : '展开公开推理摘要'}
      </button>}
    </div>
    {truncated && <p className={css.notice} data-reasoning-summary-truncated>
      {hasText ? '摘要已截断，下方可查看已保存的部分。' : '摘要已截断，当前没有保留的正文。'}
    </p>}
    {open && hasText && <div className={css.body} id={bodyId} data-reasoning-summary-body>{safe.text}</div>}
  </section>;
}
