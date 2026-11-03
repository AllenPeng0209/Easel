/**
 * Adapted from DeepSeek Harness ui-reference/src/client/index.ts @ ce7073d (MIT).
 * The candidate row resolves an opaque session ID; Easel adds an explicit snapshot preview
 * and only inserts the inspected ID/hash rather than copying transcript content into the draft.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { SessionReferencePreview } from '../lib/api';
import { sessionReferenceLabel, validateChatSessionReferences } from '../lib/chatReferences';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import './SessionReferencePicker.css';

export interface SessionReferenceCandidate {
  id: string;
  title: string;
}

type PreviewState = { kind: 'loading' | 'error'; sessionId: string }
  | { kind: 'ready'; snapshot: SessionReferencePreview };

export default function SessionReferencePicker({ candidates, inspect, onInsert, onClose }: {
  candidates: readonly SessionReferenceCandidate[];
  inspect: (sessionId: string, signal: AbortSignal) => Promise<SessionReferencePreview>;
  onInsert: (snapshot: SessionReferencePreview) => boolean;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const request = useRef<AbortController | null>(null);
  const [query, setQuery] = useState('');
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [insertError, setInsertError] = useState('');
  const close = () => { request.current?.abort(); onClose(); };
  useModalLayer(dialog, true, close);
  useEffect(() => () => request.current?.abort(), []);

  const rows = candidates.filter(candidate => candidate.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const selected = preview?.kind === 'ready' ? preview.snapshot.sessionId : preview?.sessionId;
  const scoped = candidates.some(candidate => candidate.id === selected);
  const snapshot = scoped && preview?.kind === 'ready' ? preview.snapshot : undefined;
  const bytes = snapshot === undefined ? undefined
    : new TextEncoder().encode(snapshot.preview.map(entry => entry.content).join('')).length;

  const openPreview = async (sessionId: string) => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setPreview({ kind: 'loading', sessionId });
    setInsertError('');
    try {
      const value = await inspect(sessionId, controller.signal);
      if (controller.signal.aborted || request.current !== controller) return;
      if (value?.sessionId !== sessionId || typeof value.label !== 'string' || typeof value.truncated !== 'boolean' ||
          !Array.isArray(value.preview) || !value.preview.every(entry => entry &&
            (entry.role === 'user' || entry.role === 'assistant') && typeof entry.content === 'string')) {
        throw new Error('Mismatched snapshot');
      }
      const label = sessionReferenceLabel(value.label);
      if (!validateChatSessionReferences(label, [{ start: 0, end: label.length,
        sessionId, snapshotHash: value.snapshotHash, label: value.label }])) throw new Error('Invalid snapshot reference');
      setPreview({ kind: 'ready', snapshot: value });
    } catch {
      if (!controller.signal.aborted && request.current === controller) setPreview({ kind: 'error', sessionId });
    }
  };

  return createPortal(<div className="session-reference-overlay" onMouseDown={event => {
    if (event.target === event.currentTarget) close();
  }}>
    <section ref={dialog} className="session-reference-dialog" role="dialog" aria-modal="true"
      aria-label="引用历史对话" tabIndex={-1}>
      <header>
        <div><h2>引用历史对话</h2><p>先预览内容，再把这个版本插入正文。</p></div>
        <button type="button" aria-label="关闭历史对话引用" onClick={close}>×</button>
      </header>
      <div className="session-reference-body">
        <div>
          <input type="search" aria-label="搜索历史对话" data-modal-autofocus placeholder="搜索历史对话"
            value={query} onChange={event => setQuery(event.target.value)} />
          <ul className="session-reference-candidates">
            {rows.map(candidate => <li key={candidate.id}>
              <button type="button" aria-label={`预览 ${candidate.title}`}
                aria-pressed={candidate.id === selected} onClick={() => void openPreview(candidate.id)}>
                {candidate.title}
              </button>
            </li>)}
          </ul>
          {rows.length === 0 && <p>没有匹配的历史对话。</p>}
        </div>
        <div className="session-reference-preview">
          {!preview && <p>请选择要预览的历史对话。</p>}
          {preview && !scoped && <p role="status">这条对话当前不可引用。</p>}
          {scoped && preview?.kind === 'loading' && <p role="status">正在读取对话快照…</p>}
          {scoped && preview?.kind === 'error' && <p role="alert">无法读取这条对话，请重新预览。</p>}
          {snapshot && <>
            <h3>{snapshot.label}</h3>
            <p>来源：当前 IP 的历史对话 · 预览正文 {bytes} 字节</p>
            {snapshot.truncated && <p role="status">预览已截断。</p>}
            <ol>{snapshot.preview.map((entry, index) => <li key={index}>
              <strong>{entry.role === 'user' ? '用户' : '助手'}</strong>
              <p>{entry.content}</p>
            </li>)}</ol>
          </>}
        </div>
      </div>
      <footer>
        {insertError && <p role="alert">{insertError}</p>}
        <button type="button" onClick={close}>取消</button>
        {snapshot && <button type="button" className="session-reference-insert" data-insert-session-reference
          onClick={() => {
            if (onInsert(snapshot)) close();
            else setInsertError('当前无法插入，请检查发送状态和正文引用数量。');
          }}>插入引用</button>}
      </footer>
    </section>
  </div>, document.body);
}
