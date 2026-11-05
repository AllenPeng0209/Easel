/**
 * Adapted from DeepSeek Harness ui-input-trigger keyboard candidate behavior
 * @ ce7073d51254c26cd63b22a60835b8f948deb447, MIT.
 * 响芽's Host supplies the reviewed catalog. Browsing or selecting never executes it.
 */
import { Fragment, useEffect, useId, useState } from 'react';
import { validateChatCommand, type ChatCommand, type ChatCommandOption } from '../lib/chatCommands';
import './ComposerCommandMenu.css';

export function useComposerCommandMenu({ value, command, disabled, load, onPick }: {
  value: string;
  command?: ChatCommand;
  disabled: boolean;
  load: (signal: AbortSignal) => Promise<{ commands: ChatCommandOption[] }>;
  onPick: (command: ChatCommand, trigger?: string) => void;
}) {
  const menuId = useId();
  const [manual, setManual] = useState(false);
  const [dismissed, setDismissed] = useState<string>();
  const [commands, setCommands] = useState<ChatCommandOption[]>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [index, setIndex] = useState(0);
  const trigger = !command && /^\/[\p{L}\p{N}\p{M}_-]*$/u.test(value) && dismissed !== value ? value : undefined;
  const open = !disabled && (manual || trigger !== undefined);
  const needsCatalog = open || command !== undefined;
  const query = manual ? '' : trigger?.slice(1).toLocaleLowerCase() ?? '';
  const rows = commands?.filter(row => [row.token, row.label, row.description, row.group]
    .some(text => text.toLocaleLowerCase().includes(query))) ?? [];
  const active = Math.min(index, rows.length - 1);
  const activeId = open && active >= 0 ? `${menuId}-${active}` : undefined;

  useEffect(() => { setIndex(0); }, [query]);
  useEffect(() => {
    if (!needsCatalog || commands) return;
    const controller = new AbortController();
    setFailed(false);
    void load(controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (!Array.isArray(result.commands)) throw new Error('Invalid command catalog');
      setCommands(result.commands.filter(row => row &&
        [row.label, row.group, row.description, row.argumentHint].every(text => typeof text === 'string') &&
        validateChatCommand(row.token, { id: row.id, token: row.token }) !== null));
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
    return () => controller.abort();
  }, [needsCatalog, commands, load, attempt]);

  const close = () => { setManual(false); setDismissed(value); };
  const select = (row: ChatCommandOption) => {
    onPick({ id: row.id, token: row.token }, manual ? undefined : trigger);
    close();
  };
  const keyDown = (event: Pick<KeyboardEvent, 'key' | 'preventDefault'>) => {
    if (!open || !['ArrowDown', 'ArrowUp', 'Escape', 'Enter'].includes(event.key)) return false;
    event.preventDefault();
    if (event.key === 'Escape') close();
    else if (event.key === 'Enter') {
      if (active >= 0) select(rows[active]);
    } else if (rows.length) {
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      setIndex(current => (Math.min(current, rows.length - 1) + direction + rows.length) % rows.length);
    }
    return true;
  };

  return {
    menuId, activeId, open, commands,
    openExplicitly: () => { setIndex(0); setManual(true); },
    keyDown,
    content: open ? <div className="composer-command-menu">
      <header><strong>可用命令</strong><small>↑↓ 选择 · Enter 插入 · Esc 关闭</small></header>
      <ul id={menuId} role="listbox" aria-label="可用命令">
        {rows.map((row, rowIndex) => <Fragment key={row.id}>
          {row.group !== rows[rowIndex - 1]?.group && <li role="presentation"
            className="composer-command-group">{row.group}</li>}
          <li role="presentation">
            <button id={`${menuId}-${rowIndex}`} type="button" role="option" aria-selected={rowIndex === active}
              aria-label={`${row.token} ${row.label}`} onMouseDown={event => event.preventDefault()}
              onClick={() => select(row)}>
              <span><code>{row.token}</code> <strong>{row.label}</strong></span>
              <span>{row.description}</span>
              {row.argumentHint && <small>参数：{row.argumentHint}</small>}
            </button>
          </li>
        </Fragment>)}
      </ul>
      {failed ? <p role="alert">无法读取命令。 <button type="button" onClick={() => setAttempt(value => value + 1)}>重试</button></p>
        : !commands ? <p role="status">正在读取可用命令…</p>
          : rows.length === 0 ? <p role="status">没有匹配的命令。</p> : null}
    </div> : null,
  };
}
