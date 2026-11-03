import { projectUrl, projectFetch } from '../lib/projectScope';
import { useState, useRef, useEffect, useCallback, useLayoutEffect } from 'react';
import type { ReactNode } from 'react';
import ForkChatDialog, { type CreatedChatFork } from './ForkChatDialog';
import MessageBubble from './MessageBubble';
import DeliverablePreviewPane, { ChatInspectionPane } from './DeliverablePreviewPane';
import { SubagentTranscript } from './AgentSubagentLineage';
import type { AgentSubagent } from '../lib/agentEvents';
import QuestionCards from './QuestionCards';
import BrushEntry from './BrushEntry';
import StructuredComposerEditor, { type StructuredEditorHandle } from './StructuredComposerEditor';
import { chatCommandOverlapsReferences, validateChatCommand, type ChatCommand } from '../lib/chatCommands';
import {
  attachmentReferenceLabel, MAX_CHAT_REFERENCES, validateChatReferences,
  validateChatSessionReferences, type ChatDraftSessionReference, type ChatReference,
} from '../lib/chatReferences';
import type { ChatSession, ChatMessage, QueuedMessage, StreamState } from '../lib/store';
import { uploadFiles, adoptOversize } from '../lib/api';
import type { ChatQuestionClosed, UploadedFile } from '../lib/api';
import type { ChatDeliverable } from '../lib/deliverables';
import { IconArrowUp, IconStop, IconPlus, IconFile } from './icons';
import { AttachmentUploadQueue } from '../lib/uploadQueue';
import { validateRestoredAttachments } from '../lib/restoredAttachments';
import { loadChatDraft, saveChatDraft, consumeChatDraft } from '../lib/chatDrafts';
import { useChatReading } from '../lib/chatReading';
import { loadWorkDetailsPreferences, saveWorkDetailsPreferences, type WorkDetailsPreferences } from '../lib/chatWorkDetails';
import { attachmentHref } from '../vendor/deepseek-harness/message/attachment-reference';
import { fileSizeText } from '../vendor/deepseek-harness/composer/file-size';
import { ImageLightbox } from '../vendor/deepseek-harness/composer/ImageLightbox';
import SessionModelControl from './SessionModelControl';
import ContextBudgetMeter from './ContextBudgetMeter';
import { readContextBudget } from '../lib/contextBudget';
import type { SessionModelCapability } from '../lib/api';
import '../styles/composer.css';

interface ChatPageProps {
  apiDirect: boolean;
  session: ChatSession;
  modelCapability?: SessionModelCapability;
  nextModel?: string | null;
  turnModel?: string;
  onChangeModel?: (model: string | null) => void;
  modelLoading?: boolean;
  onReloadModels?: () => void;
  onNewContextChat?: () => void;
  stream?: StreamState;          // 进行中的流式态（来自 App，切页也不丢）
  onSend: (displayText: string, attachments?: UploadedFile[], references?: ChatReference[],
    sessionReferences?: ChatDraftSessionReference[], command?: ChatCommand) => boolean | Promise<boolean>;
  onSendSuperagent?: (text: string) => Promise<boolean>;
  superagentTasks?: ReactNode;
  onSteer?: (text: string) => Promise<boolean>;
  onStop: () => void;
  onResend: (userIndex: number) => void; // 仅最后一轮，追加新请求
  onEditHistory?: (sessionId: string, userIndex: number) => Promise<void>;
  onLoadEarlier?: (sessionId: string, before: string) => Promise<void>;
  onForkAnswer?: (messageIndex: number) => Promise<CreatedChatFork | null>;
  onOpenFork?: (id: string) => void;
  onQuestionAnswered?: (receipt: ChatQuestionClosed) => void;
  queue?: QueuedMessage[];
  onEditQueued?: (id: string, text: string) => boolean;
  onQueueEditChange?: (id: string | null) => void;
  onRemoveQueued?: (id: string) => void;
  queuePaused?: boolean;
  onResumeQueue?: () => void;
  historyNotice?: string;
  onOpenFailureSettings?: () => void;
  admissionNotice?: { message: string; action?: { label: string; run: () => void } };
}

// 空态推荐（贴合 Easel 社媒创作场景）
const SUGGESTIONS = [
  { icon: '🔥', title: '蹭个热点', prompt: '看看现在微博和抖音有什么热搜，挑几个适合我做二创的选题' },
  { icon: '✍️', title: '写小红书文案', prompt: '基于当前 IP 的定位选择合适主题，写一条小红书文案初稿，普通偏好采用合理默认值并说明。' },
  { icon: '🎴', title: '做金句卡片', prompt: '把一句走心的话做成一张适合发朋友圈的金句卡片' },
  { icon: '🎬', title: '口播脚本', prompt: '基于当前 IP 的定位，完成一条 60 秒口播短视频脚本，先采用合适主题和默认风格做一版初稿。' },
];

function greeting(): string {
  const h = new Date().getHours();
  const g = h < 6 ? '夜深了' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
  return `${g}，想创作点什么？`;
}

export default function ChatPage({ apiDirect, session, stream, onSend, onSendSuperagent, superagentTasks, onSteer, onStop, onResend, onEditHistory, onLoadEarlier, onForkAnswer, onOpenFork, onQuestionAnswered,
  modelCapability, nextModel, turnModel, onChangeModel, modelLoading, onReloadModels, onNewContextChat, queue = [], onRemoveQueued, onEditQueued, onQueueEditChange, queuePaused, onResumeQueue, historyNotice, admissionNotice, onOpenFailureSettings }: ChatPageProps) {
  const [forkTarget, setForkTarget] = useState<{ sessionId: string; index: number; turnId: string; answer: string }>();
  useEffect(() => setForkTarget(undefined), [session.id]);
  const [historyEdit, setHistoryEdit] = useState<{ sessionId: string; index: number; pending: boolean; error?: string }>();
  const historyEditOwner = useRef<{ sessionId: string; index: number } | undefined>(undefined);
  useEffect(() => {
    setHistoryEdit(undefined);
    return () => {
      if (historyEditOwner.current?.sessionId === session.id) historyEditOwner.current = undefined;
    };
  }, [session.id]);
  const editHistoricalMessage = async (index: number) => {
    if (!onEditHistory || historyEditOwner.current?.sessionId === session.id) return;
    const owner = { sessionId: session.id, index };
    historyEditOwner.current = owner;
    setHistoryEdit({ ...owner, pending: true });
    try {
      await onEditHistory(owner.sessionId, index);
      if (historyEditOwner.current === owner) setHistoryEdit(undefined);
    } catch (error) {
      if (historyEditOwner.current === owner) {
        setHistoryEdit({ ...owner, pending: false,
          error: error instanceof Error && error.message ? error.message : '无法从此消息创建修订分支，请稍后重试。' });
      }
    } finally {
      if (historyEditOwner.current === owner) historyEditOwner.current = undefined;
    }
  };
  const [queueEdit, setQueueEdit] = useState<{ id: string; text: string } | null>(null);
  const queueEditChange = useRef(onQueueEditChange);
  queueEditChange.current = onQueueEditChange;
  useEffect(() => () => queueEditChange.current?.(null), [session.id]);
  const changeQueueEdit = (next: typeof queueEdit) => {
    setQueueEdit(next);
    queueEditChange.current?.(next?.id ?? null);
  };
  useEffect(() => {
    if (queueEdit && !queue.some(item => item.id === queueEdit.id && item.delivery !== 'sending' && item.delivery !== 'unconfirmed')) {
      setQueueEdit(null);
      queueEditChange.current?.(null);
    }
  }, [queue, queueEdit]);
  const [initialDraft] = useState(() => loadChatDraft(session.id));
  const [composerId] = useState(() => crypto.getRandomValues(new Uint32Array(4)).join('-'));
  const [restoredKeys] = useState(() => new Set(initialDraft.attachments.map(item => item.key)));
  const [restoreState, setRestoreState] = useState<'checking' | 'complete' | 'unconfirmed'>(
    restoredKeys.size ? 'checking' : 'complete');
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const [{ text: input, references, sessionReferences, command }, setDraftContent] = useState(() => ({
    text: initialDraft.text, references: initialDraft.references ?? [], sessionReferences: initialDraft.sessionReferences ?? [],
    command: initialDraft.command,
  }));
  const draftContentRef = useRef({ text: input, references, sessionReferences, command });
  const replaceInput = (text: string, nextReferences: ChatReference[] = [],
    nextSessionReferences: ChatDraftSessionReference[] = [], nextCommand?: ChatCommand) => {
    const next = { text, references: nextReferences, sessionReferences: nextSessionReferences, command: nextCommand };
    draftContentRef.current = next;
    setDraftContent(next);
  };
  const [uploadItems, setUploadItems] = useState(initialDraft.attachments);
  const [interruptedFiles, setInterruptedFiles] = useState(initialDraft.interruptedFiles);
  const [sending, setSending] = useState(false);
  const [sendInBackground, setSendInBackground] = useState(false);
  const backgroundExecution = sendInBackground && !!onSendSuperagent;
  const [selectingFile, setSelectingFile] = useState(false);
  const selectingFileRef = useRef(false);
  const [submitError, setSubmitError] = useState('');
  const [draftError, setDraftError] = useState(false);
  const [previewImage, setPreviewImage] = useState<{ url: string; name: string } | null>(null);
  const [inspection, setInspection] = useState<
    { kind: 'file'; file: ChatDeliverable } | { kind: 'subagent'; child: AgentSubagent } | null
  >(null);
  const inspectionTrigger = useRef<HTMLElement | null>(null);
  const closePreview = useCallback(() => setPreviewImage(null), []);
  const [dragOver, setDragOver] = useState(false);
  const [maxMb, setMaxMb] = useState(50);
  const maxMbRef = useRef(maxMb);
  maxMbRef.current = maxMb;
  const [uploads] = useState(() => new AttachmentUploadQueue(async (file, signal) => {
    const upload = file.size > maxMbRef.current * 1024 * 1024 ? adoptOversize : uploadFiles;
    const saved = await upload([file], session.id, signal);
    if (!saved[0]) throw new Error('上传服务没有返回文件回执');
    return saved[0];
  }, setUploadItems, initialDraft.attachments));
  const editorRef = useRef<StructuredEditorHandle>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sendingRef = useRef(false);
  const mountedRef = useRef(false);
  const admittedCleanup = useRef(false);
  const dragDepth = useRef(0);

  const contextBudget = readContextBudget(session.messages, stream ? stream.events ?? [] : undefined);
  const isStreaming = !!stream;
  const isEmpty = session.messages.length === 0 && !isStreaming;
  const historyStart = session.historyWindow?.start ?? 0;
  const historyBefore = session.historyWindow?.before;
  const reading = useChatReading(session.id, session.messages, stream, !isEmpty, historyStart);
  const [historyLoad, setHistoryLoad] = useState<{ key: string; pending: boolean; error?: string }>();
  const historyRequest = useRef<object | null>(null);
  const automaticHistoryAttempt = useRef<string | undefined>(undefined);
  const nearTopArmed = useRef(false);
  const historyAnchor = useRef<{ sessionId: string; start: number; restore?: () => void } | undefined>(undefined);
  const historyKey = historyBefore ? JSON.stringify([session.id, historyBefore]) : '';
  const currentHistoryKey = useRef(historyKey);
  currentHistoryKey.current = historyKey;
  useEffect(() => () => {
    historyRequest.current = null;
    historyAnchor.current = undefined;
    nearTopArmed.current = false;
    automaticHistoryAttempt.current = undefined;
  }, [session.id]);
  useLayoutEffect(() => {
    const anchor = historyAnchor.current;
    if (anchor?.sessionId !== session.id || anchor.start === historyStart) return;
    historyAnchor.current = undefined;
    if (historyStart < anchor.start) anchor.restore?.();
  }, [historyStart, session.id]);

  // Upstream use-chat-navigation: begin paging without following the tail,
  // then keep the reader's current anchor when the older page commits.
  const loadEarlier = async (automatic: boolean) => {
    if (!onLoadEarlier || !historyBefore || !historyKey || historyRequest.current
      || (automatic && automaticHistoryAttempt.current === historyKey)) return;
    const request = {};
    historyRequest.current = request;
    automaticHistoryAttempt.current = historyKey;
    reading.pauseFollowing();
    historyAnchor.current = { sessionId: session.id, start: historyStart, restore: reading.captureReadingAnchor() };
    setHistoryLoad({ key: historyKey, pending: true });
    try {
      await onLoadEarlier(session.id, historyBefore);
      if (historyRequest.current === request) setHistoryLoad(undefined);
    } catch (error) {
      if (historyRequest.current === request && currentHistoryKey.current === historyKey) {
        historyAnchor.current = undefined;
        setHistoryLoad({ key: historyKey, pending: false, error:
          error instanceof Error && error.message ? error.message : '较早消息暂时无法加载，请重试。' });
      }
    } finally {
      if (historyRequest.current === request) historyRequest.current = null;
    }
  };

  const onHistoryScroll = () => {
    reading.onScroll();
    if (historyRequest.current && historyAnchor.current?.start === historyStart) {
      historyAnchor.current.restore = reading.captureReadingAnchor();
    }
    const viewport = reading.scrollRef.current;
    if (!viewport || !historyBefore) return;
    if (viewport.scrollTop > 72) nearTopArmed.current = true;
    else if (nearTopArmed.current) {
      nearTopArmed.current = false;
      void loadEarlier(true);
    }
  };
  const [workDetails, setWorkDetails] = useState(loadWorkDetailsPreferences);
  const restoreReadingAnchor = useRef<(() => void) | undefined>(undefined);
  useLayoutEffect(() => {
    restoreReadingAnchor.current?.();
    restoreReadingAnchor.current = undefined;
  }, [workDetails]);
  const changeWorkDetails = (next: WorkDetailsPreferences) => {
    restoreReadingAnchor.current = reading.captureReadingAnchor();
    setWorkDetails(next);
    saveWorkDetailsPreferences(next);
  };
  const openDeliverable = (file: ChatDeliverable, trigger: HTMLElement) => {
    inspectionTrigger.current = trigger;
    setInspection({ kind: 'file', file });
  };
  const openSubagent = (child: AgentSubagent, trigger: HTMLElement) => {
    if (!trigger.closest('.deliverable-preview-pane')) inspectionTrigger.current = trigger;
    setInspection({ kind: 'subagent', child });
  };
  useEffect(() => {
    if (inspection || !inspectionTrigger.current) return;
    const trigger = inspectionTrigger.current;
    inspectionTrigger.current = null;
    (trigger.isConnected ? trigger : reading.scrollRef.current)?.focus({ preventScroll: true });
  }, [inspection, reading.scrollRef]);
  const uploading = uploadItems.some(item => item.status === 'queued' || item.status === 'uploading');
  const failedUploads = uploadItems.some(item => item.status === 'failed');
  const attachments = uploads.ready;
  const restorePending = restoreState !== 'complete' && uploadItems.some(item => restoredKeys.has(item.key));
  const backgroundBlocked = uploadItems.length > 0 || interruptedFiles.length > 0
    || references.length > 0 || sessionReferences.length > 0 || !!command;
  const canSend = (backgroundExecution ? !!input.trim() && !backgroundBlocked : !!input.trim() || attachments.length > 0)
    && !uploading && !failedUploads && !restorePending && !sending && !selectingFile;
  const showSteer = !backgroundExecution && isStreaming && onSteer && uploadItems.length === 0 && interruptedFiles.length === 0 && sessionReferences.length === 0 && !command;
  const sendLabel = backgroundExecution ? '发送到后台' : isStreaming ? '加入队列' : '发送';
  const waitingApproval = stream?.questions?.some(question => (!question.status || question.status === 'pending')
    && (question.requiresHuman || question.kind === 'approval' || question.publication));
  const lifecycleLabel = stream?.lifecycle === 'waiting_input' ? (waitingApproval ? '等待你批准' : '等待补充信息')
    : stream?.lifecycle === 'stopping' ? '正在停止'
    : stream?.lifecycle === 'stop_failed' ? (stream.stopFeedback?.kind === 'failed' ? '停止请求失败' : '停止未确认')
    : isStreaming ? '任务进行中' : apiDirect ? '文字与附件分析' : '自主执行';

  useEffect(() => {
    projectFetch(projectUrl('/api/upload/limits')).then((r) => r.json())
      .then((d) => { if (d?.max_mb) setMaxMb(d.max_mb); }).catch(() => {});
  }, []);

  useEffect(() => {
    const items = uploads.snapshot.filter(item => restoredKeys.has(item.key) && item.status === 'ready');
    if (!items.length) return;
    const controller = new AbortController();
    void validateRestoredAttachments(session.id, items, controller.signal).then(invalid => {
      if (controller.signal.aborted) return;
      uploads.invalidateRestored(invalid);
      setRestoreState('complete');
    }).catch(() => {
      if (!controller.signal.aborted) setRestoreState('unconfirmed');
    });
    return () => controller.abort();
  }, [session.id, restoredKeys, uploads, restoreAttempt]);

  useEffect(() => {
    if (admittedCleanup.current) {
      admittedCleanup.current = false;
      if (!input && uploadItems.length === 0) {
        // Reconcile any simultaneous reminder dismissal without taking over
        // storage that a newly mounted composer may already own.
        setDraftError(!consumeChatDraft(session.id, composerId, interruptedFiles));
        return;
      }
    }
    setDraftError(!saveChatDraft(session.id, { text: input, references, sessionReferences, command, attachments: uploadItems, interruptedFiles }, composerId));
  }, [session.id, composerId, input, references, sessionReferences, command, uploadItems, interruptedFiles]);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // StrictMode remounts effects before this microtask; real unmount aborts transport.
      queueMicrotask(() => { if (!mountedRef.current) uploads.dispose(); });
    };
  }, [uploads]);

  const doUpload = (fs: FileList | File[]) => {
    if (sendingRef.current) { setSubmitError('当前消息接收中，请稍后添加素材。'); return; }
    if (backgroundExecution) { setSubmitError('后台执行只接收文字。请切回对话模式添加素材。'); return; }
    uploads.add(Array.from(fs)); setSubmitError('');
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); dragDepth.current = 0; setDragOver(false);
    if (e.dataTransfer.files?.length) doUpload(e.dataTransfer.files);
  };
  const submitDraft = async (intent: 'send' | 'steer') => {
    const { text: submittedText, references: submittedReferences,
      sessionReferences: submittedSessionReferences, command: submittedCommand } = draftContentRef.current;
    const trimmed = submittedText.trim();
    if (sendingRef.current || selectingFileRef.current) return;
    if (backgroundExecution && intent === 'send' && (uploads.snapshot.length > 0 || interruptedFiles.length > 0
        || submittedReferences.length > 0 || submittedSessionReferences.length > 0 || submittedCommand)) {
      setSubmitError('后台执行只接收文字。请移除附件、引用或命令，或切回对话模式。');
      return;
    }
    if (intent === 'steer' && (!showSteer || !trimmed || uploads.snapshot.length > 0 || submittedSessionReferences.length > 0 || submittedCommand)) return;
    if ((!trimmed && attachments.length === 0) || uploading || failedUploads || restorePending) return;
    const exactReferences = validateChatReferences(submittedText, submittedReferences, attachments);
    if (!exactReferences) { setSubmitError('正文附件引用已变更，请重新插入后发送。'); return; }
    const exactSessions = validateChatSessionReferences(submittedText, submittedSessionReferences, exactReferences);
    if (!exactSessions) { setSubmitError('历史对话引用已变更，请重新预览后插入。'); return; }
    const exactCommand = validateChatCommand(submittedText, submittedCommand);
    if (submittedCommand && (!exactCommand || chatCommandOverlapsReferences(exactCommand, exactReferences, exactSessions))) {
      setSubmitError('命令已变更，请重新选择后发送。');
      return;
    }
    sendingRef.current = true; setSending(true); setSubmitError('');
    try {
      const accepted = await (intent === 'steer' ? onSteer?.(trimmed)
        : backgroundExecution ? onSendSuperagent?.(trimmed)
        : exactCommand ? onSend(submittedText, attachments, exactReferences, exactSessions, exactCommand)
          : exactSessions.length ? onSend(submittedText, attachments, exactReferences, exactSessions)
          : exactReferences.length ? onSend(submittedText, attachments, exactReferences) : onSend(trimmed, attachments));
      if (accepted !== true) {
        if (mountedRef.current) setSubmitError(intent === 'steer'
          ? '当前任务尚未确认接收指导，草稿已保留。'
          : backgroundExecution ? '后台任务尚未接收，草稿已保留，请重试。' : '任务尚未接收，草稿已保留，请重试。');
        return;
      }
      consumeChatDraft(session.id, composerId);
      if (mountedRef.current) {
        admittedCleanup.current = true;
        replaceInput(''); uploads.clear(); reading.backToBottom();
      }
    } catch (error) {
      if (mountedRef.current) setSubmitError(error instanceof Error ? error.message
        : intent === 'steer' ? '指导未确认，草稿已保留。' : '发送失败，草稿已保留。');
    } finally {
      sendingRef.current = false;
      if (mountedRef.current) setSending(false);
    }
  };
  const handleSend = () => submitDraft('send');

  const quoteAnswer = useCallback((text: string): boolean => {
    if (sendingRef.current || selectingFileRef.current) return false;
    return editorRef.current?.insertAnswerQuote(text) ?? false;
  }, []);

  const inputBox = (hero: boolean) => (
    <div className={`composer ${hero ? 'composer-hero' : ''} ${dragOver ? 'composer-drag' : ''}`}>
      {uploadItems.length > 0 && (
        <div className="composer-upload-list" aria-label="待发送附件">
          {uploadItems.map((item) => {
            const checking = restorePending && restoredKeys.has(item.key);
            const receiptUrl = item.reference && item.status === 'ready' && !checking ? attachmentHref(item.reference.path) : null;
            const image = item.mime.startsWith('image/') || /\.(?:png|jpe?g|gif|webp|bmp|avif)$/i.test(item.name);
            const imageUrl = item.previewUrl || (image ? receiptUrl : '') || '';
            return (
            <div key={item.key} className={`composer-file composer-file--${item.status}`}>
              {imageUrl ? <button className="composer-file-preview" type="button" title={`预览 ${item.name}`} aria-label={`预览 ${item.name}`}
                onClick={() => setPreviewImage({ url: imageUrl, name: item.name })}>
                <img src={imageUrl} alt={item.name} /></button>
                : <div className="composer-file-icon"><IconFile size={22} /></div>}
              <div className="composer-file-info"><strong title={item.name}>{item.name}</strong>
                <span>{item.reference && item.reference.size === undefined ? '大小未记录' : fileSizeText(item.size)} · {checking ? (restoreState === 'unconfirmed' ? '附件未核验' : '核验中…') : item.status === 'ready' ? '已就绪' : item.status === 'queued' ? '排队中'
                  : item.status === 'uploading' ? '上传中…' : item.reference ? '附件不可用' : '上传失败'}</span>
                {item.status === 'uploading' && <span className="composer-upload-progress" role="progressbar" aria-label={`${item.name} 上传中`} />}
                {item.error && <span className="composer-file-error" title={item.error}>{item.error}</span>}
              </div>
              {item.status === 'failed' && !item.reference && <button type="button" className="composer-file-retry" disabled={sending}
                onClick={() => uploads.retry(item.key)}>重试</button>}
              {receiptUrl && !imageUrl && <a className="composer-file-retry" href={receiptUrl} target="_blank"
                rel="noopener noreferrer" aria-label={`打开 ${item.name}`}>打开</a>}
              {item.status === 'ready' && item.reference && !checking && <button type="button" className="composer-file-retry"
                disabled={sending} aria-label={`插入正文 ${item.name}`} onMouseDown={event => event.preventDefault()}
                onClick={() => {
                  if (!item.reference || sendingRef.current) return;
                  if (draftContentRef.current.references.length >= MAX_CHAT_REFERENCES) {
                    setSubmitError('正文引用数量已达上限，请先移除一个引用。');
                    return;
                  }
                  setSubmitError('');
                  editorRef.current?.insertReference(item.reference.id, attachmentReferenceLabel(item.reference));
                }}>插入正文</button>}
              <button type="button" className="composer-file-remove" disabled={sending} onClick={() => {
                if (sendingRef.current) return;
                if (item.reference) editorRef.current?.removeReference(item.reference.id);
                uploads.remove(item.key);
              }} title={`移除 ${item.name}`} aria-label={`移除 ${item.name}`}>×</button>
            </div>
          ); })}
        </div>
      )}
      {restorePending && restoreState === 'unconfirmed' && <div className="composer-notice" role="status">
        <span>暂时无法核验恢复的附件，请重试或移除这些附件。</span>
        <button type="button" onClick={() => {
          setRestoreState('checking');
          setRestoreAttempt(value => value + 1);
        }}>重试核验</button>
      </div>}
      {!!interruptedFiles.length && <div className="composer-notice" role="status">
        <span>上次有 {interruptedFiles.length} 个附件需要重新选择：{interruptedFiles.map(file => file.name).join('、')}</span>
        <button type="button" onClick={() => setInterruptedFiles([])}>知道了</button>
      </div>}
      <div className="composer-top">
        {apiDirect ? <span className="composer-hint">API 直连 · 文字与附件分析</span>
          : <BrushEntry onPick={(t) => {
            if (sendingRef.current) return;
            replaceInput(t);
            requestAnimationFrame(() => editorRef.current?.focus());
          }} />}
        <StructuredComposerEditor ref={editorRef} value={input} references={references}
          sessionReferences={sessionReferences} command={command} disabled={sending}
          fileReferenceSessionId={backgroundExecution ? undefined : session.id}
          onAdoptFileReference={file => !backgroundExecution && !sendingRef.current &&
            draftContentRef.current.references.length < MAX_CHAT_REFERENCES && uploads.adopt(file)}
          onFileSelectionPending={pending => {
            selectingFileRef.current = pending;
            setSelectingFile(pending);
          }}
          placeholder={backgroundExecution ? '描述要交给 Super Agent 的任务…' : dragOver ? '松手上传素材…' : hero ? '把你的想法告诉我，选题 / 文案 / 卡片 / 视频 / 发布都行…（输入 @ 引用文件，也可拖入素材）' : '发消息…（@ 引用文件）'}
          onChange={(text, nextReferences, nextSessions, nextCommand) => {
            if (sendingRef.current) return;
            const receipts = uploads.ready;
            const valid = validateChatReferences(text, nextReferences, receipts) ??
              nextReferences.slice(0, MAX_CHAT_REFERENCES)
                .filter(reference => validateChatReferences(text, [reference], receipts) !== null);
            const validSessions = validateChatSessionReferences(text, nextSessions, valid) ?? [];
            const claim = validateChatCommand(text, nextCommand);
            replaceInput(text, valid, validSessions,
              claim && !chatCommandOverlapsReferences(claim, valid, validSessions) ? claim : undefined);
          }}
          onSubmit={handleSend} onPasteFiles={doUpload}
        />
      </div>
      <input ref={fileInputRef} type="file" multiple hidden
        onChange={(e) => { if (e.target.files) doUpload(e.target.files); e.target.value = ''; }} />
      <div className={onSendSuperagent ? 'composer-bar composer-bar--superagent' : 'composer-bar'}>
        <button className="composer-attach-btn" onClick={() => fileInputRef.current?.click()}
          disabled={sending || backgroundExecution} title={`添加图片或文档；超过 ${maxMb}MB 使用大文件上传通道`}>
          <IconPlus size={15} /> 添加素材
        </button>
        {onSendSuperagent && <button type="button" className="composer-attach-btn composer-background-toggle"
          data-superagent-composer-toggle aria-pressed={backgroundExecution} disabled={sending || selectingFile}
          title="交给 Super Agent 后台执行"
          onClick={() => { setSendInBackground(value => !value); setSubmitError(''); }}>后台执行</button>}
        {(uploading || selectingFile || sending || backgroundExecution) && <span className="composer-hint">{uploading ? '附件全部就绪后可发送' : selectingFile ? '正在准备所选文件…' : sending ? '正在接收…'
          : '任务发送后可离开对话'}</span>}
        <div className="composer-send-controls">
          {isStreaming && <button className="composer-stop-btn" onClick={onStop} title="停止当前任务"
            disabled={stream?.lifecycle === 'stopping' || queue.some(item => item.delivery === 'sending')}>
            <IconStop size={14} /><span>{stream?.lifecycle === 'stopping' ? '停止中' : stream?.lifecycle === 'stop_failed' ? '重试停止' : '停止'}</span></button>}
          {showSteer && <button type="button" className="composer-attach-btn" title="立即指导"
            onClick={() => submitDraft('steer')} disabled={!canSend}>立即指导</button>}
          {(modelCapability || modelLoading || onReloadModels) && <SessionModelControl capability={modelCapability}
            nextModel={nextModel} turnModel={turnModel} running={isStreaming} disabled={sending}
            loading={modelLoading} onChangeModel={onChangeModel} onReload={onReloadModels} />}
          <button className={`send-btn ${backgroundExecution || isStreaming ? 'send-btn-queue' : ''}`} onClick={handleSend} disabled={!canSend}
            data-superagent-composer-send={backgroundExecution || undefined} title={sendLabel} aria-label={sendLabel}>
            {backgroundExecution || isStreaming ? <span>{sendLabel}</span> : <IconArrowUp size={17} />}
          </button>
        </div>
      </div>
      {failedUploads && <div className="composer-feedback" role="alert">请重试或移除失败的附件后发送。</div>}
      {backgroundExecution && backgroundBlocked && <div className="composer-feedback" role="status">
        后台执行只接收文字。请移除附件、引用或命令，或切回对话模式。
      </div>}
      {submitError && <div className="composer-feedback" role="alert">{submitError}</div>}
      {draftError && <div className="composer-feedback" role="alert">浏览器存储已满，当前草稿暂未保存。请保持本页打开。</div>}
    </div>
  );

  const dragProps = {
    onDragEnter: (event: React.DragEvent) => { if (Array.from(event.dataTransfer.types).includes('Files')) {
      event.preventDefault(); dragDepth.current += 1; setDragOver(true);
    } },
    onDragOver: (event: React.DragEvent) => { if (Array.from(event.dataTransfer.types).includes('Files')) event.preventDefault(); },
    onDragLeave: () => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragOver(false); },
    onDrop,
  };
  const dragOverlay = dragOver && <div className="chat-file-drop" aria-live="polite"><IconPlus size={30} />
    <strong>将素材拖到这里</strong><span>图片、文档或视频 · 按选择顺序加入当前对话</span></div>;
  const preview = previewImage && <ImageLightbox src={previewImage.url} alt={previewImage.name}
    labels={{ dialog: previewImage.name, close: '关闭图片预览' }} onClose={closePreview} />;
  const queueDock = queue.length > 0 && <div className="chat-queue" aria-label="待执行消息队列">
    <div className="chat-queue-title"><strong>{queuePaused ? '队列已暂停' : '接下来执行'}</strong><span>{queue.length} 条</span>
      {queuePaused && onResumeQueue && <button type="button" disabled={!!queueEdit} onClick={onResumeQueue}>
        {queue[0]?.delivery === 'unconfirmed' ? '重新核对' : '继续执行'}</button>}
    </div>
    {queue.map((item, index) => {
      const editable = item.delivery !== 'sending' && item.delivery !== 'unconfirmed';
      const editing = editable && queueEdit?.id === item.id;
      const label = item.delivery === 'sending' ? '等待服务端接收' : item.delivery === 'unconfirmed' ? '回执待核对'
        : item.delivery === 'rejected' ? '服务端未接收' : '排队中';
      const saveEdit = () => {
        if (queueEdit && onEditQueued?.(item.id, queueEdit.text)) changeQueueEdit(null);
      };
      return <div key={item.id} className="chat-queue-row"><span>{index + 1}</span>
        <div>
          {editing ? <textarea className="field" rows={2} aria-label={`编辑第 ${index + 1} 条排队消息`} value={queueEdit.text}
            onChange={event => setQueueEdit({ id: item.id, text: event.target.value })}
            onKeyDown={event => {
              if (event.key === 'Escape') { event.preventDefault(); changeQueueEdit(null); }
            }} /> : <p>{item.text || '处理附件'}</p>}
          {!!item.attachments?.length && <small>{item.attachments.map(file => file.name).join('、')}</small>}
          <small role="status">{label}</small>
        </div>
        {editing ? <>
          <button type="button" disabled={!queueEdit.text.trim() && !item.attachments?.length} onClick={saveEdit}>保存</button>
          <button type="button" onClick={() => changeQueueEdit(null)}>取消</button>
        </> : editable && <>
          {onEditQueued && <button type="button" disabled={!!queueEdit} aria-label={`编辑第 ${index + 1} 条排队消息`}
            onClick={() => changeQueueEdit({ id: item.id, text: item.text })}>编辑</button>}
          {onRemoveQueued && <button type="button" className="chat-queue-remove" aria-label={`移除第 ${index + 1} 条排队消息`} onClick={() => onRemoveQueued(item.id)}>×</button>}
        </>}
      </div>;
    })}
  </div>;

  const composer = (hero: boolean) => <>
    {inputBox(hero)}
    <ContextBudgetMeter budget={contextBudget} onNewChat={onNewContextChat} />
  </>;

  const admissionBanner = admissionNotice && <div className="composer-notice">
    <span role="status">{admissionNotice.message}</span>
    {admissionNotice.action && <button type="button" disabled={sending} onClick={admissionNotice.action.run}>
      {admissionNotice.action.label}
    </button>}
  </div>;

  // ---- 空态：居中欢迎页 ----
  if (isEmpty) {
    return (
      <div className="chat-page agent-chat" {...dragProps}>
        {dragOverlay}{preview}
        <div className="chat-hero">
          <div className="chat-hero-brand">
            <img src="./static/easel-icon-transparent.png" alt="" />
            <span>Easel</span>
          </div>
          <h1 className="chat-hero-title">{greeting()}</h1>
          <p className="chat-hero-sub">{apiDirect ? 'API 直连：写文案、策划选题、分析附件。本机工具和自动发布未启用。'
            : '从选题到发布，一站式帮你把想法做成能发的内容。'}</p>
          {queueDock}{composer(true)}
          {superagentTasks}
          {admissionBanner}
          {historyNotice && <p className="chat-history-notice" role="status">{historyNotice}</p>}
          <div className="suggestions">
            {SUGGESTIONS.filter((s) => !apiDirect || ['写小红书文案', '口播脚本'].includes(s.title)).map((s) => (
              <button key={s.title} className="card card-hover suggestion-card"
                onClick={() => {
                  if (sendingRef.current) return;
                  replaceInput(s.prompt);
                  requestAnimationFrame(() => editorRef.current?.focus());
                }}>
                <span className="suggestion-icon">{s.icon}</span>
                <span className="suggestion-body">
                  <span className="suggestion-title">{s.title}</span>
                  <span className="suggestion-text">{s.prompt}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }

  const forkMessage = forkTarget?.sessionId === session.id ? session.messages[forkTarget.index] : undefined;
  const forkDialog = forkTarget && forkMessage?.status === 'completed' && forkMessage.clientTurnId === forkTarget.turnId
    && forkMessage.content === forkTarget.answer && onForkAnswer && onOpenFork
    ? <ForkChatDialog key={forkTarget.turnId} sourceTitle={session.title} answerText={forkTarget.answer}
      messageCount={historyStart + forkTarget.index + 1} onFork={() => onForkAnswer(forkTarget.index)}
      onOpen={onOpenFork} onClose={() => setForkTarget(undefined)} /> : null;
  // ---- 对话态 ----
  const lastUserIndex = session.messages.findLastIndex(message => message.role === 'user');
  const lastMessage = session.messages.at(-1);
  const lastTurnHasPublication = lastMessage?.events?.some(event => event.kind === 'publication' && event.publication);
  const canRetryLastTurn = !lastTurnHasPublication && lastMessage?.status !== 'unconfirmed';
  const activeHistoryEdit = historyEdit?.sessionId === session.id ? historyEdit : undefined;
  const displayMessages: ChatMessage[] = [...session.messages];
  if (isStreaming) displayMessages.push({ role: 'assistant', content: stream!.content || '' });

  const lineage = session.fork;
  const hasLineage = lineage && typeof lineage.sourceTitle === 'string' &&
    Number.isSafeInteger(lineage.cutoffIndex) && lineage.cutoffIndex >= 0;
  const chat = (
    <div className="chat-page agent-chat" {...dragProps}>
      {dragOverlay}{preview}{forkDialog}
      <div className="chat-session-header"><div><span>AI 对话</span><h1>{session.title === 'New Chat' ? '新对话' : session.title}</h1>
        {hasLineage && <p className="chat-fork-source" data-chat-fork-source>
          分叉自「{lineage.sourceTitle}」 · 截止第 {lineage.cutoffIndex + 1} 条消息
          {lineage.contextTruncated === true && ' · 引擎上下文保留了部分历史'}
        </p>}
      </div>
        <div className="chat-session-controls">
          <label><span>工作细节</span><select aria-label="工作细节" value={workDetails.mode}
            onChange={event => changeWorkDetails({ ...workDetails, mode: event.target.value === 'detailed' ? 'detailed' : 'compact' })}>
            <option value="compact">简洁</option>
            <option value="detailed">详细</option>
          </select></label>
          <label><span>简洁模式自动折叠</span><select aria-label="简洁模式自动折叠" value={workDetails.collapseTiming}
            onChange={event => changeWorkDetails({ ...workDetails,
              collapseTiming: event.target.value === 'next-input' ? 'next-input' : 'completion' })}>
            <option value="completion">完成时</option>
            <option value="next-input">下一次提问时</option>
          </select></label>
          <span className={`chat-session-status ${isStreaming ? 'is-active' : ''} ${stream?.lifecycle === 'waiting_input' ? 'is-waiting' : ''}
          ${stream?.lifecycle === 'stop_failed' ? 'is-error' : ''}`} aria-live="polite">
            <i />{lifecycleLabel}</span>
        </div>
      </div>
      <div className="chat-messages" data-reading-detached={!reading.following || undefined} ref={reading.scrollRef} onScroll={onHistoryScroll} onWheel={reading.onWheel}
        onTouchStart={reading.interrupt} onPointerDown={reading.interrupt} onKeyDown={reading.onKeyDown} tabIndex={0} aria-label="对话消息">
        <div className="chat-thread" ref={reading.threadRef}>
          {onLoadEarlier && historyBefore && <div className="chat-history-page">
            <button type="button" className="btn" data-load-earlier-turns
              disabled={historyLoad?.key === historyKey && historyLoad.pending}
              onClick={() => void loadEarlier(false)}>
              {historyLoad?.key === historyKey && historyLoad.pending ? '正在加载较早轮次…'
                : historyLoad?.key === historyKey && historyLoad.error ? '重试加载较早轮次' : '加载较早轮次'}
            </button>
            {historyLoad?.key === historyKey && historyLoad.error &&
              <p role="alert" data-history-page-error>{historyLoad.error}</p>}
          </div>}
          {displayMessages.map((msg, i) => {
            const isLast = i === displayMessages.length - 1;
            const live = isStreaming && isLast && msg.role === 'assistant';
            const isFinal = !live && i < session.messages.length;
            const absoluteIndex = historyStart + i;
            const inherited = isFinal && !!hasLineage && absoluteIndex <= lineage.cutoffIndex;
            let actions;
            if (isFinal) {
              const copy = () => navigator.clipboard?.writeText(msg.content);
              // 最近一轮可追加一次尝试，旧消息从分支修订。
              const isLastFinal = i === session.messages.length - 1;
              if (msg.role === 'user') {
                const backgroundMessage = msg.clientTurnId?.startsWith('superagent-');
                actions = {
                  onCopy: copy,
                  onRetry: !backgroundMessage && canRetryLastTurn && isLastFinal && !msg.planRef && !inherited
                    ? () => onResend(i)
                    : undefined,
                  onEditHistory: !backgroundMessage && onEditHistory && !inherited && i < lastUserIndex
                    ? () => { void editHistoricalMessage(i); }
                    : undefined,
                  historyEditPending: activeHistoryEdit?.pending === true,
                  historyEditCurrent: activeHistoryEdit?.index === i,
                  historyEditError: activeHistoryEdit?.index === i ? activeHistoryEdit.error : undefined,
                  canModify: !isStreaming,
                };
              } else {
                const turnId = msg.clientTurnId;
                const pi = i - 1;
                const prevUser = pi >= 0 && session.messages[pi]?.role === 'user' ? session.messages[pi] : null;
                actions = {
                  onCopy: copy,
                  onFork: onForkAnswer && onOpenFork && msg.status === 'completed' && typeof turnId === 'string' &&
                    /^[A-Za-z0-9_.-]{1,160}$/.test(turnId)
                    ? () => setForkTarget({ sessionId: session.id, index: i, turnId, answer: msg.content })
                    : undefined,
                  onRetry: (canRetryLastTurn && isLastFinal && prevUser && !prevUser.clientTurnId?.startsWith('superagent-')
                    && !prevUser.planRef && !inherited)
                    ? () => onResend(pi)
                    : undefined,
                  canModify: !isStreaming,
                };
              }
            }
            return (
              <div key={`${absoluteIndex}-${msg.role}`} data-chat-message data-chat-message-index={absoluteIndex}>
              <MessageBubble
                key={`${absoluteIndex}-${msg.role}`}
                sessionId={session.id}
                message={msg}
                isStreaming={live}
                inheritedForkText={inherited}
                detailMode={workDetails.mode}
                foldWorkDetails={isFinal && (workDetails.collapseTiming === 'completion' || i < lastUserIndex)}
                reasoningSummary={live ? stream!.reasoningSummary : undefined}
                answerCompletion={live ? stream!.answerCompletion : undefined}
                turnLifecycle={live ? stream!.lifecycle : undefined}
                activity={live ? stream!.stopFeedback?.message ?? stream!.activity : ''}
                stillWorking={live ? stream!.stillWorking : ''}
                events={live ? stream!.events : msg.events}
                timeline={live ? stream!.timeline : undefined}
                pendingTimelineQuestions={live
                  ? stream!.questions?.filter(question => !question.status || question.status === 'pending').length : undefined}
                actions={actions}
                onOpenFailureSettings={onOpenFailureSettings}
                onQuoteAnswer={isFinal && msg.role === 'assistant' ? quoteAnswer : undefined}
                onOpenDeliverable={openDeliverable}
                onOpenSubagent={openSubagent}
              />
              {!live && msg.questions && <QuestionCards sessionId={session.id} questions={msg.questions} onDone={onQuestionAnswered} />}
              </div>
            );
          })}
          {isStreaming && (stream!.questions?.length ?? 0) > 0 && (
            <QuestionCards sessionId={session.id} questions={stream!.questions || []} onDone={onQuestionAnswered} />
          )}
          {superagentTasks}
        </div>
      </div>

      <div className="chat-input-area">
        {!reading.following && <button className="chat-back-to-bottom" onClick={reading.backToBottom}>
          {reading.unread ? '有新内容 · 回到底部' : '回到底部'} <span>↓</span></button>}
        <div className="chat-input-inner">{queueDock}{composer(false)}
          {admissionBanner}
          {historyNotice && <p className="chat-history-notice" role="status">{historyNotice}</p>}
          {apiDirect && <p className="chat-autonomy-hint">文字与附件分析模式</p>}
        </div>
      </div>
    </div>
  );
  return <div className="chat-deliverable-layout">
    {chat}
    {inspection?.kind === 'file' && <DeliverablePreviewPane key={inspection.file.artifactId} file={inspection.file}
      onClose={() => setInspection(null)} />}
    {inspection?.kind === 'subagent' && <ChatInspectionPane key={inspection.child.id} title={inspection.child.title}
      contentLabel="子会话内容" onClose={() => setInspection(null)}>
      <SubagentTranscript sessionId={session.id} child={inspection.child} onOpenSubagent={openSubagent} />
    </ChatInspectionPane>}
  </div>;
}
