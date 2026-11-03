import assert from 'node:assert/strict';
import { test } from 'node:test';
import { componentModule } from './agentMessagesHarness.mjs';

const modulePath = 'vendor/deepseek-harness/markdown/highlight.ts';
const languagePath = 'vendor/deepseek-harness/markdown/code-language.ts';

test('required grammars produce themed tokens without changing exact text', () => {
  const { highlightCode } = componentModule(modulePath);
  const fixtures = [
    ['json', '{"title":"<img src=x>","ok":true}\n'],
    ['JS', 'const hello = "🧪";\n\n  console.log(hello)\n'],
    ['ts', 'const ready: boolean = true;'],
    ['python', 'def answer():\n    return 42\n'],
    ['bash', 'printf "%s" "$HOME"\n'],
  ];
  for (const [language, source] of fixtures) {
    const lines = highlightCode(source, language);
    assert.ok(lines, language);
    assert.equal(lines.map(line => line.map(run => run.text).join('')).join('\n'), source, language);
    const colors = new Set(lines.flatMap(line => line.map(run => run.color)));
    assert.ok(colors.size > 1, language);
    assert.ok([...colors].every(color => /^var\(--shiki-[a-z-]+\)$/.test(color)), language);
  }
});

test('explicit grammar allowlist and source budgets fall back without token output', () => {
  const { highlightLanguage, MAX_HIGHLIGHT_LENGTH } = componentModule(languagePath);
  const { highlightCode } = componentModule(modulePath);
  assert.equal(highlightLanguage('sh'), 'shellscript');
  assert.equal(highlightLanguage('Py'), 'python');
  for (const hint of [undefined, 'html', 'toString', 'javascript alert']) {
    assert.equal(highlightLanguage(hint), undefined);
    assert.equal(highlightCode('alert(1)', hint), undefined);
  }
  assert.equal(highlightCode('x'.repeat(1_000), 'ts'), undefined);
  assert.equal(highlightCode('const x = true;\n'.repeat(MAX_HIGHLIGHT_LENGTH), 'ts'), undefined);
  assert.equal(highlightCode('', 'json'), undefined);
});
