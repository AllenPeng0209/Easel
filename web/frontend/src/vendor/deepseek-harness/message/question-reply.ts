/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Source: packages/client/ui-user-questions/src/client/question-reply.ts
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel: extracted the unchanged payload/clipboard functions from Cordis registration;
 * translator type is a local function boundary. See integrations/deepseek-harness/LICENSE.
 */
export type QuestionReplyTranslator = (key: string) => string;
export interface QuestionReplyQuestion {
  readonly id: string
  readonly question: string
  readonly header?: string
}

/** One structured answer as echoed in the reply payload. */
export interface QuestionReplyAnswer {
  readonly id: string
  readonly selected: readonly string[]
  readonly custom?: string
}

/** Presentation data of one submitted answer batch. */
export interface QuestionReplyData {
  readonly callId: string
  /** A submitted answer batch, including individually skipped questions. */
  readonly outcome: 'answered'
  readonly questions: readonly QuestionReplyQuestion[]
  readonly answers: readonly QuestionReplyAnswer[]
  /** Model-facing text, shown when the payload is unreadable. */
  readonly text: string
  readonly time: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Read the question and answer pairs out of the reply text at the conversation boundary.
 * @param text - Model-facing JSON payload of the steered message.
 * @returns The pairs, or empty lists when the payload is unreadable.
 */
export function replyPairsOf(text: string): Pick<QuestionReplyData, 'questions' | 'answers'> {
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch (error) {
    // A reply whose text is not JSON renders as its raw text.
    void error
    return { questions: [], answers: [] }
  }
  if (!isRecord(value) || !Array.isArray(value.questions)) {
    return { questions: [], answers: [] }
  }
  const questions: QuestionReplyQuestion[] = []
  for (const question of value.questions) {
    if (!isRecord(question) || typeof question.id !== 'string' || typeof question.question !== 'string') continue
    questions.push({
      id: question.id,
      question: question.question,
      ...(typeof question.header === 'string' ? { header: question.header } : {}),
    })
  }
  const answers: QuestionReplyAnswer[] = []
  for (const answer of Array.isArray(value.answers) ? value.answers : []) {
    if (!isRecord(answer) || typeof answer.id !== 'string' || !Array.isArray(answer.selected)) continue
    answers.push({
      id: answer.id,
      selected: answer.selected.filter((item): item is string => typeof item === 'string'),
      ...(typeof answer.custom === 'string' ? { custom: answer.custom } : {}),
    })
  }
  return { questions, answers }
}

/**
 * Answer values of one question in display order: the selected option labels
 * followed by a non-blank custom answer.
 * @param data - Projected reply holding the recorded answers.
 * @param id - Question id to read.
 * @returns The values, empty when the user skipped that question.
 */
export function replyAnswerValues(data: QuestionReplyData, id: string): string[] {
  const answer = data.answers.find(item => item.id === id)
  const custom = answer?.custom?.trim() ?? ''
  return [...(answer?.selected ?? []), ...(custom === '' ? [] : [custom])]
}

/**
 * Clipboard text of one answer batch: every question with the answer the user
 * gave, without question details or unselected options.
 * @param data - Projected reply to copy.
 * @param t - Bound `question` namespace translator owning the answer labels.
 * @returns One block per question, or the model-facing text when the payload was unreadable.
 */
export function replyClipboardText(data: QuestionReplyData, t: QuestionReplyTranslator): string {
  if (data.questions.length === 0) return data.text
  return data.questions.map((question) => {
    const values = replyAnswerValues(data, question.id)
    const heading = question.header && question.header !== question.question
      ? `${question.header} — ${question.question}`
      : question.question
    const answer = values.length === 0
      ? t('reply.skipped')
      : `${t('reply.answerLabel')}${values.join(', ')}`
    return `${heading}\n${answer}`
  }).join('\n\n')
}
