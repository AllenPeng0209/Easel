/**
 * Settled token rendering adapted from DeepSeek Harness
 * ui-primitives/src/markdown/CodeBlock.tsx @ ce7073d (MIT).
 * The sanitizer owns the HTML. Highlighting only decorates existing code text.
 */
import { highlightLanguage, MAX_HIGHLIGHT_LENGTH } from '../vendor/deepseek-harness/markdown/code-language';
import type { HighlightLine } from '../vendor/deepseek-harness/markdown/highlight';

function selectionIntersects(code: HTMLElement): boolean {
  const selection = code.ownerDocument.getSelection?.();
  if (!selection || selection.isCollapsed) return false;
  for (let index = 0; index < selection.rangeCount; index++) {
    if (selection.getRangeAt(index).intersectsNode?.(code)) return true;
  }
  return false;
}

function decoratedCode(document: Document, lines: HighlightLine[], original: string): DocumentFragment | undefined {
  if (lines.map(line => line.map(token => token.text).join('')).join('\n') !== original) return undefined;
  const result = document.createDocumentFragment();
  for (const [index, line] of lines.entries()) {
    if (index > 0) result.append(document.createTextNode('\n'));
    for (const token of line) {
      const element = document.createElement('span');
      element.setAttribute('data-code-token', '');
      element.textContent = token.text;
      if (/^var\(--shiki-[a-z0-9-]+\)$/.test(token.color)) element.style.color = token.color;
      const style = token.fontStyle ?? 0;
      if (style > 0) {
        if (style & 1) element.style.fontStyle = 'italic';
        if (style & 2) element.style.fontWeight = 'bold';
        if (style & 4) element.style.textDecorationLine = 'underline';
      }
      result.append(element);
    }
  }
  return result;
}

const MAX_ANSWER_BLOCKS = 12;
const MAX_ANSWER_LENGTH = 48_000;
const MAX_ANSWER_TOKENS = 8_000;

/** Start the grammar chunk only when the settled answer contains a supported fence. */
export function highlightSettledAnswer(root: HTMLElement): (() => void) | undefined {
  const blocks: { code: HTMLElement; language: NonNullable<ReturnType<typeof highlightLanguage>>; original: string }[] = [];
  let availableChars = MAX_ANSWER_LENGTH;
  for (const code of root.querySelectorAll<HTMLElement>('pre > code')) {
    if (blocks.length >= MAX_ANSWER_BLOCKS || availableChars === 0) break;
    const hint = [...code.classList].find(name => name.startsWith('language-'))?.slice('language-'.length);
    const language = highlightLanguage(hint);
    const original = code.textContent || '';
    if (!language || code.childElementCount || !original.trim()
        || original.length > MAX_HIGHLIGHT_LENGTH || original.length > availableChars) continue;
    blocks.push({ code, language, original });
    availableChars -= original.length;
  }
  if (!blocks.length) return undefined;

  const document = root.ownerDocument;
  let active = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopSelectionListener: (() => void) | undefined;
  void import('../vendor/deepseek-harness/markdown/highlight').then(({ highlightCode }) => {
    if (!active || !root.isConnected) return;
    const pending = new Set(blocks);
    let availableTokens = MAX_ANSWER_TOKENS;
    const schedule = () => {
      if (active && pending.size && timer === undefined) timer = setTimeout(decorateNext, 0);
    };
    const finish = () => {
      if (!pending.size || !availableTokens) stopSelectionListener?.();
    };
    const decorateNext = () => {
      timer = undefined;
      if (!active || !root.isConnected) { stopSelectionListener?.(); return; }
      for (const block of pending) {
        const { code, language, original } = block;
        if (!root.contains(code) || code.textContent !== original || code.childElementCount) {
          pending.delete(block);
          continue;
        }
        if (selectionIntersects(code)) continue;
        pending.delete(block);
        const lines = highlightCode(original, language);
        const count = lines?.reduce((total, line) => total + line.length, 0) ?? 0;
        if (lines && count <= availableTokens) {
          const decorated = decoratedCode(document, lines, original);
          if (decorated) {
            code.replaceChildren(decorated);
            code.dataset.codeHighlighted = language;
            availableTokens -= count;
          }
        }
        // Yield after each tokenized fence; blocked ranges wait for selectionchange.
        if (availableTokens) schedule();
        finish();
        return;
      }
      finish();
    };
    document.addEventListener('selectionchange', schedule);
    stopSelectionListener = () => document.removeEventListener('selectionchange', schedule);
    schedule();
  }).catch(() => { /* The original sanitized code is already visible. */ });
  return () => {
    active = false;
    if (timer !== undefined) clearTimeout(timer);
    stopSelectionListener?.();
  };
}
