import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from 'react';
import { $getSelection, $isRangeSelection, type RangeSelection, CLEAR_HISTORY_COMMAND, COMMAND_PRIORITY_HIGH, createEditor, KEY_DOWN_COMMAND, KEY_ENTER_COMMAND, PASTE_COMMAND } from 'lexical';
import { fetchChatCommands, searchChatAttachments, selectChatAttachment, type UploadedFile } from '../lib/api';
import type { ChatCommand } from '../lib/chatCommands';
import { useComposerCommandMenu } from './ComposerCommandMenu';
import { isComposerFileReceipt, useComposerFileMenu } from './ComposerFileMenu';
import {
  $fileReferenceTrigger, captureFileReferenceSelection, sameFileReferenceTrigger, type FileReferenceTrigger,
} from './fileReferenceTrigger';
import { attachmentReferenceLabel, sessionReferenceLabel, type ChatDraftSessionReference, type ChatReference } from '../lib/chatReferences';
import {
  $projectReferenceDraft, $restoreReferenceDraft, applyCommandChip, CommandChipNode, insertAnswerQuote, insertReferenceChip, QuoteNode, ReferenceChipNode,
  registerReferenceEditing, detachReferenceChips, sameReferenceDraft,
} from '../vendor/deepseek-harness/composer/reference-editor';
import './StructuredComposerEditor.css';

export interface StructuredEditorHandle {
  focus: () => void;
  insertAnswerQuote: (text: string) => boolean;
  rememberSelection: () => void;
  openCommandMenu: () => void;
  insertSessionReference: (reference: Pick<ChatDraftSessionReference, 'sessionId' | 'label' | 'snapshotHash'>) => void;
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
  const savedSelection = useRef<RangeSelection | null>(null);
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

  const menu = useComposerCommandMenu({ value, command, disabled, load: fetchChatCommands,
    onPick: (selected, trigger) => {
      // The keyboard command runs inside a Lexical update. Apply the selection
      // after it closes, so the pick cannot also reach the Enter-to-send handler.
      queueMicrotask(() => {
        if (!current.current.disabled && editor.getRootElement() && applyCommandChip(editor, selected, trigger)) editor.focus();
      });
    } });
  const currentMenu = useRef(menu);
  currentMenu.current = menu;

  const [fileTrigger, setFileTrigger] = useState<FileReferenceTrigger>();
  const loadFiles = useCallback((query: string, signal: AbortSignal) =>
    fileReferenceSessionId ? searchChatAttachments(fileReferenceSessionId, query, signal)
      : Promise.resolve({ files: [], candidates: [] }), [fileReferenceSessionId]);
  const fileMenu = useComposerFileMenu({ trigger: fileTrigger, disabled: disabled || menu.open || !fileReferenceSessionId,
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
      currentMenu.current.keyDown({ key: 'Escape', preventDefault() {} });
      currentFileMenu.current.close();
      const inserted = insertAnswerQuote(editor, text);
      if (inserted) editor.focus();
      return inserted;
    },
    openCommandMenu() {
      if (current.current.disabled) return;
      currentFileMenu.current.close();
      currentMenu.current.openExplicitly();
      editor.focus();
    },
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
      return currentFileMenu.current.keyDown(event) || currentMenu.current.keyDown(event);
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

  useEffect(() => {
    if (!disabled && command && menu.commands &&
        !menu.commands.some(row => row.id === command.id && row.token === command.token)) {
      current.current.onChange(value, references, sessionReferences);
    }
  }, [command, disabled, menu.commands, references, sessionReferences, value]);

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
      aria-controls={fileMenu.open ? fileMenu.menuId : menu.open ? menu.menuId : undefined}
      aria-activedescendant={fileMenu.activeId ?? menu.activeId}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={() => { composing.current = false; compositionEndedAt.current = Date.now(); }}
      onBlur={() => { composing.current = false; }} />
    {!value && <span className="structured-composer-placeholder" aria-hidden="true">{placeholder}</span>}
    {menu.content}
    {fileMenu.content}
  </div>;
}
