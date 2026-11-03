import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const { $getRoot, $isRangeSelection, $getSelection, $setSelection, UNDO_COMMAND, REDO_COMMAND, KEY_BACKSPACE_COMMAND, KEY_DELETE_COMMAND, createEditor } = require('lexical');

function editorFixture() {
  const runtime = componentModule('vendor/deepseek-harness/composer/reference-editor.ts');
  const editor = createEditor({ nodes: [runtime.ReferenceChipNode], onError(error) { throw error; } });
  const update = callback => editor.update(callback, { discrete: true });
  const snapshot = () => editor.getEditorState().read(runtime.$projectReferenceDraft);
  const dispose = runtime.registerReferenceEditing(editor);
  return { editor, runtime, update, snapshot, dispose };
}

test('Lexical projection preserves ordered UTF-16 spans, multiple references, and line breaks', () => {
  const { runtime, update, snapshot, dispose } = editorFixture();
  const value = { text: '🎬 @draft.md\n结合 @clip.mp4', references: [
    { start: 3, end: 12, attachmentId: 'draft' }, { start: 16, end: 25, attachmentId: 'clip' },
  ] };
  try {
    update(() => runtime.$restoreReferenceDraft(value));
    assert.deepEqual(JSON.parse(JSON.stringify(snapshot())), value);
  } finally { dispose(); }
});

test('an inserted file is atomic and undo/redo retains its exact attachment ID', async () => {
  const { editor, runtime, update, snapshot, dispose } = editorFixture();
  const flush = () => new Promise(resolve => setImmediate(resolve));
  try {
    update(() => runtime.$restoreReferenceDraft({ text: '前后', references: [] }));
    update(() => $getRoot().getFirstDescendant().select(1, 1));
    runtime.insertReferenceChip(editor, 'receipt-1', '@素材.pdf');
    assert.deepEqual(JSON.parse(JSON.stringify(snapshot())), {
      text: '前@素材.pdf 后', references: [{ start: 1, end: 8, attachmentId: 'receipt-1' }],
    });
    editor.dispatchCommand(UNDO_COMMAND);
    await flush();
    assert.equal(snapshot().text, '前后');
    editor.dispatchCommand(REDO_COMMAND);
    await flush();
    assert.equal(snapshot().references[0].attachmentId, 'receipt-1');
    update(() => {
      const paragraph = $getRoot().getFirstChild();
      const suffix = paragraph.getLastChild();
      suffix.select(0, 0);
      assert.ok($isRangeSelection($getSelection()));
      assert.ok(editor.dispatchCommand(KEY_BACKSPACE_COMMAND, { preventDefault() {} }));
    });
    assert.equal(snapshot().references.length, 0);
    assert.equal(snapshot().text, '前 后');
  } finally { dispose(); }
});


test('forward Delete removes one chip and detaching a receipt removes all its occurrences only', () => {
  const { editor, runtime, update, snapshot, dispose } = editorFixture();
  const text = 'A @x.md B @y.md C @x.md';
  try {
    update(() => runtime.$restoreReferenceDraft({ text, references: [
      { start: 2, end: 7, attachmentId: 'x' },
      { start: 10, end: 15, attachmentId: 'y' },
      { start: 18, end: 23, attachmentId: 'x' },
    ] }));
    update(() => {
      const prefix = $getRoot().getFirstDescendant();
      prefix.select(prefix.getTextContentSize(), prefix.getTextContentSize());
      assert.ok(editor.dispatchCommand(KEY_DELETE_COMMAND, { preventDefault() {} }));
    });
    assert.deepEqual(Array.from(snapshot().references, reference => reference.attachmentId), ['y', 'x']);
    runtime.detachReferenceChips(editor, 'x');
    assert.equal(snapshot().text, 'A  B @y.md C @x.md');
    assert.deepEqual(Array.from(snapshot().references, reference => reference.attachmentId), ['y']);
  } finally { dispose(); }
});


test('session and attachment chips project independently and insert at the caret saved before modal focus', async () => {
  const { editor, runtime, update, snapshot, dispose } = editorFixture();
  const flush = () => new Promise(resolve => setImmediate(resolve));
  const selected = { sessionId: 'source-session', label: '旧计划', snapshotHash: 'a'.repeat(64) };
  try {
    update(() => runtime.$restoreReferenceDraft({
      text: 'AB @x.md', references: [{ start: 3, end: 8, attachmentId: 'x' }],
    }));
    let caret;
    update(() => {
      $getRoot().getFirstDescendant().select(1, 1);
      caret = $getSelection().clone();
      $setSelection(null);
    });
    runtime.insertReferenceChip(editor, '', '@旧计划', selected, caret);
    const inserted = snapshot();
    assert.equal(inserted.text, 'A@旧计划 B @x.md');
    assert.deepEqual(JSON.parse(JSON.stringify(inserted.references)), [{ start: 8, end: 13, attachmentId: 'x' }]);
    assert.deepEqual(JSON.parse(JSON.stringify(inserted.sessionReferences)), [{ start: 1, end: 5, ...selected }]);
    editor.dispatchCommand(UNDO_COMMAND);
    await flush();
    assert.equal(snapshot().sessionReferences, undefined);
    editor.dispatchCommand(REDO_COMMAND);
    await flush();
    assert.equal(snapshot().sessionReferences[0].snapshotHash, selected.snapshotHash);
    update(() => runtime.$restoreReferenceDraft(inserted));
    assert.deepEqual(JSON.parse(JSON.stringify(snapshot())), JSON.parse(JSON.stringify(inserted)));
  } finally { dispose(); }
});
