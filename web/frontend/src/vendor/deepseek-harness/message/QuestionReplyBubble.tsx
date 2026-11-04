/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Source: packages/client/ui-user-questions/src/client/QuestionReplyView.tsx
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: standalone props, native title tooltip and existing icon imports.
 * See integrations/deepseek-harness/LICENSE.
 */
import { UserMessageBubble } from './UserMessageBubble';
import { useClipboardCopy } from './use-clipboard-copy';
import { IconCheck, IconCopy } from '../../../components/icons';
import { replyAnswerValues, replyClipboardText, type QuestionReplyData, type QuestionReplyTranslator } from './question-reply';
import css from './QuestionReplyView.module.css';
/**
 * Render one answered batch with questions above their corresponding answers.
 * @param props - Reply data and the question locale translator.
 * @returns The right-aligned, fully visible question-and-answer bubble.
 */
export function QuestionReplyBubble({ data, t }: { data: QuestionReplyData; t: QuestionReplyTranslator }) {
  const copyText = replyClipboardText(data, t)
  const { copied, onCopy } = useClipboardCopy(copyText)
  return (
    <div className={css.row} data-question-reply={data.callId} data-reply-outcome={data.outcome}
      role="group" aria-label={t('reply.label')}>
      <div className={css.bubbleWidth}>
        <UserMessageBubble className={css.bubble}>
          {data.questions.length === 0
            ? <p className={css.text}>{data.text}</p>
            : <dl className={css.details}>
              {data.questions.map((question) => {
                const values = replyAnswerValues(data, question.id)
                return <div key={question.id}>
                  <dt className={css.question}>
                    {question.header && question.header !== question.question && <span className={css.header}>{question.header}</span>}
                    <span>{question.question}</span>
                  </dt>
                  <dd className={css.answer}>{values.length === 0 ? t('reply.skipped') : values.join(', ')}</dd>
                </div>
              })}
            </dl>}
        </UserMessageBubble>
      </div>
      <div className={css.actions}>
        {data.time > 0 && <span className={css.time}>
          {new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false }).format(data.time)}
        </span>}
        <span title={copied ? t('copied') : t('copy')}>
          <button type="button" className={css.action} aria-label={copied ? t('copied') : t('copy')} onClick={onCopy}>
            {copied ? <IconCheck size={15} /> : <IconCopy size={15} />}
          </button>
        </span>
      </div>
    </div>
  )
}
