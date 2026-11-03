/**
 * Exact-cutoff confirmation adapted from DeepSeek Harness workspace fork
 * semantics @ ce7073d (MIT). Creating and opening a child remain separate.
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import './ForkChatDialog.css';

export interface CreatedChatFork { id: string; title: string }

interface Props {
  sourceTitle: string;
  answerText: string;
  messageCount: number;
  onFork: () => Promise<CreatedChatFork | null>;
  onOpen: (id: string) => void;
  onClose: () => void;
}

export default function ForkChatDialog({ sourceTitle, answerText, messageCount, onFork, onOpen, onClose }: Props) {
  const dialog = useRef<HTMLElement>(null);
  const dismissButton = useRef<HTMLButtonElement>(null);
  const owner = useRef<symbol | null>(null);
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const [created, setCreated] = useState<CreatedChatFork>();
  useLayoutEffect(() => () => { owner.current = null; }, []);
  useLayoutEffect(() => { if (created) dismissButton.current?.focus({ preventScroll: true }); }, [created]);
  const close = () => { if (!owner.current) onClose(); };
  useModalLayer(dialog, true, close);

  const create = async () => {
    if (owner.current || created) return;
    const request = Symbol('fork');
    owner.current = request;
    setPending(true);
    setFailed(false);
    try {
      const child = await onFork();
      if (owner.current !== request) return;
      if (child && typeof child.id === 'string' && child.id.trim() && typeof child.title === 'string') setCreated(child);
      else setFailed(true);
    } catch {
      if (owner.current === request) setFailed(true);
    } finally {
      if (owner.current === request) {
        owner.current = null;
        setPending(false);
      }
    }
  };

  const preview = Array.from(answerText);
  const cutoff = preview.slice(0, 420).join('');
  return createPortal(<div className="fork-chat-overlay" onMouseDown={event => {
    if (event.target === event.currentTarget) close();
  }}>
    <section ref={dialog} className="fork-chat-dialog" role="dialog" aria-modal="true" tabIndex={-1}
      aria-label="从答复创建分叉" aria-busy={pending} data-fork-chat-dialog>
      <h2>从答复创建分叉</h2>
      <p>来源「{sourceTitle || '当前对话'}」，继承截至此答复的 {messageCount} 条可见消息。新分支使用独立的引擎上下文。</p>
      <p>截止答复预览</p>
      <blockquote>{cutoff || '（此回合没有文字答复）'}{preview.length > 420 ? '…' : ''}</blockquote>
      {pending && <p role="status">正在创建分叉…</p>}
      {failed && <p role="alert" data-fork-chat-error>新分支未确认，原对话仍可继续查看。</p>}
      {created && <p role="status" data-fork-chat-created>已创建「{created.title || '新分支'}」。</p>}
      <footer>
        <button ref={dismissButton} type="button" data-modal-autofocus disabled={pending}
          onClick={close}>{created ? '留在原对话' : '取消'}</button>
        {created ? <button type="button" data-fork-chat-open onClick={() => {
          onOpen(created.id);
          onClose();
        }}>打开新对话</button> : <button type="button" data-fork-chat-create disabled={pending}
          onClick={() => void create()}>{failed ? '重试创建分叉' : '创建分叉'}</button>}
      </footer>
    </section>
  </div>, document.body);
}
