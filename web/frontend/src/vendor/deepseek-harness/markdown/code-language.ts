/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Grammar aliases adapted from ui-primitives/src/markdown/highlight.ts
 * @ ce7073d51254c26cd63b22a60835b8f948deb447.
 */
export const MAX_HIGHLIGHT_LENGTH = 24_000;

type HighlightGrammar = 'typescript' | 'shellscript' | 'json' | 'python';

const aliases = new Map<string, HighlightGrammar>([
  ['js', 'typescript'], ['javascript', 'typescript'], ['ts', 'typescript'], ['typescript', 'typescript'],
  ['bash', 'shellscript'], ['sh', 'shellscript'], ['shell', 'shellscript'], ['shellscript', 'shellscript'],
  ['json', 'json'], ['python', 'python'], ['py', 'python'],
]);

export function highlightLanguage(hint: string | undefined): HighlightGrammar | undefined {
  return hint === undefined ? undefined : aliases.get(hint.toLowerCase());
}
