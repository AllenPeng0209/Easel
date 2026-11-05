/**
 * Turn-tail file list and four-row disclosure adapted from DeepSeek Harness
 * ui-deliverables/src/client/ChangedFiles.tsx @ ce7073d (MIT).
 * 响芽 lists scoped snapshot evidence without implying file-opening authority.
 */
import { useId, useRef, useState } from 'react';
import type { AgentFileChanges } from '../lib/agentEvents';
import TurnChangeComparison from './TurnChangeComparison';
import './TurnFileChanges.css';

const COLLAPSED_ROWS = 4;
const format = new Intl.NumberFormat('zh-CN');
const changeLabels = { added: '新增', modified: '修改', deleted: '删除' };
const limitLabels = new Map([
  ['concurrent_observation', '期间存在并发变更'],
  ['scan_limit', '达到扫描限制'],
  ['unreadable', '部分文件无法读取'],
  ['summary_limit', '达到摘要条数限制'],
  ['unsupported', '部分对象不属于可统计范围'],
]);
const turnLabels = {
  completed: '',
  stopped: '本轮已停止。已核对的文件变化仍保留。',
  failed: '本轮执行失败。已核对的文件变化仍保留。',
  unconfirmed: '本轮执行结果未确认。',
};

function FileChangeCounts({ row }: { row: AgentFileChanges['files'][number] }) {
  if (row.diffStatus === 'binary') return <span>二进制文件 · 不计行数</span>;
  if (row.diffStatus === 'oversized') return <span>超出统计限制 · 行数未知</span>;
  if (row.diffStatus !== 'text' || row.added === undefined || row.deleted === undefined) return <span>行数未确认</span>;
  return <span className="turn-change-counts" aria-label={`增加 ${row.added} 行，删除 ${row.deleted} 行`}>
    <span data-change-added>+{format.format(row.added)}</span>
    <span data-change-deleted>−{format.format(row.deleted)}</span>
  </span>;
}

export default function TurnFileChanges({ changes, status, sessionId, turnId }: {
  changes: AgentFileChanges;
  status: keyof typeof turnLabels;
  sessionId?: string;
  turnId?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const [selectedPath, setSelectedPath] = useState<string>();
  const comparisonTrigger = useRef<HTMLButtonElement>(null);
  const selected = changes.files.find(row => row.path === selectedPath && row.compareAvailable);
  if (!changes.total && changes.coverage === 'complete') return null;
  const visible = expanded ? changes.files : changes.files.slice(0, COLLAPSED_ROWS);
  const limitations = changes.warnings.map(warning => limitLabels.get(warning)).filter(Boolean);
  return <section className="turn-file-changes" data-turn-file-changes aria-label="本轮文件变更">
    <header>
      <strong>{changes.total ? `观察到 ${format.format(changes.total)} 个文件变更` : '无法确认文件变更'}</strong>
      <p>当前 IP 输出目录的快照差异，可能包含共享工作区中的其他操作。</p>
    </header>
    {turnLabels[status] && <p data-change-turn-outcome>{turnLabels[status]}</p>}
    {changes.coverage === 'partial' && <p data-change-coverage>
      仅观察到部分范围。{limitations.length > 0 && limitations.join('、') + '。'}
    </p>}
    {!!visible.length && <ul id={listId}>
      {visible.map(row => <li key={row.path} data-changed-file data-change-kind={row.kind} data-change-diff-status={row.diffStatus}>
        <code title={row.path}>{row.path}</code>
        <span className="turn-change-kind">{changeLabels[row.kind]}</span>
        <FileChangeCounts row={row} />
        {row.compareAvailable && sessionId && turnId && <button type="button" className="btn btn-sm"
          data-compare-turn-file aria-label={`比较本轮文件：${row.path}`}
          onClick={event => { comparisonTrigger.current = event.currentTarget; setSelectedPath(row.path); }}>前后对比</button>}
      </li>)}
    </ul>}
    {selected && sessionId && turnId && <TurnChangeComparison key={`${turnId}:${selected.path}`}
      sessionId={sessionId} turnId={turnId} path={selected.path} kind={selected.kind}
      onClose={() => {
        setSelectedPath(undefined);
        if (comparisonTrigger.current?.isConnected) comparisonTrigger.current.focus({ preventScroll: true });
      }} />}
    {changes.files.length > COLLAPSED_ROWS && <button type="button" className="btn btn-sm"
      aria-expanded={expanded} aria-controls={listId} onClick={() => setExpanded(value => !value)}>
      {expanded ? '收起文件清单' : `查看已列出的 ${format.format(changes.files.length)} 个文件`}
    </button>}
  </section>;
}
