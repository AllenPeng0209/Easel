import { projectUrl, projectFetch } from '../lib/projectScope';
import { useState, useRef, useEffect, useCallback } from 'react';
import MessageBubble from './MessageBubble';
import QuestionCards from './QuestionCards';
import BrushEntry from './BrushEntry';
import type { ChatSession, ChatMessage, StreamState } from '../lib/store';
import { uploadFiles, adoptOversize, mediaUrl } from '../lib/api';
import type { UploadedFile } from '../lib/api';
import { IconArrowUp, IconStop, IconPlus, IconFile } from './icons';
import { AttachmentUploadQueue } from '../lib/uploadQueue';
import { loadChatDraft, saveChatDraft, clearChatDraft } from '../lib/chatDrafts';
import { useChatReading } from '../lib/chatReading';
import { fileSizeText } from '../vendor/deepseek-harness/composer/file-size';
import { ImageLightbox } from '../vendor/deepseek-harness/composer/ImageLightbox';
import '../styles/composer.css';

interface ChatPageProps {
  apiDirect: boolean;
  session: ChatSession;
  stream?: StreamState;          // 进行中的流式态（来自 App，切页也不丢）
  onSend: (displayText: string, attachments?: UploadedFile[]) => boolean | Promise<boolean>;
  onStop: () => void;
  onResend: (
    userIndex: number,
    displayText: string,
    attachments?: UploadedFile[],
    legacyAgentText?: string,
  ) => void; // 重试：仅对最后一轮
  onQuestionAnswered?: (questionId: string) => void;   // 某道问答题提交成功（App 记录答过，重放不再出现）
  onQuestionClosed?: (id: string, status: string) => void;
  queue?: { id: string; text: string; attachments?: UploadedFile[] }[];
  onRemoveQueued?: (id: string) => void;
  queuePaused?: boolean;
  onResumeQueue?: () => void;
  historyNotice?: string;
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

export default function ChatPage({ apiDirect, session, stream, onSend, onStop, onResend, onQuestionAnswered, onQuestionClosed,
  queue = [], onRemoveQueued, queuePaused, onResumeQueue, historyNotice }: ChatPageProps) {
  const [initialDraft] = useState(() => loadChatDraft(session.id));
  const [input, setInput] = useState(initialDraft.text);
  const [uploadItems, setUploadItems] = useState(initialDraft.attachments);
  const [interruptedFiles, setInterruptedFiles] = useState(initialDraft.interruptedFiles);
  const [sending, setSending] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [draftError, setDraftError] = useState(false);
  const [previewImage, setPreviewImage] = useState<{ url: string; name: string } | null>(null);
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
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);
  const compositionEndedAt = useRef(0);
  const sendingRef = useRef(false);
  const mountedRef = useRef(false);
  const dragDepth = useRef(0);

  const isStreaming = !!stream;
  const isEmpty = session.messages.length === 0 && !isStreaming;
  const reading = useChatReading(session.id, stream || session.messages, !isEmpty);
  const uploading = uploadItems.some(item => item.status === 'queued' || item.status === 'uploading');
  const failedUploads = uploadItems.some(item => item.status === 'failed');
  const attachments = uploads.ready;
  const canSend = (!!input.trim() || attachments.length > 0) && !uploading && !failedUploads && !sending;
  const waitingApproval = stream?.questions?.some(question => (!question.status || question.status === 'pending')
    && (question.requiresHuman || question.kind === 'approval' || question.publication));
  const lifecycleLabel = stream?.lifecycle === 'waiting_input' ? (waitingApproval ? '等待你批准' : '等待补充信息')
    : stream?.lifecycle === 'stopping' ? '正在停止' : stream?.lifecycle === 'stop_failed' ? '停止未确认'
    : isStreaming ? '任务进行中' : apiDirect ? '文字与附件分析' : '自主执行';

  useEffect(() => {
    projectFetch(projectUrl('/api/upload/limits')).then((r) => r.json())
      .then((d) => { if (d?.max_mb) setMaxMb(d.max_mb); }).catch(() => {});
  }, []);

  useEffect(() => {
    setDraftError(!saveChatDraft(session.id, { text: input, attachments: uploadItems, interruptedFiles }));
  }, [session.id, input, uploadItems, interruptedFiles]);
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
    uploads.add(Array.from(fs)); setSubmitError('');
  };
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); dragDepth.current = 0; setDragOver(false);
    if (e.dataTransfer.files?.length) doUpload(e.dataTransfer.files);
  };
  const onPaste = (e: React.ClipboardEvent) => {
    if (e.clipboardData.files?.length) { e.preventDefault(); doUpload(e.clipboardData.files); }
  };

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 180) + 'px';
    }
  }, [input]);

  const handleSend = async () => {
    const trimmed = input.trim();
    if ((!trimmed && attachments.length === 0) || uploading || failedUploads || sendingRef.current) return;
    sendingRef.current = true; setSending(true); setSubmitError('');
    try {
      const accepted = await onSend(trimmed, attachments);
      if (accepted !== true) { setSubmitError('任务尚未接收，草稿已保留，请重试。'); return; }
      clearChatDraft(session.id);
      if (mountedRef.current) {
        setInput(''); uploads.clear(); setInterruptedFiles([]); reading.backToBottom();
      }
    } catch (error) {
      if (mountedRef.current) setSubmitError(error instanceof Error ? error.message : '发送失败，草稿已保留。');
    } finally {
      sendingRef.current = false;
      if (mountedRef.current) setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // 输入法确认候选词/英文的回车交给输入法处理，不能触发发送。
    // 部分浏览器先触发 compositionend，此时仍需用 229 识别输入法按键。
    if (isComposingRef.current || e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229
      || Date.now() - compositionEndedAt.current < 60) return;
    if (e.key === 'Enter' && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const inputBox = (hero: boolean) => (
    <div className={`composer ${hero ? 'composer-hero' : ''} ${dragOver ? 'composer-drag' : ''}`}>
      {uploadItems.length > 0 && (
        <div className="composer-upload-list" aria-label="待发送附件">
          {uploadItems.map((item) => {
            const imageUrl = item.previewUrl || (item.mime.startsWith('image/') && item.reference ? mediaUrl(item.reference.path) : '');
            return (
            <div key={item.key} className={`composer-file composer-file--${item.status}`}>
              {imageUrl ? <button className="composer-file-preview" type="button" title={`预览 ${item.name}`}
                onClick={() => setPreviewImage({ url: imageUrl, name: item.name })}>
                <img src={imageUrl} alt={item.name} /></button>
                : <div className="composer-file-icon"><IconFile size={22} /></div>}
              <div className="composer-file-info"><strong title={item.name}>{item.name}</strong>
                <span>{fileSizeText(item.size)} · {item.status === 'ready' ? '已就绪' : item.status === 'queued' ? '排队中'
                  : item.status === 'uploading' ? '上传中…' : '上传失败'}</span>
                {item.status === 'uploading' && <span className="composer-upload-progress" role="progressbar" aria-label={`${item.name} 上传中`} />}
                {item.error && <span className="composer-file-error" title={item.error}>{item.error}</span>}
              </div>
              {item.status === 'failed' && <button type="button" className="composer-file-retry" disabled={sending}
                onClick={() => uploads.retry(item.key)}>重试</button>}
              <button type="button" className="composer-file-remove" disabled={sending} onClick={() => uploads.remove(item.key)}
                title={`移除 ${item.name}`} aria-label={`移除 ${item.name}`}>×</button>
            </div>
          ); })}
        </div>
      )}
      {!!interruptedFiles.length && <div className="composer-notice" role="status">
        <span>上次有 {interruptedFiles.length} 个文件未上传完成，请重新选择：{interruptedFiles.map(file => file.name).join('、')}</span>
        <button type="button" onClick={() => setInterruptedFiles([])}>知道了</button>
      </div>}
      <div className="composer-top">
        {apiDirect ? <span className="composer-hint">API 直连 · 文字与附件分析</span>
          : <BrushEntry onPick={(t) => { setInput(t); requestAnimationFrame(() => textareaRef.current?.focus()); }} />}
        <textarea
          ref={textareaRef}
          className="chat-input"
          placeholder={dragOver ? '松手上传素材…' : hero ? '把你的想法告诉我，选题 / 文案 / 卡片 / 视频 / 发布都行…（可拖入图片/文档当素材）' : '发消息…（Enter 发送，Shift+Enter 换行，可拖入/粘贴素材）'}
          value={input}
          disabled={sending}
          aria-label="给 AI 发送消息"
          onChange={(e) => setInput(e.target.value)}
          onCompositionStart={() => { isComposingRef.current = true; }}
          onCompositionEnd={() => { isComposingRef.current = false; compositionEndedAt.current = Date.now(); }}
          onBlur={() => { isComposingRef.current = false; }}
          onKeyDown={handleKeyDown}
          onPaste={onPaste}
          rows={1}
        />
      </div>
      <input ref={fileInputRef} type="file" multiple hidden
        onChange={(e) => { if (e.target.files) doUpload(e.target.files); e.target.value = ''; }} />
      <div className="composer-bar">
        <button className="composer-attach-btn" onClick={() => fileInputRef.current?.click()}
          disabled={sending} title={`添加图片或文档；超过 ${maxMb}MB 使用大文件上传通道`}>
          <IconPlus size={15} /> 添加素材
        </button>
        <span className="composer-hint">{uploading ? '附件全部就绪后可发送' : sending ? '正在接收…'
          : isStreaming ? '继续输入，下一条会加入队列' : 'Enter 发送 · Shift+Enter 换行'}</span>
        <div className="composer-send-controls">
          {isStreaming && <button className="composer-stop-btn" onClick={onStop} title="停止当前任务" disabled={stream?.lifecycle === 'stopping'}>
            <IconStop size={14} /><span>{stream?.lifecycle === 'stopping' ? '停止中' : stream?.lifecycle === 'stop_failed' ? '重试停止' : '停止'}</span></button>}
          <button className={`send-btn ${isStreaming ? 'send-btn-queue' : ''}`} onClick={handleSend} disabled={!canSend}
            title={isStreaming ? '加入队列' : '发送'} aria-label={isStreaming ? '加入队列' : '发送'}>
            {isStreaming ? <span>加入队列</span> : <IconArrowUp size={17} />}
          </button>
        </div>
      </div>
      {failedUploads && <div className="composer-feedback" role="alert">请重试或移除失败的附件后发送。</div>}
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
      {queuePaused && onResumeQueue && <button type="button" onClick={onResumeQueue}>继续执行</button>}
    </div>
    {queue.map((item, index) => <div key={item.id} className="chat-queue-row"><span>{index + 1}</span>
      <div><p>{item.text || '处理附件'}</p>{!!item.attachments?.length && <small>{item.attachments.map(file => file.name).join('、')}</small>}</div>
      {onRemoveQueued && <button type="button" aria-label={`移除第 ${index + 1} 条排队消息`} onClick={() => onRemoveQueued(item.id)}>×</button>}
    </div>)}
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
          {queueDock}{inputBox(true)}
          {historyNotice && <p className="chat-history-notice" role="status">{historyNotice}</p>}
          <div className="suggestions">
            {SUGGESTIONS.filter((s) => !apiDirect || ['写小红书文案', '口播脚本'].includes(s.title)).map((s) => (
              <button key={s.title} className="card card-hover suggestion-card"
                onClick={() => { setInput(s.prompt); requestAnimationFrame(() => textareaRef.current?.focus()); }}>
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

  // ---- 对话态 ----
  const displayMessages: ChatMessage[] = [...session.messages];
  if (isStreaming) displayMessages.push({ role: 'assistant', content: stream!.content || '' });

  return (
    <div className="chat-page agent-chat" {...dragProps}>
      {dragOverlay}{preview}
      <div className="chat-session-header"><div><span>AI 对话</span><h1>{session.title === 'New Chat' ? '新对话' : session.title}</h1></div>
        <span className={`chat-session-status ${isStreaming ? 'is-active' : ''} ${stream?.lifecycle === 'waiting_input' ? 'is-waiting' : ''}
          ${stream?.lifecycle === 'stop_failed' ? 'is-error' : ''}`} aria-live="polite">
          <i />{lifecycleLabel}</span>
      </div>
      <div className="chat-messages" ref={reading.scrollRef} onScroll={reading.onScroll} onWheel={reading.onWheel}
        onTouchStart={reading.interrupt} onPointerDown={reading.interrupt} onKeyDown={reading.onKeyDown} tabIndex={0} aria-label="对话消息">
        <div className="chat-thread" ref={reading.threadRef}>
          {displayMessages.map((msg, i) => {
            const isLast = i === displayMessages.length - 1;
            const live = isStreaming && isLast && msg.role === 'assistant';
            const isFinal = !live && i < session.messages.length;
            let actions;
            if (isFinal) {
              const copy = () => navigator.clipboard?.writeText(msg.content);
              // 只允许对「最后一轮」重试，契合 OpenClaw append-only 模型（不改写历史）；已移除编辑
              const isLastFinal = i === session.messages.length - 1;
              if (msg.role === 'user') {
                actions = {
                  onCopy: copy,
                  onRetry: isLastFinal
                    ? () => onResend(i, msg.content, msg.attachments, msg.agentContent)
                    : undefined,
                  canModify: !isStreaming,
                };
              } else {
                const pi = i - 1;
                const prevUser = pi >= 0 && session.messages[pi]?.role === 'user' ? session.messages[pi] : null;
                actions = {
                  onCopy: copy,
                  onRetry: (isLastFinal && prevUser)
                    ? () => onResend(pi, prevUser.content, prevUser.attachments, prevUser.agentContent)
                    : undefined,
                  canModify: !isStreaming,
                };
              }
            }
            return (
              <div key={`${i}-${msg.role}`}>
              <MessageBubble
                key={`${i}-${msg.role}`}
                message={msg}
                isStreaming={live}
                thinking={live ? stream!.thinking : ''}
                activity={live ? stream!.activity : ''}
                stillWorking={live ? stream!.stillWorking : ''}
                events={live ? stream!.events : msg.events}
                actions={actions}
              />
              {!live && msg.questions && <QuestionCards sessionId={session.id} questions={msg.questions} onDone={onQuestionAnswered} onClosed={onQuestionClosed} />}
              </div>
            );
          })}
          {isStreaming && (stream!.questions?.length ?? 0) > 0 && (
            <QuestionCards sessionId={session.id} questions={stream!.questions || []} onDone={(qid) => onQuestionAnswered?.(qid)} onClosed={onQuestionClosed} />
          )}
        </div>
      </div>

      <div className="chat-input-area">
        {!reading.following && <button className="chat-back-to-bottom" onClick={reading.backToBottom}>
          {reading.unread ? '有新内容 · 回到底部' : '回到底部'} <span>↓</span></button>}
        <div className="chat-input-inner">{queueDock}{inputBox(false)}
          {historyNotice && <p className="chat-history-notice" role="status">{historyNotice}</p>}
          <p className="chat-autonomy-hint">{apiDirect ? '文字与附件分析模式' : 'AI 持续推进任务；需要你补充信息时会显示输入卡。'}</p></div>
      </div>
    </div>
  );
}
