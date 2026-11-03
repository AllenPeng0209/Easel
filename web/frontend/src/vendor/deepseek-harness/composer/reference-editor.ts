/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from deepseek-ai/deepseek-harness @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Upstream: ui-conversation/src/client/input/editor/{chip-node.tsx,projection.ts,runtime.ts}.
 * Easel uses one attachment reference source and projects UTF-16 receipt spans.
 * License: integrations/deepseek-harness/LICENSE.
 */
import {
  $applyNodeReplacement, $createParagraphNode, $createTextNode, $getAdjacentNode, $getRoot, $getSelection, $getNodeByKey, $setSelection,
  $isElementNode, $isRangeSelection, COMMAND_PRIORITY_HIGH, DecoratorNode, HISTORY_PUSH_TAG,
  KEY_BACKSPACE_COMMAND, KEY_DELETE_COMMAND, RootNode, type LexicalEditor, type LexicalNode, type NodeKey, type RangeSelection,
  type SerializedLexicalNode, type Spread,
} from 'lexical';
import { createEmptyHistoryState, registerHistory } from '@lexical/history';
import { registerPlainText } from '@lexical/plain-text';
import { mergeRegister } from '@lexical/utils';
import { $createCommandChip, CommandChipNode } from './command-chip';
export { CommandChipNode } from './command-chip';
import type { ChatCommand } from '../../../lib/chatCommands';
import type { ChatReference, ChatDraftSessionReference } from '../../../lib/chatReferences';

export interface ReferenceDraft {
  text: string;
  references: ChatReference[];
  sessionReferences?: ChatDraftSessionReference[];
  command?: ChatCommand;
}

type SessionChip = Pick<ChatDraftSessionReference, 'sessionId' | 'snapshotHash' | 'label'>;
type SerializedChip = Spread<{ attachmentId: string; label: string; session?: SessionChip }, SerializedLexicalNode>;

export class ReferenceChipNode extends DecoratorNode<null> {
  __attachmentId: string;
  __label: string;
  __session: SessionChip | undefined;

  static override getType(): string { return 'reference-chip'; }
  static override clone(node: ReferenceChipNode): ReferenceChipNode {
    return new ReferenceChipNode(node.__attachmentId, node.__label, node.__session, node.__key);
  }
  static override importJSON(value: SerializedChip): ReferenceChipNode {
    return $createReferenceChip(value.attachmentId, value.label, value.session);
  }

  constructor(attachmentId = '', label = '', session?: SessionChip, key?: NodeKey) {
    super(key);
    this.__attachmentId = attachmentId;
    this.__label = label;
    this.__session = session;
  }

  override exportJSON(): SerializedChip {
    return { ...super.exportJSON(), attachmentId: this.__attachmentId, label: this.__label,
      ...(this.__session ? { session: this.__session } : {}) };
  }

  override createDOM(): HTMLElement {
    const element = document.createElement('span');
    element.setAttribute('data-composer-chip', this.__session ? 'session' : 'file');
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
  getSession(): SessionChip | undefined { return this.getLatest().__session; }
}

function $createReferenceChip(attachmentId: string, label: string, session?: SessionChip): ReferenceChipNode {
  return $applyNodeReplacement(new ReferenceChipNode(attachmentId, label, session));
}

/** Clipboard/persistence projection. Paragraph gaps count as one newline. */
export function $projectReferenceDraft(): ReferenceDraft {
  const result: ReferenceDraft = { text: '', references: [] };
  const visit = (node: LexicalNode) => {
    if (node instanceof CommandChipNode) {
      if (!result.text) result.command = node.getCommand();
      result.text += node.getTextContent();
    } else if (node instanceof ReferenceChipNode) {
      const start = result.text.length;
      result.text += node.getTextContent();
      const session = node.getSession();
      if (session) (result.sessionReferences ??= []).push({ start, end: result.text.length, ...session });
      else result.references.push({ start, end: result.text.length, attachmentId: node.getAttachmentId() });
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
  if (value.command) {
    paragraph.append($createCommandChip(value.command));
    offset = value.command.token.length;
  }
  const ordered = [...value.references, ...(value.sessionReferences ?? [])].sort((left, right) => left.start - right.start);
  for (const reference of ordered) {
    append(value.text.slice(offset, reference.start));
    const label = value.text.slice(reference.start, reference.end);
    paragraph.append('sessionId' in reference
      ? $createReferenceChip('', label, { sessionId: reference.sessionId, snapshotHash: reference.snapshotHash, label: reference.label })
      : $createReferenceChip(reference.attachmentId, label));
    offset = reference.end;
  }
  append(value.text.slice(offset));
  root.selectEnd();
}

export function sameReferenceDraft(left: ReferenceDraft, right: ReferenceDraft): boolean {
  const leftSessions = left.sessionReferences ?? [];
  const rightSessions = right.sessionReferences ?? [];
  return left.text === right.text && left.command?.id === right.command?.id && left.command?.token === right.command?.token
    && left.references.length === right.references.length
    && leftSessions.length === rightSessions.length
    && leftSessions.every(({ start, end, sessionId, snapshotHash, label }, index) => {
      const other = rightSessions[index];
      return start === other.start && end === other.end && sessionId === other.sessionId
        && snapshotHash === other.snapshotHash && label === other.label;
    })
    && left.references.every(({ start, end, attachmentId }, index) => {
      const other = right.references[index];
      return start === other.start && end === other.end && attachmentId === other.attachmentId;
    });
}

export function insertReferenceChip(editor: LexicalEditor, attachmentId: string, label: string,
  session?: SessionChip, savedSelection?: RangeSelection | null): void {
  editor.update(() => {
    if (savedSelection && $getNodeByKey(savedSelection.anchor.key) && $getNodeByKey(savedSelection.focus.key)) {
      $setSelection(savedSelection.clone());
    }
    let selection = $getSelection();
    if (!$isRangeSelection(selection)) selection = $getRoot().selectEnd();
    selection.insertNodes([$createReferenceChip(attachmentId, label, session), $createTextNode(' ')]);
  }, { discrete: true, tag: HISTORY_PUSH_TAG });
}

/** Replace an exact open slash query, or choose a leading command from the explicit menu. */
export function applyCommandChip(editor: LexicalEditor, command: ChatCommand, trigger?: string): boolean {
  let applied = false;
  editor.update(() => {
    const current = $projectReferenceDraft();
    if (trigger !== undefined && (current.text !== trigger || current.command ||
        current.references.length || current.sessionReferences?.length)) return;
    const replaced = trigger?.length ?? current.command?.token.length ?? 0;
    const rest = current.text.slice(replaced);
    const prefix = command.token + (/^\s/u.test(rest) ? '' : ' ');
    const delta = prefix.length - replaced;
    $restoreReferenceDraft({ text: prefix + rest, command,
      references: current.references.map(ref => ({ ...ref, start: ref.start + delta, end: ref.end + delta })),
      sessionReferences: current.sessionReferences?.map(ref => ({ ...ref, start: ref.start + delta, end: ref.end + delta })),
    });
    applied = true;
  }, { discrete: true, tag: HISTORY_PUSH_TAG });
  return applied;
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
    if (!(adjacent instanceof ReferenceChipNode) && !(adjacent instanceof CommandChipNode)) return false;
    event?.preventDefault();
    adjacent.remove();
    return true;
  };
  return mergeRegister(
    registerPlainText(editor),
    registerHistory(editor, createEmptyHistoryState(), 1000),
    editor.registerNodeTransform(RootNode, root => {
      const first = root.getFirstChild();
      const leading = $isElementNode(first) ? first.getFirstChild() : null;
      const flattenNonLeading = (node: LexicalNode) => {
        if (node instanceof CommandChipNode && node !== leading) node.replace($createTextNode(node.getTextContent()));
        else if ($isElementNode(node)) node.getChildren().forEach(flattenNonLeading);
      };
      root.getChildren().forEach(flattenNonLeading);
    }),
    editor.registerCommand(KEY_BACKSPACE_COMMAND, event => removeAdjacent(event, true), COMMAND_PRIORITY_HIGH),
    editor.registerCommand(KEY_DELETE_COMMAND, event => removeAdjacent(event, false), COMMAND_PRIORITY_HIGH),
  );
}
