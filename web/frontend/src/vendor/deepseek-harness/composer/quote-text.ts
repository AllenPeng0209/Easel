/** Copyright (c) 2026 DeepSeek. MIT License.
 * From ui-primitives/src/markdown/quote-text.ts and session/message-quotes/src/client/quote-text.ts
 * @ ce7073d51254c26cd63b22a60835b8f948deb447. License: integrations/deepseek-harness/LICENSE.
 */
export const MAX_ANSWER_QUOTE_LENGTH = 12_000;

/** Shared Markdown blockquote formatting for browser text. */

/**
 * Project selected visible text as an ordinary Markdown blockquote.
 * @param text - selected visible text without quote markers.
 * @returns one `> `-prefixed line per normalized input line.
 */
export function formatQuoteText(text: string): string {
  return text.replace(/\r\n?/gu, '\n').split('\n').map(line => `> ${line}`).join('\n')
}
/** Explicit blockquote recognition and decoding for quote presentation. */

/**
 * Remove one blockquote marker from each explicitly quoted line.
 * @param text - consecutive blockquote lines, with up to three leading spaces.
 * @returns excerpt text with original line endings normalized.
 */
export function quoteTextOf(text: string): string {
  return text.replace(/\r\n?/gu, '\n').replace(/^ {0,3}>[ \t]?/gmu, '')
}

/** One blockquote span in original UTF-16 text, excluding its trailing line break. */
export interface MessageQuoteRange {
  readonly start: number
  readonly end: number
}

/**
 * Recognize consecutive explicitly marked Markdown blockquote lines.
 * @param text - complete original message text.
 * @returns ordered nonblank quote spans; inline greater-than signs remain ordinary text.
 */
export function messageQuoteRanges(text: string): readonly MessageQuoteRange[] {
  const ranges: MessageQuoteRange[] = []
  for (const match of text.matchAll(/^ {0,3}>[^\r\n]*(?:\r?\n {0,3}>[^\r\n]*)*/gmu)) {
    if (quoteTextOf(match[0]).trim() !== '') ranges.push({ start: match.index, end: match.index + match[0].length })
  }
  return ranges
}
