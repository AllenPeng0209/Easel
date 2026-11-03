/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from deepseek-ai/deepseek-harness @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Upstream: ui-conversation/src/client/input/editor/{chip-node.tsx,projection.ts,runtime.ts}.
 * Easel uses one attachment reference source and projects UTF-16 receipt spans.
 * License: integrations/deepseek-harness/LICENSE.
 */
import {
  $applyNodeReplacement, $createParagraphNode, $createTextNode, $getAdjacentNode, $getRoot, $getSelection,
  $isElementNode, $isRangeSelection, COMMAND_PRIORITY_HIGH, DecoratorNode, HISTORY_PUSH_TAG,
  KEY_BACKSPACE_COMMAND, KEY_DELETE_COMMAND, type LexicalEditor, type LexicalNode, type NodeKey,
  type SerializedLexicalNode, type Spread,
} from 'lexical';
import { createEmptyHistoryState, registerHistory } from '@lexical/history';
import { registerPlainText } from '@lexical/plain-text';
import { mergeRegister } from '@lexical/utils';
import type { ChatReference } from '../../../lib/chatReferences';

export interface ReferenceDraft {
  text: string;
  references: ChatReference[];
}

type SerializedChip = Spread<{ attachmentId: string; label: string }, SerializedLexicalNode>;

export class ReferenceChipNode extends DecoratorNode<null> {
  __attachmentId: string;
  __label: string;

  static override getType(): string { return 'reference-chip'; }
  static override clone(node: ReferenceChipNode): ReferenceChipNode {
    return new ReferenceChipNode(node.__attachmentId, node.__label, node.__key);
  }
  static override importJSON(value: SerializedChip): ReferenceChipNode {
    return $createReferenceChip(value.attachmentId, value.label);
  }

  constructor(attachmentId = '', label = '', key?: NodeKey) {
    super(key);
    this.__attachmentId = attachmentId;
    this.__label = label;
  }

  override exportJSON(): SerializedChip {
    return { ...super.exportJSON(), attachmentId: this.__attachmentId, label: this.__label };
  }

  override createDOM(): HTMLElement {
    const element = document.createElement('span');
    element.setAttribute('data-composer-chip', 'file');
    element.setAttribute('contenteditable', 'false');
    element.textContent = this.__label;
    return element;
  }

  override updateDOM(): false { return false; }
  override isInline(): true { return true; }
  override isKeyboardSelectable(): false { return false; }
  override decorate(): null { return null; }
  override getTextContent(): string { return this.getLatest().__label; }
  getAttachmentId(): string { return this.getLatest().__attachmentId; }
}

function $createReferenceChip(attachmentId: string, label: string): ReferenceChipNode {
  return $applyNodeReplacement(new ReferenceChipNode(attachmentId, label));
}

/** Clipboard/persistence projection. Paragraph gaps count as one newline. */
export function $projectReferenceDraft(): ReferenceDraft {
  const result: ReferenceDraft = { text: '', references: [] };
  const visit = (node: LexicalNode) => {
    if (node instanceof ReferenceChipNode) {
      const start = result.text.length;
      result.text += node.getTextContent();
      result.references.push({ start, end: result.text.length, attachmentId: node.getAttachmentId() });
    } else if ($isElementNode(node)) {
      node.getChildren().forEach(visit);
    } else {
      result.text += node.getTextContent();
    }
  };
  $getRoot().getChildren().forEach((block, index) => {
    if (index > 0) result.text += '\n';
    visit(block);
  });
  return result;
}

/** Draft spans have already been validated against its current receipt set. */
export function $restoreReferenceDraft(value: ReferenceDraft): void {
  const root = $getRoot();
  root.clear();
  let paragraph = $createParagraphNode();
  root.append(paragraph);
  const append = (text: string) => {
    text.split('\n').forEach((line, index) => {
      if (index > 0) {
        paragraph = $createParagraphNode();
        root.append(paragraph);
      }
      if (line) paragraph.append($createTextNode(line));
    });
  };
  let offset = 0;
  for (const reference of value.references) {
    append(value.text.slice(offset, reference.start));
    paragraph.append($createReferenceChip(reference.attachmentId, value.text.slice(reference.start, reference.end)));
    offset = reference.end;
  }
  append(value.text.slice(offset));
  root.selectEnd();
}

export function sameReferenceDraft(left: ReferenceDraft, right: ReferenceDraft): boolean {
  return left.text === right.text && left.references.length === right.references.length
    && left.references.every(({ start, end, attachmentId }, index) => {
      const other = right.references[index];
      return start === other.start && end === other.end && attachmentId === other.attachmentId;
    });
}

export function insertReferenceChip(editor: LexicalEditor, attachmentId: string, label: string): void {
  editor.update(() => {
    let selection = $getSelection();
    if (!$isRangeSelection(selection)) selection = $getRoot().selectEnd();
    selection.insertNodes([$createReferenceChip(attachmentId, label), $createTextNode(' ')]);
  }, { discrete: true, tag: HISTORY_PUSH_TAG });
}

export function detachReferenceChips(editor: LexicalEditor, attachmentId: string): void {
  editor.update(() => {
    const visit = (node: LexicalNode) => {
      if (node instanceof ReferenceChipNode && node.getAttachmentId() === attachmentId) {
        node.replace($createTextNode(node.getTextContent()));
      }
      else if ($isElementNode(node)) node.getChildren().forEach(visit);
    };
    visit($getRoot());
  }, { discrete: true });
}

export function registerReferenceEditing(editor: LexicalEditor): () => void {
  const removeAdjacent = (event: KeyboardEvent | null, backward: boolean) => {
    const selection = $getSelection();
    if (!$isRangeSelection(selection) || !selection.isCollapsed()) return false;
    const adjacent = $getAdjacentNode(selection.anchor, backward);
    if (!(adjacent instanceof ReferenceChipNode)) return false;
    event?.preventDefault();
    adjacent.remove();
    return true;
  };
  return mergeRegister(
    registerPlainText(editor),
    registerHistory(editor, createEmptyHistoryState(), 1000),
    editor.registerCommand(KEY_BACKSPACE_COMMAND, event => removeAdjacent(event, true), COMMAND_PRIORITY_HIGH),
    editor.registerCommand(KEY_DELETE_COMMAND, event => removeAdjacent(event, false), COMMAND_PRIORITY_HIGH),
  );
}
