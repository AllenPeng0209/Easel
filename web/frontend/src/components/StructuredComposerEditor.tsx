import { useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from 'react';
import { $getSelection, $isRangeSelection, type RangeSelection, CLEAR_HISTORY_COMMAND, COMMAND_PRIORITY_HIGH, createEditor, KEY_ENTER_COMMAND, PASTE_COMMAND } from 'lexical';
import { sessionReferenceLabel, type ChatDraftSessionReference, type ChatReference } from '../lib/chatReferences';
import {
  $projectReferenceDraft, $restoreReferenceDraft, insertReferenceChip, ReferenceChipNode,
  registerReferenceEditing, detachReferenceChips, sameReferenceDraft,
} from '../vendor/deepseek-harness/composer/reference-editor';
import './StructuredComposerEditor.css';

export interface StructuredEditorHandle {
  focus: () => void;
  rememberSelection: () => void;
  insertSessionReference: (reference: Pick<ChatDraftSessionReference, 'sessionId' | 'label' | 'snapshotHash'>) => void;
  insertReference: (attachmentId: string, label: string) => void;
  removeReference: (attachmentId: string) => void;
}

interface EditorProps {
  ref?: Ref<StructuredEditorHandle>;
  value: string;
  references: ChatReference[];
  sessionReferences?: ChatDraftSessionReference[];
  placeholder: string;
  disabled: boolean;
  onChange: (text: string, references: ChatReference[], sessionReferences?: ChatDraftSessionReference[]) => void;
  onSubmit: () => void;
  onPasteFiles: (files: FileList) => void;
}

/** React host for the pinned Harness plain-text editor and its atomic file references. */
export default function StructuredComposerEditor({ ref, value, references, sessionReferences, placeholder, disabled,
  onChange, onSubmit, onPasteFiles }: EditorProps) {
  const root = useRef<HTMLDivElement>(null);
  const projection = useRef({ text: value, references, sessionReferences });
  const savedSelection = useRef<RangeSelection | null>(null);
  const current = useRef({ onChange, onSubmit, onPasteFiles, disabled });
  current.current = { onChange, onSubmit, onPasteFiles, disabled };
  const composing = useRef(false);
  const compositionEndedAt = useRef(0);
  const [editor] = useState(() => {
    const instance = createEditor({ namespace: 'easel-composer', nodes: [ReferenceChipNode],
      onError(error) { throw error; } });
    instance.update(() => $restoreReferenceDraft({ text: value, references, sessionReferences }), { discrete: true });
    return instance;
  });

  useImperativeHandle(ref, () => ({
    focus: () => editor.focus(),
    rememberSelection() {
      savedSelection.current = editor.getEditorState().read(() => {
        const selection = $getSelection();
        return $isRangeSelection(selection) ? selection.clone() : null;
      });
    },
    insertSessionReference(reference) {
      if (current.current.disabled) return;
      insertReferenceChip(editor, '', sessionReferenceLabel(reference.label), reference, savedSelection.current);
      savedSelection.current = null;
      requestAnimationFrame(() => editor.focus());
    },
    insertReference(attachmentId, label) {
      if (current.current.disabled) return;
      insertReferenceChip(editor, attachmentId, label);
      editor.focus();
    },
    removeReference(attachmentId) {
      detachReferenceChips(editor, attachmentId);
      // A removed attachment can no longer be referenced by an undo snapshot.
      editor.dispatchCommand(CLEAR_HISTORY_COMMAND, undefined);
    },
  }), [editor]);

  useLayoutEffect(() => {
    const dispose = registerReferenceEditing(editor);
    const unregisterChange = editor.registerUpdateListener(({ editorState }) => {
      const updated = editorState.read($projectReferenceDraft);
      if (sameReferenceDraft(updated, projection.current)) return;
      projection.current = { ...updated, sessionReferences: updated.sessionReferences };
      current.current.onChange(updated.text, updated.references, updated.sessionReferences);
    });
    const unregisterEnter = editor.registerCommand(KEY_ENTER_COMMAND, event => {
      if (!event || composing.current || editor.isComposing() || event.isComposing || event.keyCode === 229
        || Date.now() - compositionEndedAt.current < 60
        || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return false;
      event.preventDefault();
      if (!current.current.disabled) current.current.onSubmit();
      return true;
    }, COMMAND_PRIORITY_HIGH);
    const unregisterPaste = editor.registerCommand(PASTE_COMMAND, event => {
      if (!('clipboardData' in event) || !event.clipboardData?.files.length) return false;
      event.preventDefault();
      if (!current.current.disabled) current.current.onPasteFiles(event.clipboardData.files);
      return true;
    }, COMMAND_PRIORITY_HIGH);
    editor.setRootElement(root.current);
    return () => {
      unregisterPaste();
      unregisterEnter();
      unregisterChange();
      dispose();
      editor.setRootElement(null);
    };
  }, [editor]);

  useLayoutEffect(() => { editor.setEditable(!disabled); }, [editor, disabled]);

  useLayoutEffect(() => {
    const next = { text: value, references, sessionReferences };
    if (sameReferenceDraft(next, projection.current)) return;
    projection.current = next;
    editor.update(() => $restoreReferenceDraft(next), { discrete: true });
    editor.dispatchCommand(CLEAR_HISTORY_COMMAND, undefined);
  }, [editor, value, references, sessionReferences, disabled]);

  return <div className="structured-composer-editor" data-empty={!value || undefined}>
    <div ref={root} className="chat-input structured-composer-input" contentEditable={!disabled}
      suppressContentEditableWarning role="textbox" aria-label="给 AI 发送消息" aria-multiline="true"
      aria-disabled={disabled} aria-placeholder={placeholder} spellCheck={false}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={() => { composing.current = false; compositionEndedAt.current = Date.now(); }}
      onBlur={() => { composing.current = false; }} />
    {!value && <span className="structured-composer-placeholder" aria-hidden="true">{placeholder}</span>}
  </div>;
}
