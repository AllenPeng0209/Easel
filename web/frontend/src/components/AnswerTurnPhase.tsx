/**
 * Separate answer and turn indicators adapted from the DeepSeek Harness
 * ui-trajectory request/assistant status presentation @ ce7073d (MIT).
 * 响芽 uses the Host's explicit completion observation for each state.
 */
import css from './AnswerTurnPhase.module.css';

const liveLabels: Record<string, string> = {
  running: '本轮进行中', recovering: '本轮正在恢复', waiting_input: '本轮等待输入',
  stopping: '本轮正在停止', stop_failed: '本轮停止待核实',
};
const settledLabels: Record<string, string> = {
  completed: '本轮已完成', failed: '本轮执行失败', stopped: '本轮已停止', unconfirmed: '本轮结果未确认',
};

export default function AnswerTurnPhase({ answerCompleted, active, lifecycle, outcome }: {
  answerCompleted: boolean;
  active: boolean;
  lifecycle?: string;
  outcome?: string;
}) {
  const labels = active ? liveLabels : settledLabels;
  const reported = active ? lifecycle : outcome;
  const phase = typeof reported === 'string' && Object.hasOwn(labels, reported) ? reported : active ? 'running' : undefined;
  if (!answerCompleted && !phase) return null;
  return <div className={css.strip} aria-label="答复与执行状态" aria-live="polite">
    {answerCompleted && <span className={css.answer} data-answer-phase="completed">答复已完成</span>}
    {phase && <span className={css.turn} data-turn-phase={phase}>{labels[phase]}</span>}
  </div>;
}
