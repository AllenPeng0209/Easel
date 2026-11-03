/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from deepseek-ai/deepseek-harness @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Upstream: packages/client/ui-primitives/src/TerminalBlock.tsx.
 * Easel supplies scrubbed plain text and observed execution facts.
 * License: integrations/deepseek-harness/LICENSE.
 */
import { useId, useMemo, useState, type ReactNode } from 'react';
import { headTailCap } from './head-tail-cap';
import { useClipboardCopy } from '../message/use-clipboard-copy';
import css from './TerminalBlock.module.css';

const MAX_LINES = 16;

export function TerminalBlock({ command, cwd, output, outputLabel, facts, copyText, notice }: {
  command?: string; cwd?: string; output?: string; outputLabel: string; facts: ReactNode;
  copyText: string; notice?: string;
}) {
  const outputId = useId();
  const [expanded, setExpanded] = useState(false);
  const { copied, onCopy } = useClipboardCopy(copyText);
  const lines = useMemo(() => {
    const body = output?.endsWith('\n') ? output.slice(0, -1) : output;
    return body ? body.split('\n') : [];
  }, [output]);
  const { hidden, capped, headLines, tailLines } = headTailCap(lines.length, MAX_LINES, expanded);

  return <div className={css.block} data-terminal>
    <div className={css.header}>
      <div className={css.prompt}>
        <h4>命令</h4>
        {command === undefined ? <p>当时未记录</p> : <pre><span aria-hidden="true">$ </span>{command || '（空命令）'}</pre>}
        {cwd !== undefined && <p className={css.cwd}>工作目录 <code>{cwd}</code></p>}
      </div>
      <button className={css.action} type="button" data-copy-terminal onClick={onCopy}>
        {copied ? '已复制' : '复制诊断'}
      </button>
    </div>
    {facts}
    <div className={css.output} id={outputId}>
      <h4>{outputLabel}</h4>
      {lines.length > 0 ? <pre>{(capped ? lines.slice(0, headLines) : lines).join('\n')}</pre>
        : <p>{output === undefined ? '当时未记录' : '没有输出'}</p>}
      {hidden > 0 && <button type="button" className={css.action} data-terminal-expand
        aria-expanded={expanded} aria-controls={outputId} onClick={() => setExpanded(value => !value)}>
        {expanded ? '折叠输出' : `展开剩余 ${hidden} 行`}
      </button>}
      {capped && <pre>{lines.slice(lines.length - tailLines).join('\n')}</pre>}
    </div>
    {notice && <p className={css.notice}>{notice}</p>}
  </div>;
}
