/**
 * Split hunk pairing adapted from DeepSeek Harness
 * ui-deliverables/src/client/FileDiff.tsx @ ce7073d (MIT).
 * 响芽 pairs only validated snapshot diff rows, never mutable file text.
 */
import { FileDiff } from '../vendor/deepseek-harness/tool/FileDiff';
import type { DiffRow, ToolDiffProjection } from '../vendor/deepseek-harness/tool/file-diff';
import { useClipboardCopy } from '../vendor/deepseek-harness/message/use-clipboard-copy';
import css from './SnapshotTextDiff.module.css';

type Pair = { before?: DiffRow; after?: DiffRow };

function splitRows(lines: readonly DiffRow[]): Pair[] {
  const pairs: Pair[] = [];
  let deleted: DiffRow[] = [];
  let added: DiffRow[] = [];
  const flush = () => {
    for (let index = 0; index < Math.max(deleted.length, added.length); index += 1) {
      pairs.push({ before: deleted[index], after: added[index] });
    }
    deleted = [];
    added = [];
  };
  for (const row of lines) {
    if (row.kind === 'del') deleted.push(row);
    else if (row.kind === 'add') added.push(row);
    else {
      flush();
      pairs.push({ before: row, after: row });
    }
  }
  flush();
  return pairs;
}

function Cell({ row, side }: { row?: DiffRow; side: 'before' | 'after' }) {
  const number = side === 'before' ? row?.old : row?.new;
  return <td data-diff-side={side} data-diff-line={row?.kind} data-diff-empty={row ? undefined : true}
    data-old-line={side === 'before' ? number : undefined} data-new-line={side === 'after' ? number : undefined}>
    {row && <div className={css.cell}>
      <span className={css.lineNumber}>{number}</span>
      <span aria-hidden="true">{row.kind === 'add' ? '+' : row.kind === 'del' ? '−' : ' '}</span>
      <code>{row.text}</code>
    </div>}
  </td>;
}

function SplitSnapshotDiff({ projection }: { projection: Extract<ToolDiffProjection, { kind: 'unified' }> }) {
  const { copied, onCopy } = useClipboardCopy(projection.text);
  return <div className={css.diff} data-file-diff-format="unified" data-snapshot-text-diff>
    <div className={css.toolbar}>
      <span aria-label={`新增 ${projection.added} 行`} data-file-diff-added={projection.added}>+{projection.added}</span>
      <span aria-label={`删除 ${projection.removed} 行`} data-file-diff-removed={projection.removed}>−{projection.removed}</span>
      <button type="button" data-copy-file-diff onClick={onCopy}>{copied ? '已复制' : '复制差异'}</button>
    </div>
    {projection.hunks.map((hunk, index) => <details key={index} open={index === 0 && hunk.rows.length <= 24}>
      <summary data-diff-hunk-header>{hunk.header}</summary>
      <table aria-label="修改前后文本对比">
        <thead><tr><th scope="col">修改前</th><th scope="col">修改后</th></tr></thead>
        <tbody>{splitRows(hunk.rows).map((row, rowIndex) => <tr key={rowIndex}>
          <Cell side="before" row={row.before} />
          <Cell side="after" row={row.after} />
        </tr>)}</tbody>
      </table>
    </details>)}
  </div>;
}

export default function SnapshotTextDiff({ projection }: { projection: ToolDiffProjection }) {
  return projection.kind === 'unified' ? <SplitSnapshotDiff projection={projection} />
    : <FileDiff projection={projection} source="snapshot" />;
}
