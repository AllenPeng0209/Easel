/**
 * Scoped @ candidates adapted from DeepSeek Harness ui-reference/src/client/index.ts
 * and ui-input-trigger @ ce7073d (MIT). Selection uses a Host receipt, never a source path.
 */
import { useEffect, useId, useRef, useState } from 'react';
import type { ChatAttachmentCandidate, UploadedFile } from '../lib/api';
import { attachmentHref } from '../vendor/deepseek-harness/message/attachment-reference';
import type { FileReferenceTrigger } from './fileReferenceTrigger';
import './ComposerCommandMenu.css';
import './ComposerFileMenu.css';

export type ComposerFileOption =
  | { kind: 'attachment'; file: UploadedFile }
  | { kind: 'library'; file: ChatAttachmentCandidate };

export function isComposerFileReceipt(file: unknown): file is UploadedFile {
  if (!file || typeof file !== 'object') return false;
  const { id, name, path } = file as Partial<UploadedFile>;
  return typeof id === 'string' && !!id.trim() && typeof name === 'string' && !!name.trim()
    && !/\p{Cc}/u.test(name) && typeof path === 'string' && path.startsWith('_inbox/') && attachmentHref(path) !== null;
}

type SearchResult = { files?: UploadedFile[]; candidates?: ChatAttachmentCandidate[] };

export function useComposerFileMenu({ trigger, disabled, load, onPick, onSelecting }: {
  trigger?: FileReferenceTrigger;
  disabled: boolean;
  load: (query: string, signal: AbortSignal) => Promise<SearchResult>;
  onPick: (option: ComposerFileOption, trigger: FileReferenceTrigger, signal: AbortSignal) => Promise<boolean>;
  onSelecting: (selecting: boolean) => void;
}) {
  const menuId = useId();
  const [dismissed, setDismissed] = useState<string>();
  const [index, setIndex] = useState(0);
  const [result, setResult] = useState<{ token: string; rows: ComposerFileOption[] }>();
  const [failed, setFailed] = useState<string>();
  const [selectError, setSelectError] = useState<string>();
  const [selecting, setSelecting] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const selection = useRef<AbortController | null>(null);
  const pendingCallback = useRef(onSelecting);
  pendingCallback.current = onSelecting;
  const token = trigger ? JSON.stringify([trigger.key, trigger.start, trigger.end, trigger.raw]) : undefined;
  const open = !disabled && token !== undefined && dismissed !== token;
  const query = trigger?.query;
  const rows = open && result?.token === token ? result.rows : [];
  const active = Math.min(index, rows.length - 1);

  useEffect(() => {
    if (token === undefined) setDismissed(undefined);
  }, [token]);

  useEffect(() => {
    if (!open || token === undefined || query === undefined) return;
    const controller = new AbortController();
    setIndex(0);
    setResult(undefined);
    setFailed(undefined);
    setSelectError(undefined);
    const timer = setTimeout(() => {
      void load(query, controller.signal).then(resultValue => {
        if (controller.signal.aborted) return;
        const files = Array.isArray(resultValue?.files) ? resultValue.files.filter(isComposerFileReceipt) : [];
        const candidates = Array.isArray(resultValue?.candidates) ? resultValue.candidates.filter(file =>
          file && typeof file.id === 'string' && /^[a-f0-9]{64}$/.test(file.id)
          && typeof file.name === 'string' && !!file.name.trim() && !/\p{Cc}/u.test(file.name)) : [];
        setResult({ token, rows: [
          ...files.map(file => ({ kind: 'attachment' as const, file })),
          ...candidates.map(file => ({ kind: 'library' as const, file })),
        ] });
      }).catch(() => {
        if (!controller.signal.aborted) setFailed(token);
      });
    }, 120);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [open, token, query, load, attempt]);

  useEffect(() => () => {
    if (!selection.current) return;
    selection.current.abort();
    selection.current = null;
    pendingCallback.current(false);
    setSelecting(false);
  }, [token, disabled]);

  const close = () => {
    if (selection.current) {
      selection.current.abort();
      selection.current = null;
      pendingCallback.current(false);
      setSelecting(false);
    }
    setDismissed(token);
  };
  const choose = async (option: ComposerFileOption) => {
    if (!open || !trigger || selection.current) return;
    const controller = new AbortController();
    selection.current = controller;
    pendingCallback.current(true);
    setSelecting(true);
    setSelectError(undefined);
    try {
      const inserted = await onPick(option, trigger, controller.signal);
      if (controller.signal.aborted || selection.current !== controller) return;
      if (inserted) setDismissed(token);
      else setSelectError(token);
    } catch {
      if (!controller.signal.aborted && selection.current === controller) setSelectError(token);
    } finally {
      if (selection.current === controller) {
        selection.current = null;
        pendingCallback.current(false);
        setSelecting(false);
      }
    }
  };
  const keyDown = (event: Pick<KeyboardEvent, 'key' | 'preventDefault'>) => {
    if (!open || !['ArrowDown', 'ArrowUp', 'Escape', 'Enter'].includes(event.key)) return false;
    event.preventDefault();
    if (event.key === 'Escape') close();
    else if (event.key === 'Enter') {
      if (active >= 0) void choose(rows[active]);
    } else if (rows.length && !selecting) {
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      setIndex(current => (Math.min(current, rows.length - 1) + direction + rows.length) % rows.length);
    }
    return true;
  };

  return {
    open, menuId, activeId: open && active >= 0 ? `${menuId}-${active}` : undefined, keyDown, close,
    content: open ? <div className="composer-command-menu composer-file-menu">
      <header><strong>可用文件</strong><small>↑↓ 选择 · Enter 插入 · Esc 关闭</small></header>
      <ul id={menuId} role="listbox" aria-label="可用文件">
        {rows.map((option, rowIndex) => <li key={`${option.kind}:${option.file.id}`} role="presentation">
          <button id={`${menuId}-${rowIndex}`} type="button" role="option" aria-selected={rowIndex === active}
            aria-label={`引用 ${option.file.name}`} onMouseDown={event => event.preventDefault()}
            disabled={selecting} onClick={() => void choose(option)}>
            <strong>{option.file.name}</strong>
            <small>{option.kind === 'attachment' ? '当前对话附件' : <>当前 IP 素材
              {typeof option.file.context === 'string' && option.file.context && !option.file.context.startsWith('/') &&
                !/\p{Cc}/u.test(option.file.context) && <> · {option.file.context}</>}</>}</small>
          </button>
        </li>)}
      </ul>
      {selecting ? <p role="status">正在准备所选文件…</p>
        : selectError === token ? <p role="alert">未能插入文件，请重新选择。</p>
          : failed === token ? <p role="alert">文件读取失败。 <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button></p>
            : result?.token !== token ? <p role="status">正在搜索文件…</p>
              : !rows.length ? <p role="status">没有可引用的匹配文件。</p> : null}
    </div> : null,
  };
}
