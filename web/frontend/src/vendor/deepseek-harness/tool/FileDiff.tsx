/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from ui-deliverables/src/client/FileDiff.tsx @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel renders the engine's bounded unified patch, with native hunk disclosure.
 * License: integrations/deepseek-harness/LICENSE.
 */
import { useClipboardCopy } from '../message/use-clipboard-copy';
import type { projectUnifiedDiff } from './file-diff';
import css from './FileDiff.module.css';

type DiffProjection = ReturnType<typeof projectUnifiedDiff>;
const marker = { add: '+', del: '-', context: ' ' };

export function FileDiff({ projection }: { projection: DiffProjection }) {
  const { copied, onCopy } = useClipboardCopy(projection.kind === 'missing' ? '' : projection.text);
  if (projection.kind === 'missing') return <p className={css.note} data-file-diff-format="missing">未提供差异。</p>;

  const unified = projection.kind === 'unified';
  const label = unified ? '复制差异' : '复制引擎文本';
  return <div className={css.diff} data-file-diff-format={projection.kind}>
    <div className={css.toolbar}>
      {unified ? <span className={css.count}>
        <span aria-label={`新增 ${projection.added} 行`} data-file-diff-added={projection.added}>+{projection.added}</span>{' '}
        <span aria-label={`删除 ${projection.removed} 行`} data-file-diff-removed={projection.removed}>−{projection.removed}</span>
      </span> : <span className={css.note}>
        {projection.reason === 'truncated' ? '差异已截断，以下为保留的引擎文本。' : '引擎文本无法按统一差异解析。'}
      </span>}
      <button type="button" className={css.copy} data-copy-file-diff onClick={onCopy} aria-label={label}>
        {copied ? '已复制' : label}
      </button>
    </div>
    {unified ? projection.hunks.map((hunk, position) => <details key={position} className={css.hunk}
      open={position === 0 && hunk.rows.length <= 24}>
      <summary className={css.hunkHeader} data-diff-hunk-header>{hunk.header}</summary>
      <div className={css.rows}>
        {hunk.rows.map((row, index) => <div key={index} className={`${css.row} ${css[row.kind]}`}
          data-diff-line={row.kind} data-old-line={row.old} data-new-line={row.new}>
          <span className={css.number}>{row.old ?? ''}</span><span className={css.number}>{row.new ?? ''}</span>
          <span aria-hidden="true" className={css.marker}>{marker[row.kind]}</span>
          <code className={css.text}>{row.text}</code>
        </div>)}
      </div>
    </details>) : <pre className={css.raw}>{projection.text}</pre>}
  </div>;
}
