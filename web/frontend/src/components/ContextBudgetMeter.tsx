/**
 * Context meter ring adapted from DeepSeek Harness
 * ui-conversation/src/client/skeleton/ContextMeter.tsx @ ce7073d (MIT).
 * 响芽 displays observed provider counts and independent unknowns.
 */
import type { ContextBudget } from '../lib/contextBudget';

const RADIUS = 5.5;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const WARNING_PERCENT = 85;
const tokenFormat = new Intl.NumberFormat('zh-CN');
export default function ContextBudgetMeter({ budget, onNewChat }: { budget: ContextBudget; onNewChat?: () => void }) {
  const { usedTokens, contextWindow, percent } = budget;
  const used = usedTokens === undefined ? '用量未知' : `已用 ${tokenFormat.format(usedTokens)}`;
  const capacity = contextWindow === undefined ? '容量未知' : `容量 ${tokenFormat.format(contextWindow)}`;
  const counts = `${used} / ${capacity}${usedTokens !== undefined || contextWindow !== undefined ? ' tokens' : ''}`;
  const warning = usedTokens !== undefined && contextWindow !== undefined && usedTokens / contextWindow * 100 >= WARNING_PERCENT;
  return <div className="composer-context-budget" data-context-budget data-context-warning={warning || undefined}>
    <div className="composer-context-reading">
      {percent !== undefined && <span className="composer-context-meter" role="meter"
        aria-label="上下文占用" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}
        aria-valuetext={`${percent}% · ${counts}`}>
        <svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">
          <circle className="composer-context-track" cx="7" cy="7" r={RADIUS} />
          <circle className="composer-context-fill" cx="7" cy="7" r={RADIUS}
            strokeDasharray={`${CIRCUMFERENCE * percent / 100} ${CIRCUMFERENCE}`}
            transform="rotate(-90 7 7)" />
        </svg>
        {percent}%
      </span>}
      <span>上下文 · {counts}</span>
    </div>
    {warning && <div className="composer-context-advice">
      <span data-context-budget-warning>{usedTokens >= contextWindow ? '上下文已达到上限。' : '上下文接近上限。'}可将必要信息整理后放进新对话。</span>
      {onNewChat && <button className="composer-attach-btn" type="button" data-context-new-chat onClick={onNewChat}>新建对话</button>}
    </div>}
  </div>;
}
