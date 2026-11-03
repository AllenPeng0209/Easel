/**
 * Adapted from DeepSeek Harness ui-reference/src/client/index.ts @ ce7073d (MIT).
 * The candidate row resolves an opaque session ID; Easel adds an explicit snapshot preview
 * and only inserts the inspected ID/hash rather than copying transcript content into the draft.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { SessionReferencePreview, SessionReferenceTurnPage } from '../lib/api';
import { sessionReferenceLabel, validateChatSessionReferences } from '../lib/chatReferences';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import './SessionReferencePicker.css';

export interface SessionReferenceCandidate {
  id: string;
  title: string;
}

type PreviewState = { kind: 'loading' | 'error'; sessionId: string; turnId?: string }
  | { kind: 'ready'; snapshot: SessionReferencePreview };

type TurnState = { rows: SessionReferenceTurnPage['turns']; before?: string | null; loading: boolean; failed?: boolean };

export default function SessionReferencePicker({ candidates, inspect, listTurns, onInsert, onClose }: {
  candidates: readonly SessionReferenceCandidate[];
  inspect: (sessionId: string, signal: AbortSignal, turnId?: string) => Promise<SessionReferencePreview>;
  listTurns?: (sessionId: string, before?: string, signal?: AbortSignal) => Promise<SessionReferenceTurnPage>;
  onInsert: (snapshot: SessionReferencePreview) => boolean;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const request = useRef<AbortController | null>(null);
  const turnRequest = useRef<AbortController | null>(null);
  const turnCursors = useRef(new Set<string>());
  const [selected, setSelected] = useState<string>();
  const [mode, setMode] = useState<'summary' | 'turn'>('summary');
  const [turns, setTurns] = useState<TurnState>();
  const [query, setQuery] = useState('');
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [insertError, setInsertError] = useState('');
  const close = () => { request.current?.abort(); turnRequest.current?.abort(); onClose(); };
  useModalLayer(dialog, true, close);
  useEffect(() => () => { request.current?.abort(); turnRequest.current?.abort(); }, []);

  const rows = candidates.filter(candidate => candidate.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const selectedTurn = preview?.kind === 'ready' ? preview.snapshot.turnId : preview?.turnId;
  const scoped = candidates.some(candidate => candidate.id === selected);
  const snapshot = scoped && preview?.kind === 'ready' && preview.snapshot.sessionId === selected &&
    (mode === 'turn') === !!preview.snapshot.turnId ? preview.snapshot : undefined;
  const bytes = snapshot === undefined ? undefined
    : new TextEncoder().encode(snapshot.preview.map(entry => entry.content).join('')).length;

  const openPreview = async (sessionId: string, turnId?: string) => {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setPreview({ kind: 'loading', sessionId, turnId });
    setInsertError('');
    try {
      const value = await inspect(sessionId, controller.signal, turnId);
      if (controller.signal.aborted || request.current !== controller) return;
      if (value?.sessionId !== sessionId || value.turnId !== turnId || typeof value.label !== 'string' || typeof value.truncated !== 'boolean' ||
          !Array.isArray(value.preview) || !value.preview.every(entry => entry &&
            (entry.role === 'user' || entry.role === 'assistant') && typeof entry.content === 'string')) {
        throw new Error('Mismatched snapshot');
      }
      const label = sessionReferenceLabel(value.label);
      if (!validateChatSessionReferences(label, [{ start: 0, end: label.length,
        sessionId, snapshotHash: value.snapshotHash, label: value.label, ...(turnId !== undefined ? { turnId } : {}) }])) throw new Error('Invalid snapshot reference');
      setPreview({ kind: 'ready', snapshot: value });
    } catch {
      if (!controller.signal.aborted && request.current === controller) setPreview({ kind: 'error', sessionId, turnId });
    }
  };


  const loadTurns = async (sessionId: string, before?: string) => {
    if (!listTurns || turnRequest.current) return;
    const controller = new AbortController();
    turnRequest.current = controller;
    const priorRows = before ? turns?.rows ?? [] : [];
    setTurns({ rows: priorRows, before, loading: true });
    try {
      const page = await listTurns(sessionId, before, controller.signal);
      if (controller.signal.aborted || turnRequest.current !== controller) return;
      if (!Array.isArray(page?.turns) || page.turns.some(turn => !turn || typeof turn.turnId !== 'string' ||
          !/^[A-Za-z0-9_.-]{1,160}$/.test(turn.turnId) || typeof turn.label !== 'string' || !turn.label.trim())
          || (page.before !== null && (typeof page.before !== 'string' || !page.before || turnCursors.current.has(page.before)))) {
        throw new Error('Invalid turn page');
      }
      if (page.before !== null) turnCursors.current.add(page.before);
      const rowsById = new Map(priorRows.map(row => [row.turnId, row]));
      for (const row of page.turns) if (!rowsById.has(row.turnId)) rowsById.set(row.turnId, row);
      setTurns({ rows: [...rowsById.values()], before: page.before, loading: false });
    } catch {
      if (!controller.signal.aborted && turnRequest.current === controller) {
        setTurns({ rows: priorRows, before, loading: false, failed: true });
      }
    } finally {
      if (turnRequest.current === controller) turnRequest.current = null;
    }
  };

  const showSummary = (sessionId: string) => {
    turnRequest.current?.abort();
    turnRequest.current = null;
    turnCursors.current.clear();
    setTurns(undefined);
    setSelected(sessionId);
    setMode('summary');
    void openPreview(sessionId);
  };

  const showTurns = (sessionId: string) => {
    request.current?.abort();
    setPreview(null);
    setInsertError('');
    setMode('turn');
    void loadTurns(sessionId);
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
                aria-pressed={candidate.id === selected} onClick={() => showSummary(candidate.id)}>
                {candidate.title}
              </button>
            </li>)}
          </ul>
          {rows.length === 0 && <p>没有匹配的历史对话。</p>}
        </div>
        <div className="session-reference-preview">
          {!selected && <p>请选择要预览的历史对话。</p>}
          {selected && !scoped && <p role="status">这条对话当前不可引用。</p>}
          {selected && scoped && listTurns && <div className="session-reference-modes" role="group" aria-label="引用范围">
            <button type="button" data-session-reference-mode="summary" aria-pressed={mode === 'summary'}
              onClick={() => { if (mode !== 'summary') showSummary(selected); }}>会话摘要</button>
            <button type="button" data-session-reference-mode="turn" aria-pressed={mode === 'turn'}
              onClick={() => { if (mode !== 'turn') showTurns(selected); }}>指定轮次</button>
          </div>}
          {selected && scoped && mode === 'turn' && <>
            {!!turns?.rows.length && <ul className="session-reference-turns" aria-label="已完成轮次">
              {turns.rows.map(turn => <li key={turn.turnId}>
                <button type="button" data-session-reference-turn={turn.turnId} aria-pressed={selectedTurn === turn.turnId}
                  onClick={() => void openPreview(selected, turn.turnId)}>{turn.label}</button>
              </li>)}
            </ul>}
            {turns?.loading && <p role="status">正在读取已完成轮次…</p>}
            {turns?.failed && <p role="alert">暂时无法读取已完成轮次。</p>}
            {turns && !turns.loading && !turns.failed && !turns.rows.length && turns.before === null &&
              <p>没有可引用的已完成轮次。</p>}
            {turns && !turns.loading && (turns.before || turns.failed) &&
              <button type="button" data-session-reference-more-turns
                onClick={() => void loadTurns(selected, turns.before ?? undefined)}>
                {turns.failed ? '重试读取轮次' : '加载较早轮次'}
              </button>}
          </>}
          {scoped && preview?.kind === 'loading' && <p role="status">正在读取对话快照…</p>}
          {scoped && preview?.kind === 'error' && <p role="alert">无法读取这条对话，请重新预览。</p>}
          {snapshot && <>
            <h3>{snapshot.label}</h3>
            <p>来源：当前 IP 的{snapshot.turnId ? '指定轮次' : '历史对话'} · 预览正文 {bytes} 字节</p>
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
