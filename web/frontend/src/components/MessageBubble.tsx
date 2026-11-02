import { useMemo } from 'react';
import type { ChatMessage } from '../lib/store';
import type { AgentEvent } from '../lib/agentEvents';
import { renderMarkdown } from '../lib/sanitize';
import { linkifyOutputs, externalizeMediaLinks } from '../lib/linkifyOutputs';
import { IconCopy, IconCheck, IconRetry } from './icons';
import { extractArtifacts } from '../lib/artifacts';
import ArtifactGallery from './ArtifactGallery';
import DeliverableCards from './DeliverableCards';
import { deliverablesFromEvents, legacyArchivesForDeliverables, omitDeclaredArtifacts,
  pinSnapshotLinks, rejectedSnapshotPaths } from '../lib/deliverables';
import UserAttachments from './UserAttachments';
import AgentEventPanel from './AgentEventPanel';
import { UserMessageBubble } from '../vendor/deepseek-harness/message/UserMessageBubble';
import { useClipboardCopy } from '../vendor/deepseek-harness/message/use-clipboard-copy';
import '../styles/agent-chat.css';

const EMPTY_EVENTS: AgentEvent[] = [];

export interface BubbleActions {
  onCopy: () => void | Promise<void>;
  onRetry?: () => void;    // 仅最后一轮可用（append-only：不改写历史）
  canModify: boolean;      // 流式中禁用 retry
}

interface MessageBubbleProps {
  message: ChatMessage;
  isStreaming?: boolean;
  thinking?: string;
  activity?: string;
  stillWorking?: string;   // 防呆心跳提示（未卡住）；仅流式时的独立提示，不替代思考/活动
  events?: AgentEvent[];
  actions?: BubbleActions;
}

function ActionBar({ actions, text }: { actions: BubbleActions; text: string }) {
  // Harness's shared hook waits for an accepted clipboard write, including its
  // fallback, before success feedback. The legacy callback was copy-only.
  const { copied, onCopy } = useClipboardCopy(text);
  return (
    <div className="msg-actions">
      <button className="msg-action" onClick={onCopy} title="复制">
        {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}<span>{copied ? '已复制' : '复制'}</span>
      </button>
      {actions.onRetry && actions.canModify && (
        <button className="msg-action" onClick={actions.onRetry} title="重新生成"><IconRetry size={14} /><span>重试</span></button>
      )}
    </div>
  );
}

export default function MessageBubble({ message, isStreaming, thinking, activity, stillWorking, events, actions }: MessageBubbleProps) {
  const effEvents = isStreaming ? events || EMPTY_EVENTS : message.events || EMPTY_EVENTS;
  const declared = useMemo(() => deliverablesFromEvents(effEvents), [effEvents]);
  const rejectedPaths = useMemo(() => rejectedSnapshotPaths(effEvents), [effEvents]);
  const rendered = useMemo(() => {
    if (message.role === 'user' || !message.content) return { html: '', artifacts: [], archives: [] };
    // 助手正文里裸露的产物路径先转成前端可用链接（文件直开、图片内联、目录跳内容库）。
    // 流式中不转换：路径/围栏代码块可能被 SSE token 从中间切开（扩展名没收全会被
    // 误判成目录、未闭合的 ``` 会误伤命令示例里的 outputs/），转换只在流式结束后
    // 对完整正文做一次，避免闪烁成错误链接再跳变。
    const body = isStreaming ? message.content : linkifyOutputs(message.content);
    const html = pinSnapshotLinks(externalizeMediaLinks(renderMarkdown(body)), declared, rejectedPaths, !isStreaming);
    return isStreaming ? { html, artifacts: [], archives: [] } : extractArtifacts(html);
  }, [message.content, message.role, isStreaming, declared, rejectedPaths]);
  const legacyArtifacts = useMemo(() => omitDeclaredArtifacts(rendered.artifacts, declared, rejectedPaths), [rendered.artifacts, declared, rejectedPaths]);
  // Legacy ZIPs retain D22's current-file index. Snapshots use their bound member API.
  const legacyArchives = useMemo(() => legacyArchivesForDeliverables(rendered.archives, declared, rejectedPaths), [rendered.archives, declared, rejectedPaths]);

  // ---- 用户消息 ----
  if (message.role === 'user') {
    if (!message.content.trim() && !message.attachments?.length) return null;
    return (
      <div className="message-row user">
        <div className="msg-col user">
          {message.attachments?.length ? <UserAttachments files={message.attachments} /> : null}
          {message.content.trim() && <UserMessageBubble className="message-bubble user">{message.content}</UserMessageBubble>}
          {actions && message.content.trim() && <ActionBar actions={actions} text={message.content} />}
        </div>
      </div>
    );
  }

  // ---- 助手消息 ----
  // 思考 / 活动：流式时用实时值；结束后用消息里持久化的值 —— 一直保留，不隐藏
  const effThinking = isStreaming ? (thinking || '') : (message.thinking || '');
  const liveActivity = isStreaming ? (activity || '') : '';
  const liveHint = isStreaming ? (stillWorking || '') : '';   // 防呆「未卡住」提示，附着显示、不顶掉真实状态
  const doneSteps = !isStreaming ? (message.activity || '') : '';

  const livePanel = (effThinking || liveActivity || liveHint || doneSteps) ? (
    <div className="live-panel">
      {liveActivity ? (
        <div className="live-activity">
          <span className="live-pulse" />{liveActivity}
          {liveHint && <span className="live-still"> · {liveHint}</span>}
        </div>
      ) : liveHint ? (
        <div className="live-activity"><span className="live-pulse" />{liveHint}</div>
      ) : null}
      {doneSteps && !effEvents.length && (
        <details className="thinking-block">
          <summary>🧠 执行过程（{doneSteps.split('\n').length} 步）</summary>
          <div className="thinking-text">{doneSteps}</div>
        </details>
      )}
      {effThinking && (
        <details className="thinking-block" open={isStreaming && !message.content}>
          <summary>💭 思考过程</summary>
          <div className="thinking-text">{effThinking}</div>
        </details>
      )}
    </div>
  ) : null;

  // 等待回复中（还没有正文、思考、活动）
  if (isStreaming && !message.content && !effThinking && !liveActivity && !liveHint && !effEvents.length) {
    return (
      <div className="message-row assistant">
        <div className="message-bubble assistant">
          <div className="typing-indicator">
            <span className="typing-dot" />
            <span className="typing-dot" />
            <span className="typing-dot" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="message-row assistant">
      <div className="msg-col assistant">
        <div className="message-bubble assistant">
          {livePanel}
          <AgentEventPanel events={effEvents.filter(event => event.kind !== 'artifact')} settled={!isStreaming} />
          {message.content && <div dangerouslySetInnerHTML={{ __html: rendered.html }} />}
          {rejectedPaths.length > 0 && <p className="artifact-hint" role="alert">交付版本信息不完整，已停用相关预览和下载。</p>}
          <DeliverableCards artifacts={declared} />
          {!isStreaming && (legacyArtifacts.length > 0 || legacyArchives.length > 0) && (
            <ArtifactGallery artifacts={legacyArtifacts} archives={legacyArchives} />
          )}
          {isStreaming && <span className="streaming-cursor" />}
        </div>
        {actions && !isStreaming && <ActionBar actions={actions} text={message.content} />}
      </div>
    </div>
  );
}
