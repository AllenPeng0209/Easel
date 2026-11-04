import { Component, type ReactNode } from 'react';
import { agentEventKey, type AgentEvent } from '../lib/agentEvents';
import type { ToolRendererResolution } from './toolRendererRegistry';
import ToolValueInspector from './ToolValueInspector';

interface BoundaryProps {
  observation: string;
  children: ReactNode;
  fallback: ReactNode;
}

interface BoundaryState { failed: boolean; observation: string }

/** Keep rendering faults inside the tool details so the call identity and descendants stay mounted. */
class ToolRenderBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { failed: false, observation: this.props.observation };

  static getDerivedStateFromProps(props: BoundaryProps, state: BoundaryState): BoundaryState | null {
    return props.observation === state.observation ? null : { failed: false, observation: props.observation };
  }

  static getDerivedStateFromError(): Partial<BoundaryState> {
    return { failed: true };
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function GenericToolDetails({ event }: { event: AgentEvent }) {
  return <>
    <ToolValueInspector label="输入" value={event.input} />
    <ToolValueInspector label="结果" value={event.output} />
    {event.exitCode !== undefined && <p className="agent-tool__exit">退出码：{event.exitCode === null ? '未提供' : event.exitCode}</p>}
    {event.truncated && <p className="agent-event__note">引擎输出已截断，以上不是完整日志。</p>}
  </>;
}

function FailedSpecialization({ event }: { event: AgentEvent }) {
  return <>
    <p className="agent-event__note" data-tool-renderer-fallback>专用详情暂不可用，已显示通用记录。</p>
    <GenericToolDetails event={event} />
  </>;
}

function SpecializedDetails({ details, showTerminalFacts }: {
  details: (showTerminalFacts: boolean) => ReactNode; showTerminalFacts: boolean;
}) {
  return details(showTerminalFacts);
}

export default function ToolRendererDetails({ event, renderer, showTerminalFacts }: {
  event: AgentEvent; renderer: ToolRendererResolution; showTerminalFacts: boolean;
}) {
  const observation = agentEventKey(event);
  return <ToolRenderBoundary observation={observation}
    fallback={<p className="agent-event__note" data-tool-renderer-unavailable>工具详情暂不可用。</p>}>
    {renderer.kind === 'specialized'
      ? <ToolRenderBoundary observation={JSON.stringify([renderer.key, observation])} fallback={<FailedSpecialization event={event} />}>
        <SpecializedDetails details={renderer.view.details} showTerminalFacts={showTerminalFacts} />
      </ToolRenderBoundary>
      : renderer.failed ? <FailedSpecialization event={event} /> : <GenericToolDetails event={event} />}
  </ToolRenderBoundary>;
}
