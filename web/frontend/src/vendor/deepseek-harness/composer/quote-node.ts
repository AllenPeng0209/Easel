/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from ui-conversation/src/client/input/editor/QuoteNode.tsx
 * @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel mounts a native DOM decorator like its reference chips.
 */
import { createCommand, DecoratorNode, type EditorConfig, type LexicalEditor, type NodeKey,
  type SerializedLexicalNode, type Spread } from 'lexical';
import { formatQuoteText } from './quote-text';

type SerializedQuoteNode = Spread<{ text: string }, SerializedLexicalNode>;

export const REMOVE_QUOTE_COMMAND = createCommand<NodeKey>('REMOVE_QUOTE_COMMAND');

export class QuoteNode extends DecoratorNode<null> {
  __text: string;

  static override getType(): string { return 'quote'; }
  static override clone(node: QuoteNode): QuoteNode { return new QuoteNode(node.__text, node.__key); }
  static override importJSON(value: SerializedQuoteNode): QuoteNode { return new QuoteNode(value.text); }

  constructor(text: string, key?: NodeKey) {
    super(key);
    this.__text = text;
  }

  override exportJSON(): SerializedQuoteNode {
    return { ...super.exportJSON(), type: 'quote', version: 1, text: this.__text };
  }

  override createDOM(_config: EditorConfig, editor: LexicalEditor): HTMLElement {
    const element = document.createElement('span');
    element.setAttribute('data-composer-quote', '');
    element.setAttribute('contenteditable', 'false');
    const excerpt = document.createElement('span');
    excerpt.textContent = this.__text;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.setAttribute('aria-label', '移除此引用');
    remove.textContent = '×';
    remove.addEventListener('pointerdown', event => event.preventDefault());
    remove.addEventListener('click', () => {
      if (editor.isEditable()) editor.dispatchCommand(REMOVE_QUOTE_COMMAND, this.getKey());
    });
    element.append(excerpt, remove);
    return element;
  }

  override updateDOM(): false { return false; }
  override isInline(): true { return true; }
  override isKeyboardSelectable(): false { return false; }
  override decorate(): null { return null; }
  override getTextContent(): string { return formatQuoteText(this.getLatest().__text); }
}
