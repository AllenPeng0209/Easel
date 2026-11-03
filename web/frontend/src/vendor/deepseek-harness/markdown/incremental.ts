/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Two-block frontier and generation keys adapted from ui-primitives/src/markdown/
 * {incremental.ts,MarkdownText.tsx} @ ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel keeps its existing Marked grammar and injects its sanitization policy.
 */
import { Lexer, marked, type Links, type Token } from 'marked';

export interface MarkdownBlock {
  readonly key: string;
  readonly html: string;
}

const OPTIONS = { breaks: true, gfm: true };
const UNSTABLE_TAIL_BLOCKS = 2;

function lex(text: string, definitions: Links) {
  const lexer = new Lexer(OPTIONS);
  // Earlier definitions still resolve links in the tail. Keep Marked's null
  // prototype dictionary and first-definition-wins behavior.
  Object.assign(lexer.tokens.links, definitions);
  return lexer.lex(text);
}

function positionBlocks(tokens: Token[], text: string, base: number) {
  // Inline HTML may open a tag in one top-level token and close it in another.
  // Keep its suffix intact so DOMPurify observes the same parse context.
  let hasHtml = false;
  marked.walkTokens(tokens, token => { if (token.type === 'html') hasHtml = true; });
  if (hasHtml) return undefined;
  const blocks: { token: Token; start: number; end: number }[] = [];
  let offset = 0;
  for (const token of tokens) {
    if (!token.raw || !text.startsWith(token.raw, offset)) return undefined;
    const end = offset + token.raw.length;
    if (token.type !== 'space' && token.type !== 'def') blocks.push({ token, start: base + offset, end: base + end });
    offset = end;
  }
  return offset === text.length ? blocks : undefined;
}

/** Append-only block renderer. Non-append input resets the frozen prefix. */
export class IncrementalMarkdownRenderer {
  private previous = '';
  private settled = false;
  private generation = 0;
  private tailStart = 0;
  private frozen: MarkdownBlock[] = [];
  private definitions: Links = Object.create(null);
  private current: MarkdownBlock[] = [];
  private readonly sanitize: (rawHtml: string) => string;

  constructor({ sanitize }: { sanitize: (rawHtml: string) => string }) {
    this.sanitize = sanitize;
  }

  update(source: string, settled = false): readonly MarkdownBlock[] {
    // Marked also folds CRLF and lone CR. Normalize before computing offsets.
    const text = source.replace(/\r\n?/g, '\n');
    if (text === this.previous && settled === this.settled) return this.current;
    const completing = settled && !this.settled;
    if ((!completing && !text.startsWith(this.previous)) || (this.settled && !settled)) {
      this.generation += 1;
      this.tailStart = 0;
      this.frozen = [];
      this.definitions = Object.create(null);
    }

    const base = settled ? 0 : this.tailStart;
    const tail = text.slice(base);
    const tokens = lex(tail, settled ? Object.create(null) : this.definitions);
    const positioned = positionBlocks(tokens, tail, base);
    const previousBlocks = new Map(this.current.map(block => [block.key, block]));
    const rendered = (key: string, raw: string): MarkdownBlock => {
      const html = this.sanitize(raw);
      const previous = previousBlocks.get(key);
      return previous?.html === html ? previous : { key, html };
    };

    if (!positioned) {
      // Never split raw HTML containers or guess at lexer offsets. The
      // unresolved suffix still uses one normal sanitized Marked parse.
      this.current = [
        ...(settled ? [] : this.frozen),
        rendered(`${this.generation}:tail:${base}`, marked.parser(tokens, OPTIONS)),
      ];
    } else {
      const blocks = positioned.map(({ token, start }) =>
        rendered(`${this.generation}:${start}`, marked.parser([token], OPTIONS)));
      if (!settled) {
        const firstUnstable = Math.max(0, positioned.length - UNSTABLE_TAIL_BLOCKS);
        if (firstUnstable > 0) {
          this.tailStart = positioned[firstUnstable - 1].end;
          const prefix = tail.slice(0, this.tailStart - base);
          if (prefix.includes(']:')) this.definitions = lex(prefix, this.definitions).links;
          this.frozen = this.frozen.concat(blocks.slice(0, firstUnstable));
        }
        this.current = [...this.frozen, ...blocks.slice(firstUnstable)];
      } else {
        this.current = blocks;
      }
    }
    this.previous = text;
    this.settled = settled;
    return this.current;
  }
}
