import { useCallback, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from 'react';
import { CLEAR_HISTORY_COMMAND, COMMAND_PRIORITY_HIGH, createEditor, KEY_DOWN_COMMAND, KEY_ENTER_COMMAND, PASTE_COMMAND } from 'lexical';
import { searchChatAttachments, selectChatAttachment, type UploadedFile } from '../lib/api';
import type { ChatCommand } from '../lib/chatCommands';
import { isComposerFileReceipt, useComposerFileMenu } from './ComposerFileMenu';
import {
  $fileReferenceTrigger, captureFileReferenceSelection, sameFileReferenceTrigger, type FileReferenceTrigger,
} from './fileReferenceTrigger';
import { attachmentReferenceLabel, type ChatDraftSessionReference, type ChatReference } from '../lib/chatReferences';
import {
  $projectReferenceDraft, $restoreReferenceDraft, CommandChipNode, insertAnswerQuote, insertReferenceChip, QuoteNode, ReferenceChipNode,
  registerReferenceEditing, detachReferenceChips, sameReferenceDraft,
} from '../vendor/deepseek-harness/composer/reference-editor';
import './StructuredComposerEditor.css';

export interface StructuredEditorHandle {
  focus: () => void;
  insertAnswerQuote: (text: string) => boolean;
  insertReference: (attachmentId: string, label: string) => void;
  removeReference: (attachmentId: string) => void;
}

interface EditorProps {
  ref?: Ref<StructuredEditorHandle>;
  value: string;
  references: ChatReference[];
  sessionReferences?: ChatDraftSessionReference[];
  command?: ChatCommand;
  placeholder: string;
  disabled: boolean;
  fileReferenceSessionId?: string;
  onAdoptFileReference?: (file: UploadedFile) => boolean;
  onFileSelectionPending?: (pending: boolean) => void;
  onChange: (text: string, references: ChatReference[], sessionReferences?: ChatDraftSessionReference[], command?: ChatCommand) => void;
  onSubmit: () => void;
  onPasteFiles: (files: FileList) => void;
}

/** React host for the pinned Harness plain-text editor and its atomic file references. */
export default function StructuredComposerEditor({ ref, value, references, sessionReferences, command, placeholder, disabled,
  onChange, onSubmit, onPasteFiles, fileReferenceSessionId, onAdoptFileReference, onFileSelectionPending }: EditorProps) {
  const root = useRef<HTMLDivElement>(null);
  const projection = useRef({ text: value, references, sessionReferences, command });
  const current = useRef({ onChange, onSubmit, onPasteFiles, onAdoptFileReference, disabled });
  current.current = { onChange, onSubmit, onPasteFiles, onAdoptFileReference, disabled };
  const composing = useRef(false);
  const compositionEndedAt = useRef(0);
  const [editor] = useState(() => {
    const instance = createEditor({ namespace: 'easel-composer', nodes: [ReferenceChipNode, CommandChipNode, QuoteNode],
      onError(error) { throw error; } });
    instance.update(() => $restoreReferenceDraft({ text: value, references, sessionReferences, command }), { discrete: true });
    return instance;
  });

  const [fileTrigger, setFileTrigger] = useState<FileReferenceTrigger>();
  const loadFiles = useCallback((query: string, signal: AbortSignal) =>
    fileReferenceSessionId ? searchChatAttachments(fileReferenceSessionId, query, signal)
      : Promise.resolve({ files: [], candidates: [] }), [fileReferenceSessionId]);
  const fileMenu = useComposerFileMenu({ trigger: fileTrigger, disabled: disabled || !fileReferenceSessionId,
    load: loadFiles, onSelecting: pending => onFileSelectionPending?.(pending),
    onPick: async (option, trigger, signal) => {
      if (!fileReferenceSessionId) return false;
      // Run outside the Lexical keyboard transaction even for an already adopted receipt.
      const receipt = await (option.kind === 'attachment' ? Promise.resolve(option.file)
        : selectChatAttachment(fileReferenceSessionId, option.file.id, signal).then(result => result.file));
      if (signal.aborted || current.current.disabled || !editor.getRootElement() || !isComposerFileReceipt(receipt)) return false;
      const span = captureFileReferenceSelection(editor, trigger);
      if (!span || !current.current.onAdoptFileReference?.(receipt)) return false;
      insertReferenceChip(editor, receipt.id, attachmentReferenceLabel(receipt), undefined, span);
      editor.focus();
      return true;
    },
  });
  const currentFileMenu = useRef(fileMenu);
  currentFileMenu.current = fileMenu;


  useImperativeHandle(ref, () => ({
    focus: () => editor.focus(),
    insertAnswerQuote(text) {
      if (current.current.disabled || composing.current) return false;
      currentFileMenu.current.close();
      const inserted = insertAnswerQuote(editor, text);
      if (inserted) editor.focus();
      return inserted;
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
    const fromComposition = (event: KeyboardEvent) => composing.current || editor.isComposing() ||
      event.isComposing || event.keyCode === 229 || Date.now() - compositionEndedAt.current < 60;
    const dispose = registerReferenceEditing(editor);
    const unregisterChange = editor.registerUpdateListener(({ editorState }) => {
      const [updated, trigger] = editorState.read(() => [$projectReferenceDraft(), $fileReferenceTrigger()] as const);
      setFileTrigger(previous => sameFileReferenceTrigger(previous, trigger) ? previous : trigger);
      if (sameReferenceDraft(updated, projection.current)) return;
      projection.current = { ...updated, sessionReferences: updated.sessionReferences, command: updated.command };
      current.current.onChange(updated.text, updated.references, updated.sessionReferences, updated.command);
    });
    const unregisterMenu = editor.registerCommand(KEY_DOWN_COMMAND, event => {
      if (fromComposition(event) || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return false;
      return currentFileMenu.current.keyDown(event);
    }, COMMAND_PRIORITY_HIGH);
    const unregisterEnter = editor.registerCommand(KEY_ENTER_COMMAND, event => {
      if (!event || event.defaultPrevented || fromComposition(event)
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
      unregisterMenu();
      unregisterChange();
      dispose();
      editor.setRootElement(null);
    };
  }, [editor]);

  useLayoutEffect(() => { editor.setEditable(!disabled); }, [editor, disabled]);

  useLayoutEffect(() => {
    const next = { text: value, references, sessionReferences, command };
    if (sameReferenceDraft(next, projection.current)) return;
    projection.current = next;
    editor.update(() => $restoreReferenceDraft(next), { discrete: true });
    editor.dispatchCommand(CLEAR_HISTORY_COMMAND, undefined);
  }, [editor, value, references, sessionReferences, command, disabled]);

  return <div className="structured-composer-editor" data-empty={!value || undefined}>
    <div ref={root} className="chat-input structured-composer-input" contentEditable={!disabled}
      suppressContentEditableWarning role="textbox" aria-label="给 AI 发送消息" aria-multiline="true"
      aria-disabled={disabled} aria-placeholder={placeholder} spellCheck={false}
      aria-controls={fileMenu.open ? fileMenu.menuId : undefined}
      aria-activedescendant={fileMenu.activeId}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={() => { composing.current = false; compositionEndedAt.current = Date.now(); }}
      onBlur={() => { composing.current = false; }} />
    {!value && <span className="structured-composer-placeholder" aria-hidden="true">{placeholder}</span>}
    {fileMenu.content}
  </div>;
}
