/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Adapted from ui-primitives/src/markdown/highlight.ts
 * @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel loads this whole module only for settled, bounded Markdown fences.
 */
import { createCssVariablesTheme, createHighlighterCoreSync, type HighlighterCore } from 'shiki/core';
import { createJavaScriptRegexEngine, defaultJavaScriptRegexConstructor } from 'shiki/engine/javascript';
import langTs from '@shikijs/langs/typescript';
import langBash from '@shikijs/langs/shellscript';
import langJson from '@shikijs/langs/json';
import langPython from '@shikijs/langs/python';
import { highlightLanguage, MAX_HIGHLIGHT_LENGTH } from './code-language';

export type HighlightLine = { text: string; color: string; fontStyle?: number }[];

const MAX_LINE_LENGTH = 1_000;
const MAX_LINES = 400;
const theme = createCssVariablesTheme({ name: 'css-variables', variablePrefix: '--shiki-', fontStyle: true });
let singleton: HighlighterCore | undefined;

function highlighter(): HighlighterCore {
  singleton ??= createHighlighterCoreSync({
    themes: [theme],
    langs: [langTs, langBash, langJson, langPython],
    engine: createJavaScriptRegexEngine({
      forgiving: true,
      regexConstructor: pattern => defaultJavaScriptRegexConstructor(pattern, {
        lazyCompileLength: Number.POSITIVE_INFINITY,
      }),
    }),
  });
  return singleton;
}

/** Tokenize into literal-text runs. Unavailable or incomplete output leaves the existing code intact. */
export function highlightCode(code: string, hint: string | undefined): HighlightLine[] | undefined {
  const lang = highlightLanguage(hint);
  if (!lang || !code || code.length > MAX_HIGHLIGHT_LENGTH) return undefined;
  const lines = code.split('\n');
  if (lines.length > MAX_LINES || lines.some(line => line.length >= MAX_LINE_LENGTH)) return undefined;
  try {
    const { tokens } = highlighter().codeToTokens(code, {
      lang, theme: 'css-variables', tokenizeMaxLineLength: MAX_LINE_LENGTH, tokenizeTimeLimit: 100,
    });
    if (tokens.length !== lines.length ||
        tokens.some((line, index) => line.map(token => token.content).join('') !== lines[index])) return undefined;
    return tokens.map(line => line.map(token => ({
      text: token.content, color: token.color ?? 'var(--shiki-foreground)', fontStyle: token.fontStyle,
    })));
  } catch {
    return undefined;
  }
}
