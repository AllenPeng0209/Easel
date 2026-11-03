import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const require = createRequire(new URL('../../web/frontend/package.json', import.meta.url));
const { $getRoot, createEditor } = require('lexical');

test('file trigger uses the current caret, preserving path fragments and quoted spaces', () => {
  const { parseFileReferenceToken, $fileReferenceTrigger, captureFileReferenceSelection } = componentModule('components/fileReferenceTrigger.ts');
  assert.equal(parseFileReferenceToken('看一下 @report/一').query, 'report/一');
  assert.equal(parseFileReferenceToken('看一下 @"long name').query, 'long name');
  assert.equal(parseFileReferenceToken('user@example.test'), undefined);
  assert.equal(parseFileReferenceToken('结束 @done 已有普通文本'), undefined);
  const { $projectReferenceDraft, $restoreReferenceDraft, insertReferenceChip, ReferenceChipNode, CommandChipNode } = componentModule('vendor/deepseek-harness/composer/reference-editor.ts');
  const editor = createEditor({ nodes: [ReferenceChipNode, CommandChipNode] });
  let trigger;
  editor.update(() => {
    $restoreReferenceDraft({ text: '甲 @foo 尾段', references: [] });
    $getRoot().getFirstDescendant().select(6, 6);
    trigger = $fileReferenceTrigger();
  }, { discrete: true });
  assert.equal(trigger.query, 'foo');
  assert.equal(trigger.raw, '@foo');
  assert.equal(trigger.start, 2);
  assert.equal(trigger.end, 6);
  const span = captureFileReferenceSelection(editor, trigger);
  assert.ok(span);
  insertReferenceChip(editor, 'receipt', '@foo.png', undefined, span);
  const draft = editor.getEditorState().read($projectReferenceDraft);
  assert.equal(draft.text, '甲 @foo.png  尾段');
  assert.deepEqual(JSON.parse(JSON.stringify(draft.references)), [{ start: 2, end: 10, attachmentId: 'receipt' }]);
  assert.equal(captureFileReferenceSelection(editor, trigger), undefined);
});
