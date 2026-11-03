/**
 * Stable keyed Markdown blocks adapted from ui-primitives/src/markdown/MarkdownText.tsx
 * @ ce7073d (MIT). HTML has already passed the answer sanitizer and link policy.
 */
import { memo } from 'react';
import css from './AnswerQuoteSelection.module.css';

export default memo(function AnswerMarkdownBlock({ html }: { html: string }) {
  return <div className={css.markdownBlock} data-answer-markdown-block dangerouslySetInnerHTML={{ __html: html }} />;
});
