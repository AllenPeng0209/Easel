import { Fragment, useMemo, useRef } from 'react';
import type { AnswerCompletion, ChatMessage, ReasoningSummary, StreamState } from '../lib/store';
import type { AgentTimelineObservation } from '../lib/agentTimeline';
import type { AgentEvent, AgentSubagent } from '../lib/agentEvents';
import { IconCopy, IconCheck, IconRetry } from './icons';
import ArtifactGallery from './ArtifactGallery';
import DeliverableCards from './DeliverableCards';
import { deliverablesFromEvents, legacyArchivesForDeliverables, omitDeclaredArtifacts,
  rejectedSnapshotPaths } from '../lib/deliverables';
import UserAttachments from './UserAttachments';
import { validateChatCommand } from '../lib/chatCommands';
import { validateChatReferences, validateChatSessionReferenceSpans } from '../lib/chatReferences';
import { attachmentHref } from '../vendor/deepseek-harness/message/attachment-reference';
import AgentEventPanel, { type AgentToolLocator } from './AgentEventPanel';
import AgentTimeline from './AgentTimeline';
import AnswerQuoteSelection from './AnswerQuoteSelection';
import { useAnswerMarkdown } from './answerMarkdown';
import { formatQuoteText, messageQuoteRanges, quoteTextOf } from '../vendor/deepseek-harness/composer/quote-text';
import quoteCss from './AnswerQuoteSelection.module.css';
import ReasoningSummaryDisclosure from './ReasoningSummaryDisclosure';
import AnswerTurnPhase from './AnswerTurnPhase';
import ChatFailureNotice from './ChatFailureNotice';
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
  sessionId?: string;
  isStreaming?: boolean;
  reasoningSummary?: ReasoningSummary;
  answerCompletion?: AnswerCompletion;
  turnLifecycle?: StreamState['lifecycle'];
  activity?: string;
  stillWorking?: string;   // 防呆心跳提示（未卡住）；仅流式时的独立提示，不替代思考/活动
  events?: AgentEvent[];
  timeline?: AgentTimelineObservation[];
  pendingTimelineQuestions?: number;
  detailMode?: 'compact' | 'detailed';
  foldWorkDetails?: boolean;
  actions?: BubbleActions;
  onOpenFailureSettings?: () => void;
  onQuoteAnswer?: (text: string) => boolean;
  onOpenSubagent?: (child: AgentSubagent, trigger: HTMLElement) => void;
  onOpenDeliverable?: (file: import('../lib/deliverables').ChatDeliverable, trigger: HTMLElement) => void;
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

function UserText({ message }: { message: ChatMessage }) {
  const { content, attachments = [] } = message;
  const fileReferences = validateChatReferences(content, message.references, attachments) || [];
  const sessionReferences = validateChatSessionReferenceSpans(content, message.sessionReferences, fileReferences) || [];
  const references = [...fileReferences, ...sessionReferences].sort((a, b) => a.start - b.start);
  const command = validateChatCommand(content, message.command);
  const leadingCommand = command && !references.some(ref => ref.start < command.token.length) ? command : null;
  const quotes = messageQuoteRanges(content).flatMap(range => {
    if (range.start < (leadingCommand?.token.length ?? 0) ||
        references.some(reference => reference.start < range.end && range.start < reference.end)) return [];
    const raw = content.slice(range.start, range.end);
    const quoteText = quoteTextOf(raw);
    return formatQuoteText(quoteText) === raw ? [{ ...range, quoteText }] : [];
  });
  const spans = [...references, ...quotes].sort((left, right) => left.start - right.start);
  if (!spans.length && !leadingCommand) return content;
  const files = new Map(attachments.map(file => [file.id, file]));
  let offset = leadingCommand?.token.length || 0;
  const parts = spans.map(reference => {
    const prefix = content.slice(offset, reference.start);
    const text = content.slice(reference.start, reference.end);
    offset = reference.end;
    if ('quoteText' in reference) return <Fragment key={reference.start}>{prefix}
      <span className={quoteCss.savedQuote} data-chat-quote>{reference.quoteText}</span>
    </Fragment>;
    if ('sessionId' in reference) {
      return <Fragment key={reference.start}>{prefix}
        <span className="message-inline-reference" data-chat-session-reference={reference.sessionId}
          title="引用的会话快照">{text}</span>
      </Fragment>;
    }
    const file = files.get(reference.attachmentId);
    const href = file ? attachmentHref(file.path) : null;
    return <Fragment key={reference.start}>{prefix}
      {href && file ? <a className="message-inline-reference" data-chat-reference={file.id} href={href}
        target="_blank" rel="noreferrer" aria-label={`打开正文附件：${file.name}`}>{text}</a> : text}
    </Fragment>;
  });
  return <>{leadingCommand && <span className="message-inline-reference" data-chat-command={leadingCommand.id}
    title="已选命令">{leadingCommand.token}</span>}{parts}{content.slice(offset)}</>;
}

/** Legacy turns appended an unstructured provider error after the visible answer. */
function visibleAssistantContent(message: ChatMessage): string {
  if (message.role !== 'assistant' ||
      (message.status !== 'failed' && message.status !== 'unconfirmed' && !message.failure)) return message.content;
  if (/^\s*Error:/.test(message.content)) return '';
  const error = message.content.search(/\r?\n\r?\nError:/);
  return error === -1 ? message.content : message.content.slice(0, error);
}

export default function MessageBubble({ message, sessionId, isStreaming, reasoningSummary, answerCompletion, turnLifecycle, activity, stillWorking, events, timeline, pendingTimelineQuestions, detailMode, foldWorkDetails, actions, onOpenFailureSettings, onQuoteAnswer, onOpenDeliverable, onOpenSubagent }: MessageBubbleProps) {
  const toolLocator = useRef<AgentToolLocator>(null);
  const effEvents = isStreaming ? events || EMPTY_EVENTS : message.events || EMPTY_EVENTS;
  const recordedTimeline = isStreaming ? timeline : message.timeline;
  const effTimeline = Array.isArray(recordedTimeline) ? recordedTimeline : undefined;
  const waitingQuestions = isStreaming ? pendingTimelineQuestions
    : message.questions?.filter(question => question.status === 'pending').length;
  const declared = useMemo(() => deliverablesFromEvents(effEvents), [effEvents]);
  const assistantContent = visibleAssistantContent(message);
  const rejectedPaths = useMemo(() => rejectedSnapshotPaths(effEvents), [effEvents]);
  const rendered = useAnswerMarkdown(message.role === 'assistant' ? assistantContent : '',
    isStreaming === true, declared, rejectedPaths);
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
          {message.content.trim() && <UserMessageBubble className="message-bubble user"><UserText message={message} /></UserMessageBubble>}
          {actions && message.content.trim() && <ActionBar actions={actions} text={message.content} />}
        </div>
      </div>
    );
  }

  // ---- 助手消息 ----
  const answerItem = (isStreaming ? answerCompletion : message.answerCompletion)?.itemId;
  const answerCompleted = typeof answerItem === 'string' && !!answerItem.trim()
    && answerItem.length <= 512 && !/\p{Cc}/u.test(answerItem);
  const phase = <AnswerTurnPhase answerCompleted={answerCompleted} active={isStreaming === true}
    lifecycle={turnLifecycle} outcome={message.status} />;
  const failedTurn = !isStreaming && (message.status === 'failed' || !!message.failure);
  const uncertainTurn = !isStreaming && message.status === 'unconfirmed';
  const hasPublicationReceipt = effEvents.some(event => event.kind === 'publication' && event.publication);
  const suppliedSummary = isStreaming ? reasoningSummary : message.reasoningSummary;
  const workDetailsOpen = detailMode === undefined ? undefined : detailMode === 'detailed' || foldWorkDetails === false;
  const publicSummary = typeof suppliedSummary?.text === 'string' && typeof suppliedSummary.truncated === 'boolean'
    && (suppliedSummary.text.trim() || suppliedSummary.truncated) ? suppliedSummary : undefined;
  const liveActivity = isStreaming ? (activity || '') : '';
  const liveHint = isStreaming ? (stillWorking || '') : '';   // 防呆「未卡住」提示，附着显示、不顶掉真实状态
  const doneSteps = !isStreaming && !effTimeline?.length ? (message.activity || '') : '';

  const livePanel = (liveActivity || liveHint || doneSteps) ? (
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
          <summary>🧠 执行记录（历史文本）</summary>
          <div className="thinking-text">{doneSteps}</div>
        </details>
      )}

    </div>
  ) : null;

  // 等待回复中，还未收到答复或其他可见执行记录。
  if (isStreaming && !answerCompleted && !assistantContent && !publicSummary && !liveActivity && !liveHint && !effEvents.length && !effTimeline?.length) {
    return (
      <div className="message-row assistant">
        <div className="message-bubble assistant">
          {phase}
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
          {phase}
          {failedTurn && <ChatFailureNotice failure={message.failure} onOpenSettings={onOpenFailureSettings}
            onRetry={actions?.canModify && !uncertainTurn && !hasPublicationReceipt ? actions.onRetry : undefined} />}
          {livePanel}
          {publicSummary && <ReasoningSummaryDisclosure summary={publicSummary} defaultOpen={workDetailsOpen} />}
          {effTimeline?.length ? <AgentTimeline observations={effTimeline} pendingQuestions={waitingQuestions} defaultOpen={workDetailsOpen}
            onLocateTool={(runId, callId) => toolLocator.current?.locate(runId, callId) ?? false} /> : null}
          <AgentEventPanel toolLocatorRef={toolLocator} defaultOpen={workDetailsOpen} sessionId={sessionId} onOpenSubagent={onOpenSubagent} events={effEvents.filter(event => event.kind !== 'artifact')} settled={!isStreaming} />
          {assistantContent && <AnswerQuoteSelection blocks={rendered.blocks} highlightCode={!isStreaming} settled={!isStreaming}
            onQuoteAnswer={isStreaming ? undefined : onQuoteAnswer} />}
          {rejectedPaths.length > 0 && <p className="artifact-hint" role="alert">交付版本信息不完整，已停用相关预览和下载。</p>}
          <DeliverableCards artifacts={declared} onOpenDeliverable={onOpenDeliverable} />
          {!isStreaming && (legacyArtifacts.length > 0 || legacyArchives.length > 0) && (
            <ArtifactGallery artifacts={legacyArtifacts} archives={legacyArchives} />
          )}
          {isStreaming && !answerCompleted && <span className="streaming-cursor" />}
        </div>
        {actions && !isStreaming && (!(failedTurn || uncertainTurn) || assistantContent) &&
          <ActionBar actions={failedTurn || uncertainTurn ? { ...actions, onRetry: undefined } : actions} text={assistantContent} />}
      </div>
    </div>
  );
}
