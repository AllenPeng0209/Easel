/**
 * Scoped download dialog adapted from DeepSeek Harness session-log-export
 * src/client/{Dialog.tsx,controller.ts} @ ce7073d (MIT).
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import './SessionLogExportDialog.css';

interface Props {
  sessionId: string;
  sessionTitle: string;
  onDownload: (id: string, signal: AbortSignal) => Promise<void>;
  onClose: () => void;
}

export default function SessionLogExportDialog({ sessionId, sessionTitle, onDownload, onClose }: Props) {
  const dialog = useRef<HTMLElement>(null);
  const active = useRef<AbortController | null>(null);
  const [status, setStatus] = useState<'idle' | 'downloading' | 'success' | 'error'>('idle');
  const [error, setError] = useState('');
  const close = () => { active.current?.abort(); active.current = null; onClose(); };
  useModalLayer(dialog, true, close);
  useLayoutEffect(() => () => {
    active.current?.abort();
    active.current = null;
  }, []);

  const download = async () => {
    if (active.current || status === 'success') return;
    const request = new AbortController();
    active.current = request;
    setStatus('downloading');
    try {
      await onDownload(sessionId, request.signal);
      if (active.current === request) setStatus('success');
    } catch (cause) {
      if (active.current === request) {
        setError(cause instanceof Error && cause.message ? cause.message : '日志暂时无法导出，请稍后重试。');
        setStatus('error');
      }
    } finally {
      if (active.current === request) active.current = null;
    }
  };

  return createPortal(<div className="session-log-overlay" onMouseDown={event => {
    if (event.target === event.currentTarget) close();
  }}>
    <section ref={dialog} className="session-log-dialog" role="dialog" aria-modal="true" tabIndex={-1}
      aria-label="导出技术日志 ZIP" aria-busy={status === 'downloading'} data-session-log-export-dialog>
      <h2>导出技术日志 ZIP</h2>
      <p>对话「{sessionTitle || '当前对话'}」。仅当前 IP 有运营权限的平台管理员可导出。</p>
      <p>包含该会话可用的 响芽 执行事件和清单。排除消息正文、工具输入输出、附件文件及引擎本机内部日志。没有可用事件时，ZIP 仅包含清单。</p>
      {status === 'downloading' && <p role="status">正在准备技术日志…</p>}
      {status === 'success' && <p role="status">已交给浏览器下载。</p>}
      {status === 'error' && <p role="alert">{error}</p>}
      <footer>
        <button type="button" className="btn" data-modal-autofocus onClick={close}>{status === 'success' ? '关闭' : '取消'}</button>
        {status !== 'success' && <button type="button" className="btn btn-primary" data-download-session-logs
          disabled={status === 'downloading'} onClick={() => void download()}>
          {status === 'error' ? '重试下载' : '下载 ZIP'}
        </button>}
      </footer>
    </section>
  </div>, document.body);
}
