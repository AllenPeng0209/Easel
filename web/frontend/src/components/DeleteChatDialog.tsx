/**
 * Request-local destructive confirmation adapted from the pinned DeepSeek
 * Harness ui-workspace session actions @ ce7073d (MIT).
 * Easel closes only after the current delete or archive action is confirmed.
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import './DeleteChatDialog.css';

type DeleteAction = 'delete' | 'archive';

interface Props {
  sessionTitle: string;
  onDelete: () => boolean | Promise<boolean>;
  onArchive?: () => boolean | Promise<boolean>;
  onClose: () => void;
}

export default function DeleteChatDialog({ sessionTitle, onDelete, onArchive, onClose }: Props) {
  const dialog = useRef<HTMLElement>(null);
  const request = useRef<symbol | null>(null);
  const [pending, setPending] = useState<DeleteAction>();
  const [failure, setFailure] = useState<DeleteAction>();
  useLayoutEffect(() => () => { request.current = null; }, []);

  const dismiss = () => { if (!request.current) onClose(); };
  useModalLayer(dialog, true, dismiss);

  const choose = async (action: DeleteAction) => {
    const run = action === 'delete' ? onDelete : onArchive;
    if (!run || request.current) return;
    const owner = Symbol(action);
    request.current = owner;
    setPending(action);
    setFailure(undefined);
    try {
      const accepted = await run();
      if (request.current !== owner) return;
      if (accepted === true) onClose();
      else setFailure(action);
    } catch {
      if (request.current === owner) setFailure(action);
    } finally {
      if (request.current === owner) {
        request.current = null;
        setPending(undefined);
      }
    }
  };

  return createPortal(<div className="delete-chat-overlay" onMouseDown={event => {
    if (event.target === event.currentTarget) dismiss();
  }}>
    <section ref={dialog} className="delete-chat-dialog" role="dialog" aria-modal="true" tabIndex={-1}
      aria-label="删除 Easel 对话" aria-busy={pending !== undefined} data-delete-chat-dialog>
      <h2>删除 Easel 对话</h2>
      <p>确认删除「{sessionTitle || '此对话'}」。如有运行中的回合，将先等待停止确认。</p>
      <dl>
        <dt>浏览器列表</dt>
        <dd>服务器确认后，从列表移除此对话。</dd>
        <dt>Easel 记录</dt>
        <dd>清除此对话的消息正文和目录记录，保留删除标记。</dd>
        <dt>引擎原生历史与交付</dt>
        <dd>引擎原生历史、任务日志、回执和交付文件会保留，按各自的访问权限查看。</dd>
      </dl>
      {pending && <p role="status" data-delete-chat-pending>
        {pending === 'delete' ? '正在等待 Easel 删除确认…' : '正在提交归档…'}
      </p>}
      {failure && <p role="alert" data-delete-chat-error>
        {failure === 'delete' ? '删除结果未确认，已保留列表中的对话。' : '归档结果未确认，已保留此对话。'}
      </p>}
      <footer>
        <button type="button" data-modal-autofocus data-delete-chat-dismiss disabled={pending !== undefined}
          onClick={dismiss}>取消</button>
        {failure && onArchive && <button type="button" data-delete-chat-action="archive" disabled={pending !== undefined}
          onClick={() => void choose('archive')}>改为归档</button>}
        <button type="button" data-delete-chat-action="delete" disabled={pending !== undefined}
          onClick={() => void choose('delete')}>{failure ? '重试删除' : '确认删除'}</button>
      </footer>
    </section>
  </div>, document.body);
}
