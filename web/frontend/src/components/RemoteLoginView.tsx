import { useCallback, useEffect, useRef, useState } from 'react';
import type { ClipboardEvent, CompositionEvent, FormEvent, KeyboardEvent, PointerEvent } from 'react';
import type { RemoteLoginDescriptor } from '../lib/api';
import { projectFetch } from '../lib/projectScope';
import '../styles/remoteLogin.css';

type Frame = {
  state: 'starting' | 'ready' | 'closed';
  image?: string;
  width?: number;
  height?: number;
  pageId?: string;
  pages?: { id: string; title: string }[];
};
type Action =
  | { kind: 'pointer'; event: 'down' | 'move' | 'up'; x: number; y: number; button: 'left' | 'right' }
  | { kind: 'wheel'; deltaX: number; deltaY: number }
  | { kind: 'key'; key: string }
  | { kind: 'text'; text: string }
  | { kind: 'page'; pageId: string }
  | { kind: 'reset' };
type PendingAction = { pageId: string; action: Action };
type Runtime = {
  key: string; active: boolean; stopped: boolean; paused: boolean; polling: boolean; sending: boolean; switching: boolean; revision: number; reconnect: boolean; framePending: boolean;
  controller: AbortController; queue: PendingAction[]; frame: Frame | null;
  pointer: { id: number; button: 'left' | 'right'; x: number; y: number } | null;
  pollTimer?: ReturnType<typeof setTimeout>; inputTimer?: ReturnType<typeof setTimeout>;
  poll: () => Promise<void>; flush: () => Promise<void>;
};

function pathFor(remote: RemoteLoginDescriptor): string {
  return `/api/accounts/${encodeURIComponent(remote.platform)}/remote/${encodeURIComponent(remote.id)}`;
}

/** Close only this login attempt. Saved platform accounts are not logged out. */
export async function closeRemoteLogin(remote: RemoteLoginDescriptor): Promise<boolean> {
  try {
    const response = await projectFetch(`${pathFor(remote)}/close`, { method: 'POST', cache: 'no-store', keepalive: true });
    return response.ok || [404, 410].includes(response.status);
  } catch { return false; }
}

const pageIsVisible = () => document.visibilityState !== 'hidden';

const shiftNavigationKeys = new Set(['Tab', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown']);
const navigationKeys = new Set(['Enter', 'Tab', 'Backspace', 'Delete', 'ArrowLeft', 'ArrowRight',
  'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown', 'Escape']);

export default function RemoteLoginView({ remote, onClose, onSettled, keepSessionOnUnmount = false }: {
  remote: RemoteLoginDescriptor;
  onClose?: () => void;
  onSettled?: (remote: RemoteLoginDescriptor, acknowledged: boolean) => void;
  /** A parent login view owns cleanup while this interactive view is optional. */
  keepSessionOnUnmount?: boolean;
}) {
  const { id, platform } = remote;
  const [frame, setFrame] = useState<Frame | null>(null);
  const [error, setError] = useState('');
  const [closed, setClosed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const runtime = useRef<Runtime | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  const keyboard = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);
  const settledCallback = useRef(onSettled);
  settledCallback.current = onSettled;

  useEffect(() => {
    const descriptor = { id, platform };
    const current: Runtime = {
      key: `${platform}:${id}`, active: true, stopped: false, paused: false,
      polling: false, sending: false, switching: false, revision: 0, reconnect: false, framePending: false, controller: new AbortController(), queue: [], frame: null,
      pointer: null, poll: async () => {}, flush: async () => {},
    };
    runtime.current = current;
    setFrame(null); setError(''); setClosed(false); setRefreshing(false);
    const base = pathFor(descriptor);
    const end = () => {
      current.stopped = true; current.queue = []; current.frame = null; current.pointer = null;
      if (current.active) { setFrame(null); setClosed(true); }
    };
    current.poll = async () => {
      clearTimeout(current.pollTimer);
      if (!current.active || current.stopped || current.polling || !pageIsVisible()) return;
      if (current.sending) { current.pollTimer = setTimeout(current.poll, 500); return; }
      current.polling = true;
      const revision = current.revision;
      try {
        const response = await projectFetch(`${base}/frame`, { cache: 'no-store', signal: current.controller.signal });
        if (!current.active) return;
        if (!response.ok) {
          if ([401, 403, 404, 410].includes(response.status)) { end(); return; }
          throw new Error('frame-unavailable');
        }
        const next = await response.json() as Frame;
        if (!current.active || revision !== current.revision) return;
        if (next.state === 'closed') { end(); return; }
        if (next.state !== 'starting' && (next.state !== 'ready' || !next.pageId ||
            !next.image?.match(/^data:image\/(jpeg|png);base64,/) || !Number.isFinite(next.width) ||
            !Number.isFinite(next.height) || (next.width || 0) <= 0 || (next.height || 0) <= 0)) {
          throw new Error('invalid-frame');
        }
        // A temporary capture gap is not a new browser page. Keep its DOM and
        // pointer capture alive so an already-sent mouse down can receive up.
        if (next.state === 'starting' && current.frame?.state === 'ready') {
          current.framePending = true; setRefreshing(true); return;
        }
        if (current.frame?.pageId !== next.pageId) { current.queue = []; current.pointer = null; }
        if (current.reconnect && next.state === 'ready') {
          current.reconnect = false;
          const reset = await projectFetch(`${base}/input`, { method: 'POST', cache: 'no-store',
            signal: current.controller.signal, headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pageId: next.pageId, actions: [{ kind: 'reset' }] }),
          });
          if (!current.active || revision !== current.revision) return;
          if (!reset.ok) {
            if ([401, 403, 404, 410].includes(reset.status)) end();
            setError('未能恢复登录操作，请重新连接或关闭后重试。');
            return;
          }
          current.paused = false;
        }
        current.frame = next;
        current.framePending = false; setRefreshing(false);
        if (next.state === 'ready') current.switching = false;
        setFrame(next);
      } catch {
        if (current.active && !current.controller.signal.aborted) setError('暂时无法显示登录页面，请检查网络后重新连接。');
      } finally {
        current.polling = false;
        if (current.active && !current.stopped && pageIsVisible()) {
          current.pollTimer = setTimeout(current.poll, 500);
        }
      }
    };
    current.flush = async () => {
      clearTimeout(current.inputTimer); current.inputTimer = undefined;
      if (current.sending || !current.active || current.stopped || current.paused) return;
      current.sending = true;
      let switched = false;
      try {
        while (current.active && !current.stopped && !current.paused && current.queue.length) {
          const pageId = current.queue[0].pageId;
          const actions: Action[] = [];
          const canAppend = () => current.queue[0]?.pageId === pageId && actions.length < 32 && current.queue[0].action.kind !== 'page';
          if (current.queue[0].action.kind === 'page') actions.push(current.queue.shift()!.action);
          else while (canAppend()) {
            actions.push(current.queue.shift()!.action);
          }
          const response = await projectFetch(`${base}/input`, {
            method: 'POST', cache: 'no-store', signal: current.controller.signal,
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pageId, actions }),
          });
          if (!current.active) return;
          if (!response.ok) {
            current.queue = []; current.pointer = null; current.paused = true;
            if ([401, 403, 404, 410].includes(response.status)) end();
            setError(response.status === 409 ? '登录页面已变化，请重新连接画面后继续。'
              : '这次操作未能确认，请重新连接后检查页面，再继续操作。');
            return;
          }
          if (actions.some(action => action.kind === 'page')) {
            current.queue = []; current.frame = null; current.pointer = null; switched = true;
            setFrame({ state: 'starting' });
          }
        }
      } catch {
        current.queue = []; current.pointer = null; current.paused = true;
        if (current.active && !current.controller.signal.aborted) setError('操作连接已中断，请重新连接后检查页面，再继续操作。');
      } finally {
        current.sending = false;
        if (switched) void current.poll();
      }
    };
    const visibility = () => {
      clearTimeout(current.pollTimer);
      if (pageIsVisible()) void current.poll();
    };
    let closeRequested = false;
    const closeOnce = () => {
      if (closeRequested) return;
      closeRequested = true;
      if (keepSessionOnUnmount) return;
      void closeRemoteLogin(descriptor).then(acknowledged => settledCallback.current?.(descriptor, acknowledged));
    };
    const deactivate = () => {
      current.active = false; current.queue = []; current.frame = null; current.pointer = null;
      current.controller.abort();
      clearTimeout(current.pollTimer); clearTimeout(current.inputTimer);
    };
    const pagehide = () => {
      deactivate(); closeOnce();
      // Back/forward cache can restore this React tree after the login has
      // already been cancelled. Do not display or enable its stale page.
      setFrame(null); setClosed(true);
    };
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', pagehide);
    void current.poll();
    return () => {
      const releasePage = keepSessionOnUnmount && (current.pointer || current.sending) ? current.frame?.pageId : undefined;
      deactivate();
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', pagehide);
      if (releasePage) void projectFetch(`${base}/input`, { method: 'POST', cache: 'no-store', keepalive: true,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pageId: releasePage, actions: [{ kind: 'reset' }] }),
      }).catch(() => {});
      // React's development effect replay immediately creates another owner of
      // the same view. Navigation/unmount still cancels the actual attempt.
      queueMicrotask(() => {
        const replacement = runtime.current;
        if (replacement !== current && replacement?.active && replacement.key === current.key) return;
        closeOnce();
      });
    };
  }, [id, platform, keepSessionOnUnmount]);

  const enqueue = useCallback((action: Action, delayed = false) => {
    const current = runtime.current;
    if (!current?.active || current.stopped || current.paused || current.switching || current.frame?.state !== 'ready' || !current.frame.pageId) return;
    if (current.framePending && !(action.kind === 'pointer' && action.event === 'up')) return;
    if (current.queue.length >= 128) { current.queue = []; current.paused = true; setError('连接较慢，请重新连接后再操作。'); return; }
    if (action.kind === 'page') { current.switching = true; current.revision++; }
    const previous = current.queue.at(-1);
    const item = { pageId: current.frame.pageId, action };
    if (action.kind === 'pointer' && action.event === 'move' && previous?.action.kind === 'pointer' && previous.action.event === 'move') {
      current.queue[current.queue.length - 1] = item;
    } else current.queue.push(item);
    if (delayed) {
      if (!current.inputTimer) current.inputTimer = setTimeout(() => { current.inputTimer = undefined; void current.flush(); }, 32);
    } else { clearTimeout(current.inputTimer); current.inputTimer = undefined; void current.flush(); }
  }, []);

  const point = (event: PointerEvent<HTMLDivElement>) => {
    const current = runtime.current;
    const rect = event.currentTarget.getBoundingClientRect();
    if (!current?.frame?.width || !current.frame.height || !rect.width || !rect.height) return null;
    return { x: Math.max(0, Math.min(current.frame.width - 1, Math.round((event.clientX - rect.left) / rect.width * current.frame.width))),
      y: Math.max(0, Math.min(current.frame.height - 1, Math.round((event.clientY - rect.top) / rect.height * current.frame.height))) };
  };
  const pointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (![0, 2].includes(event.button)) return;
    const position = point(event); const current = runtime.current;
    if (!position || !current || current.pointer || current.paused || current.switching || current.stopped || current.framePending) return;
    event.preventDefault(); event.stopPropagation();
    keyboard.current?.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const button = event.button === 2 ? 'right' : 'left';
    current.pointer = { id: event.pointerId, button, ...position };
    enqueue({ kind: 'pointer', event: 'down', button, ...position });
  };
  const pointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const current = runtime.current; const position = point(event);
    if (!position || current?.pointer?.id !== event.pointerId) return;
    event.preventDefault();
    current.pointer = { ...current.pointer, ...position };
    enqueue({ kind: 'pointer', event: 'move', button: current.pointer.button, ...position }, true);
  };
  const pointerUp = (event: PointerEvent<HTMLDivElement>) => {
    const current = runtime.current;
    if (!current?.pointer || current.pointer.id !== event.pointerId) return;
    event.preventDefault();
    const position = ['pointercancel', 'lostpointercapture'].includes(event.type) ? current.pointer : point(event) || current.pointer;
    enqueue({ kind: 'pointer', event: 'up', button: current.pointer.button, x: position.x, y: position.y });
    current.pointer = null;
    try { event.currentTarget.releasePointerCapture?.(event.pointerId); } catch { /* Capture may already be released. */ }
  };

  useEffect(() => {
    const element = surface.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const factor = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 600 : 1;
      const horizontal = event.shiftKey && event.deltaX === 0;
      const deltaX = horizontal ? event.deltaY : event.deltaX;
      const deltaY = horizontal ? 0 : event.deltaY;
      enqueue({ kind: 'wheel', deltaX: Math.max(-1500, Math.min(1500, deltaX * factor)),
        deltaY: Math.max(-1500, Math.min(1500, deltaY * factor)) });
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, [enqueue, frame?.state]);

  const sendText = (value: string) => {
    if (!value) return;
    if (value.length > 2000) { setError('一次输入的内容过长，请分段输入。'); return; }
    enqueue({ kind: 'text', text: value });
  };
  const onInput = (event: FormEvent<HTMLTextAreaElement>) => {
    if (composing.current) return;
    const value = event.currentTarget.value;
    event.currentTarget.value = '';
    sendText(value);
  };
  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    event.preventDefault();
    event.currentTarget.value = '';
    sendText(event.clipboardData.getData('text/plain'));
  };
  const onCompositionEnd = (event: CompositionEvent<HTMLTextAreaElement>) => {
    composing.current = false;
    const value = event.data || event.currentTarget.value;
    event.currentTarget.value = '';
    sendText(value);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (composing.current || event.nativeEvent.isComposing) return;
    let key = event.key;
    if (event.ctrlKey || event.metaKey) {
      if (key.toLowerCase() === 'v') return;
      if (!['a', 'z', 'y'].includes(key.toLowerCase())) { event.preventDefault(); return; }
      key = `Control+${event.shiftKey && key.toLowerCase() === 'z' ? 'Shift+' : ''}${key.toUpperCase()}`;
    } else if (navigationKeys.has(key)) {
      if (event.shiftKey && shiftNavigationKeys.has(key)) key = `Shift+${key}`;
    } else return;
    event.preventDefault(); event.stopPropagation();
    enqueue({ kind: 'key', key });
  };
  const retry = () => {
    const current = runtime.current;
    if (!current || current.stopped) return;
    current.paused = true; current.reconnect = true; current.switching = false; current.revision++;
    current.queue = []; current.pointer = null; current.frame = null;
    setFrame({ state: 'starting' }); setError(''); void current.poll();
  };
  const usable = frame?.state === 'ready' && !!frame.image && !closed;
  return <section className="remote-login" aria-label="平台登录窗口">
    <div className="remote-login-toolbar">
      <p>在下方页面扫码或输入账号信息；验证码和安全验证请由你本人完成。</p>
      <div>
        <button className="btn btn-sm" type="button" disabled={!usable || refreshing} onClick={() => keyboard.current?.focus({ preventScroll: true })}>打开键盘</button>
        {!closed && <button className="btn btn-sm btn-ghost" type="button" onClick={retry}>重新连接</button>}
      </div>
    </div>
    {error && <p className="remote-login-error" role="alert">{error}</p>}
    {closed ? <p className="remote-login-wait" role="status">登录窗口已结束。请查看账号状态；如未完成，请关闭后重新登录。</p>
      : usable ? <>
        {(frame.pages?.length || 0) > 1 && <label className="remote-login-pages">登录页面
          <select value={frame.pageId} onChange={event => enqueue({ kind: 'page', pageId: event.target.value })}>
            {frame.pages?.map((page, index) => <option key={page.id} value={page.id}>{page.title || `页面 ${index + 1}`}</option>)}
          </select>
        </label>}
        <div className="remote-login-scroll">
          <div ref={surface} className="remote-login-surface" data-remote-surface role="application" aria-label="可操作的平台登录页面" title="点击或拖动操作；按住 Shift 滚轮可左右滚动"
            style={{ aspectRatio: `${frame.width} / ${frame.height}`, maxWidth: `calc(66dvh * ${(frame.width || 1) / (frame.height || 1)})` }}
            onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp} onLostPointerCapture={pointerUp}
            onContextMenu={event => event.preventDefault()}>
            {refreshing && <span className="remote-login-updating" role="status">页面更新中，请稍候…</span>}
            <img src={frame.image} alt="平台登录页面" draggable={false} />
          </div>
        </div>
      </> : <div className="remote-login-wait" role="status"><span className="spinner" />正在打开平台登录页面…</div>}
    <textarea ref={keyboard} className="remote-login-keyboard" data-remote-input aria-label="登录页面键盘输入"
      autoComplete="off" autoCorrect="off" autoCapitalize="off" spellCheck={false} inputMode="text" disabled={!usable || refreshing} tabIndex={usable && !refreshing ? 0 : -1}
      onInput={onInput} onPaste={onPaste} onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={onCompositionEnd} onKeyDown={onKeyDown} />
    {onClose && <button className="btn btn-sm remote-login-cancel" type="button" onClick={onClose}>结束本次登录</button>}
  </section>;
}
