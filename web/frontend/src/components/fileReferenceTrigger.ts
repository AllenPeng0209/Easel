/**
 * Caret-scoped @ token selection adapted from DeepSeek Harness's
 * ui-reference candidates and ui-conversation input editor span-map @ ce7073d (MIT).
 */
import { $getSelection, $isRangeSelection, $isTextNode, type LexicalEditor, type NodeKey, type RangeSelection } from 'lexical';

export interface FileReferenceTrigger {
  key: NodeKey;
  start: number;
  end: number;
  raw: string;
  query: string;
}

export function parseFileReferenceToken(text: string): { raw: string; query: string } | undefined {
  const token = /(?:^|[\s([{（「])(@(?:"([^"\r\n]{0,256})"?|([^@\s"<>]{0,256})))$/u.exec(text);
  return token ? { raw: token[1], query: token[2] ?? token[3] ?? '' } : undefined;
}

export function $fileReferenceTrigger(): FileReferenceTrigger | undefined {
  const selection = $getSelection();
  if (!$isRangeSelection(selection) || !selection.isCollapsed() || selection.anchor.type !== 'text') return undefined;
  const node = selection.anchor.getNode();
  if (!$isTextNode(node)) return undefined;
  const end = selection.anchor.offset;
  const token = parseFileReferenceToken(node.getTextContent().slice(0, end));
  return token ? { ...token, key: node.getKey(), start: end - token.raw.length, end } : undefined;
}

export function sameFileReferenceTrigger(left?: FileReferenceTrigger, right?: FileReferenceTrigger): boolean {
  return left?.key === right?.key && left?.start === right?.start && left?.end === right?.end && left?.raw === right?.raw;
}

/** Capture the exact current token selection before transferring ownership of the receipt. */
export function captureFileReferenceSelection(editor: LexicalEditor, trigger: FileReferenceTrigger): RangeSelection | undefined {
  return editor.getEditorState().read(() => {
    if (!sameFileReferenceTrigger($fileReferenceTrigger(), trigger)) return undefined;
    const selection = $getSelection();
    if (!$isRangeSelection(selection)) return undefined;
    const replacement = selection.clone();
    replacement.anchor.set(trigger.key, trigger.start, 'text');
    return replacement;
  });
}
