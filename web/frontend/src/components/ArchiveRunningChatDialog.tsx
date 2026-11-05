/**
 * Request-local archive confirmation adapted from DeepSeek Harness
 * ui-workspace/src/client/session-actions/ArchiveSession.tsx @ ce7073d (MIT).
 * 响芽 exposes the independent background and exact-stop intents.
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import './ArchiveRunningChatDialog.css';

type ArchiveAction = 'continue' | 'stop';

interface Props {
  sessionTitle: string;
  onContinue: () => boolean | Promise<boolean>;
  onStopAndArchive: () => boolean | Promise<boolean>;
  onClose: () => void;
}

export default function ArchiveRunningChatDialog({ sessionTitle, onContinue, onStopAndArchive, onClose }: Props) {
  const dialog = useRef<HTMLElement>(null);
  const owner = useRef<symbol | null>(null);
  const [pending, setPending] = useState<ArchiveAction>();
  const [failure, setFailure] = useState<ArchiveAction>();
  useLayoutEffect(() => () => { owner.current = null; }, []);

  const dismiss = () => { if (!owner.current) onClose(); };
  useModalLayer(dialog, true, dismiss);

  const choose = async (action: ArchiveAction) => {
    if (owner.current) return;
    const request = Symbol(action);
    owner.current = request;
    setPending(action);
    setFailure(undefined);
    try {
      const accepted = await (action === 'continue' ? onContinue() : onStopAndArchive());
      if (owner.current !== request) return;
      if (accepted === true) onClose();
      else setFailure(action);
    } catch {
      if (owner.current === request) setFailure(action);
    } finally {
      if (owner.current === request) {
        owner.current = null;
        setPending(undefined);
      }
    }
  };

  return createPortal(<div className="archive-running-overlay" onMouseDown={event => {
    if (event.target === event.currentTarget) dismiss();
  }}>
    <section ref={dialog} className="archive-running-dialog" role="dialog" aria-modal="true" tabIndex={-1}
      aria-label="归档运行中的对话" aria-busy={pending !== undefined} data-archive-running-dialog>
      <h2>归档运行中的对话</h2>
      <p>「{sessionTitle || '此对话'}」仍有运行中的任务。请选择归档方式。</p>
      <dl>
        <dt>继续后台并归档</dt>
        <dd>对话移入已归档列表。当前任务继续运行，结果保留在原对话；后续排队消息会暂停。</dd>
        <dt>停止后归档</dt>
        <dd>先请求停止当前回合，确认结束后再归档，并保留已收到的内容。</dd>
      </dl>
      {pending && <p role="status" data-archive-running-pending>
        {pending === 'stop' ? '正在等待停止确认…' : '正在提交后台归档…'}
      </p>}
      {failure && <p role="alert" data-archive-running-error>
        {failure === 'stop' ? '停止结果未确认，已保留此对话。' : '归档结果未确认，已保留此对话。'}
      </p>}
      <footer>
        <button type="button" data-modal-autofocus data-archive-running-dismiss disabled={pending !== undefined}
          onClick={dismiss}>取消</button>
        <button type="button" data-archive-running-action="continue" disabled={pending !== undefined}
          onClick={() => void choose('continue')}>继续后台并归档</button>
        <button type="button" data-archive-running-action="stop" disabled={pending !== undefined}
          onClick={() => void choose('stop')}>停止后归档</button>
      </footer>
    </section>
  </div>, document.body);
}
