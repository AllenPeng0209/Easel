/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from ui-primitives/src/WebBlock.tsx @ ce7073d.
 * Easel renders native web action evidence without treating opaque results as citations.
 * License: integrations/deepseek-harness/LICENSE.
 */
import type { ReactNode } from 'react';
import type { WebCardModel } from './web-card-model';
import css from './WebBlock.module.css';

const evidenceLabel = {
  absent: '引擎未提供结果证据。',
  empty: '引擎返回了空结果列表。',
  observed: '引擎返回了结果记录。',
  failed: '引擎报告此网页操作失败。',
};

export function WebBlock({ model, renderResults }: { model: WebCardModel; renderResults: (value: unknown[]) => ReactNode }) {
  return <section className={css.block} data-web-action={model.action} data-web-evidence={model.evidence} aria-label="网页操作证据">
    {model.queries.length > 0 && <div>
      <h4>查询</h4>
      <ol className={css.queries}>{model.queries.map((query, index) => <li key={index} data-web-query>{query}</li>)}</ol>
    </div>}
    {model.action === 'search' && model.queries.length === 0 && !model.emptyQueryList && <p>引擎未提供查询词。</p>}
    {model.emptyQueryList && <p>引擎提供的批量查询列表为空。</p>}
    {(model.action === 'openPage' || model.action === 'findInPage') && <div className={css.source}>
      <h4>目标网页</h4>
      {model.url ? <a data-web-source href={model.url} target="_blank" rel="noopener noreferrer">{model.url}</a>
        : <p>{model.hasUrl ? '来源地址不可安全打开。' : '引擎未提供网页地址。'}</p>}
    </div>}
    {model.action === 'findInPage' && <div><h4>查找内容</h4><p>{model.pattern ?? '引擎未提供查找内容。'}</p></div>}
    <p className={css.evidence}>{evidenceLabel[model.evidence]}</p>
    {model.results && model.results.length > 0 && renderResults(model.results)}
    {model.truncated && <p className={css.evidence}>引擎记录已截断。</p>}
  </section>;
}
