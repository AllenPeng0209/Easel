import { commandExitCode, commandSignal, type AgentEvent } from '../lib/agentEvents';
import { inspectTerminalText } from '../lib/toolValueInspection';
import { TerminalBlock } from '../vendor/deepseek-harness/tool/TerminalBlock';

const OUTPUT_LABEL = 'stdout / stderr（合并）';

export default function CommandExecutionCard({ event, showExit }: { event: AgentEvent; showExit: boolean }) {
  const input = event.input as { command?: string; cwd?: unknown } | undefined;
  const command = input?.command === undefined ? undefined : inspectTerminalText(input.command);
  const cwd = typeof input?.cwd === 'string' ? inspectTerminalText(input.cwd) : undefined;
  const output = typeof event.output === 'string' ? inspectTerminalText(event.output, true) : undefined;
  const code = commandExitCode(event);
  const exit = showExit || code !== undefined ? `退出码：${code === undefined ? '当时未记录' : code === null ? '引擎未提供' : code}` : undefined;
  const duration = typeof event.durationMs === 'number' && Number.isSafeInteger(event.durationMs) && event.durationMs >= 0
    ? `耗时：${event.durationMs} ms` : undefined;
  const signal = commandSignal(event);
  const facts = [exit, duration, signal === undefined ? undefined : `终止信号：${signal}`].filter(Boolean);
  const notice = [event.truncated ? '引擎输出已截断，以上不是完整日志。' : undefined,
    command?.truncated || output?.truncated || cwd?.truncated ? '详情已截断，仅显示保留的部分。' : undefined]
    .filter(Boolean).join(' ');
  const diagnostics = [
    `命令：${command?.text ?? '当时未记录'}`,
    cwd ? `工作目录：${cwd.text}` : undefined,
    ...facts,
    OUTPUT_LABEL,
    output?.text ?? '当时未记录',
    notice || undefined,
  ].filter(line => line !== undefined).join('\n');
  return <TerminalBlock command={command?.text} cwd={cwd?.text} output={output?.text} outputLabel={OUTPUT_LABEL}
    facts={facts.length > 0 ? <p className="agent-tool__exit">{facts.join(' · ')}</p> : null}
    copyText={diagnostics} notice={notice || undefined} />;
}
