/**
 * File mutation dispatch adapted from DeepSeek Harness
 * ui-tool/src/client/tool/toolviews/file-mutation-row.tsx @ ce7073d (MIT).
 */
import { useMemo } from 'react';
import { fileMutationChanges, fileMutationLabel, type AgentEvent, type FileMutationState } from '../lib/agentEvents';
import { inspectTerminalText } from '../lib/toolValueInspection';
import { projectUnifiedDiff } from '../vendor/deepseek-harness/tool/file-diff';
import { FileDiff } from '../vendor/deepseek-harness/tool/FileDiff';
import css from './FileMutationCard.module.css';

const mutationLabel = { add: '新增', delete: '删除', update: '修改' };

export default function FileMutationCard({ event, state }: { event: AgentEvent; state: FileMutationState }) {
  const changes = useMemo(() => (fileMutationChanges(event) ?? []).flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const change = item as { path?: unknown; kind?: { type?: unknown; move_path?: unknown }; diff?: unknown };
    if (typeof change.path !== 'string' || !change.path) return [];
    const kind = change.kind?.type;
    const description = kind === 'add' || kind === 'delete' || kind === 'update' ? mutationLabel[kind] : undefined;
    const path = inspectTerminalText(change.path).text;
    const destination = kind === 'update' && typeof change.kind?.move_path === 'string'
      ? inspectTerminalText(change.kind.move_path).text : undefined;
    const safe = typeof change.diff === 'string' ? inspectTerminalText(change.diff) : undefined;
    const projection = safe?.truncated
      ? { kind: 'raw' as const, reason: 'truncated' as const, text: safe.text }
      : projectUnifiedDiff(safe?.text);
    return [{ path, destination, description, projection }];
  }), [event]);
  return <section className={css.card} data-file-diff-state={state} aria-label="文件变更">
    <header><strong>{fileMutationLabel[state]}</strong><p>固定交付版本由交付卡单独核验。</p></header>
    {changes.length === 0 && <p className={css.note}>未提供差异。</p>}
    {changes.map(({ path, destination, description, projection }, index) => <section className={css.file} key={index}>
      <h4>{description && <span>{description} · </span>}<code>{path}</code>
        {destination && <> → <code>{destination}</code></>}</h4>
      <FileDiff projection={projection} />
    </section>)}
  </section>;
}
