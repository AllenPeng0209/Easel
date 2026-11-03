/**
 * Adapted from DeepSeek Harness ui-chat text-interaction.ts and
 * message-quotes/src/client/QuoteSelection.tsx @ ce7073d (MIT).
 * A rendered answer owns its visible selection and explicit quote action.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { readTextSelection, selectedText, type TextSelection } from './answerTextSelection';
import { MAX_ANSWER_QUOTE_LENGTH } from '../vendor/deepseek-harness/composer/quote-text';
import css from './AnswerQuoteSelection.module.css';

function sameRange(left: Range, right: Range): boolean {
  return left.startContainer === right.startContainer && left.startOffset === right.startOffset
    && left.endContainer === right.endContainer && left.endOffset === right.endOffset;
}

interface ActiveQuote {
  selection: TextSelection;
  text: string;
  top: number;
  left: number;
}

export default function AnswerQuoteSelection({ html, onQuoteAnswer }: {
  html: string;
  onQuoteAnswer?: (text: string) => boolean;
}) {
  const answer = useRef<HTMLDivElement>(null);
  const action = useRef<HTMLButtonElement>(null);
  const callback = useRef(onQuoteAnswer);
  callback.current = onQuoteAnswer;
  const [active, setActive] = useState<ActiveQuote | null>(null);
  const enabled = onQuoteAnswer !== undefined;
  // Reusing the html object keeps React from replacing live DOM Range endpoints.
  const body = useMemo(() => ({ __html: html }), [html]);

  useEffect(() => {
    const source = answer.current;
    if (!enabled || !source) return;
    const document = source.ownerDocument;
    const view = document.defaultView;
    let dismissed: Range | null = null;
    const dismiss = () => {
      dismissed = readTextSelection(source, document.getSelection?.() ?? null)?.range.cloneRange() ?? null;
      setActive(null);
    };
    const update = () => {
      const current = document.getSelection?.() ?? null;
      const selection = readTextSelection(source, current);
      if (selection && dismissed && sameRange(selection.range, dismissed)) return;
      dismissed = null;
      const text = selection && selectedText(selection);
      if (!selection || !text || text.length > MAX_ANSWER_QUOTE_LENGTH) {
        if ((!current || current.isCollapsed) && document.activeElement === action.current) return;
        setActive(null);
        return;
      }
      const range = selection.range.cloneRange();
      const bounds = source.getBoundingClientRect();
      const rect = range.getBoundingClientRect?.();
      const top = rect ? Math.max(0, rect.bottom - bounds.top + 5) : bounds.height;
      const left = rect ? Math.max(0, Math.min(rect.left - bounds.left, bounds.width - 108)) : 0;
      setActive({ selection: { source, range }, text, top, left });
    };
    const keyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') dismiss(); };
    const pointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && !source.contains(target) && !action.current?.contains(target)) dismiss();
    };
    document.addEventListener('selectionchange', update);
    document.addEventListener('pointerdown', pointerDown, true);
    document.addEventListener('pointerup', update);
    document.addEventListener('keyup', update);
    document.addEventListener('keydown', keyDown);
    document.addEventListener('scroll', update, true);
    view?.addEventListener('resize', update);
    update();
    return () => {
      document.removeEventListener('selectionchange', update);
      document.removeEventListener('pointerdown', pointerDown, true);
      document.removeEventListener('pointerup', update);
      document.removeEventListener('keyup', update);
      document.removeEventListener('keydown', keyDown);
      document.removeEventListener('scroll', update, true);
      view?.removeEventListener('resize', update);
    };
  }, [enabled, html]);

  const quote = () => {
    const source = answer.current;
    if (!active || !source || !source.isConnected) return;
    const { range } = active.selection;
    if (!source.contains(range.startContainer) || !source.contains(range.endContainer)
        || selectedText(active.selection) !== active.text) {
      setActive(null);
      return;
    }
    const current = source.ownerDocument.getSelection?.();
    if (current && !current.isCollapsed) {
      const visible = readTextSelection(source, current);
      if (!visible || !sameRange(visible.range, range) || selectedText(visible) !== active.text) { setActive(null); return; }
    }
    if (callback.current?.(active.text) !== true) return;
    const currentAfter = source.ownerDocument.getSelection?.() ?? null;
    const quoted = readTextSelection(source, currentAfter);
    if (quoted && sameRange(quoted.range, range)) currentAfter?.removeAllRanges();
    setActive(null);
  };
  return <div className={css.root}>
    <div ref={answer} data-assistant-answer data-text-interaction-source={enabled || undefined}
      tabIndex={enabled ? 0 : undefined} dangerouslySetInnerHTML={body} />
    {enabled && active && <button type="button" ref={action} className={css.action} data-answer-quote-action
      style={{ top: active.top, left: active.left }} onPointerDown={event => event.preventDefault()}
      onMouseDown={event => event.preventDefault()} onClick={quote}>引用这段答复</button>}
  </div>;
}
