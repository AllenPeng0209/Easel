import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ChatQuestion, ChatQuestionClosed, ChatQuestionItem } from '../lib/api';
import { answerQuestion, questionStatus } from '../lib/api';
import { createQuestionDraftStore } from '../vendor/deepseek-harness/message/question-drafts';
import { QuestionReplyBubble } from '../vendor/deepseek-harness/message/QuestionReplyBubble';
import type { QuestionReplyData } from '../vendor/deepseek-harness/message/question-reply';
import '../styles/agent-chat.css';
import { PublicationSubjectView } from './PublicationApprovalDialog';
import { publicationReceiptInScope } from '../lib/publicationReceipt';
import { requestApprovalPublication } from '../lib/publicationHandoff';

function PublicationReviewLink({ publication }: { publication: NonNullable<ChatQuestion['publication']> }) {
  if (!publicationReceiptInScope(publication)) return null;
  return <button type="button" className="btn btn-secondary"
    onClick={() => { requestApprovalPublication(publication); }}>到发布中心核对</button>;
}

type QuestionDetails = ChatQuestion & {
  kind?: 'preference' | 'approval';
  defaultAnswers?: Record<string, string[]>;
  autoResolveAtMs?: number | null;
  resolvedBy?: string;
  answers?: Record<string, string[]>;
  resolvedAtMs?: number;
  turnId?: string;
};
const settledStatuses = new Set(['answered', 'resolved', 'defaulted', 'declined', 'blocked']);
const definitiveStatuses = new Set([...settledStatuses, 'expired', 'revoked', 'replaced', 'cancelled']);
const terminalStatuses = new Set([...definitiveStatuses, 'closed', 'not_found']);
type SubmitPhase = 'idle' | 'submitting' | 'checking';

function requestIdentity(question: QuestionDetails, sessionId: string): string {
  return JSON.stringify([sessionId, question.id, question.turnId ?? null, question.publication?.id ?? null,
    question.publication?.payloadHash ?? null, question.kind ?? null, question.requiresHuman === true,
    question.questions.map(item => [item.questionId, item.question, item.multiSelect === true, item.options?.map(option => option.label) ?? []])]);
}

function isExecutionApproval(question: QuestionDetails): boolean {
  return !question.publication && (question.kind === 'approval' || question.requiresHuman === true
    || question.questions.some(item => item.header === '执行授权'));
}

function matchingReceipt(value: unknown, id: string, allowClosed = false): ChatQuestionClosed | null {
  if (!value || typeof value !== 'object') return null;
  const receipt = value as Partial<ChatQuestionClosed>;
  if (receipt.id !== id || typeof receipt.status !== 'string'
      || !(allowClosed ? terminalStatuses : definitiveStatuses).has(receipt.status)) return null;
  const answers = receipt.answers && typeof receipt.answers === 'object' && !Array.isArray(receipt.answers)
    && Object.values(receipt.answers).every(values => Array.isArray(values) && values.every(value => typeof value === 'string'))
    ? receipt.answers : undefined;
  return { id, status: receipt.status, ...(answers ? { answers } : {}),
    ...(typeof receipt.resolvedBy === 'string' ? { resolvedBy: receipt.resolvedBy } : {}) };
}
const replyLabels: Record<string, string> = {
  'reply.label': '已接受的问答', 'reply.skipped': '未提供答案', 'reply.answerLabel': '答复：', copied: '已复制', copy: '复制',
};

function QuestionResolution({ question }: { question: QuestionDetails }) {
  const executionApproval = isExecutionApproval(question);
  const closedLabels: Record<string, string> = {
    historical: '历史授权请求无法在此提交', revoked: '操作权限已撤销',
    replaced: '该授权请求已被替换', cancelled: '该授权请求已取消',
  };
  const closedLabel = closedLabels[question.status || ''] || '该授权请求已过期或关闭';
  const data: QuestionReplyData = {
    callId: question.id, outcome: 'answered', time: question.resolvedAtMs || 0, text: '',
    questions: question.questions.map(item => ({ id: item.questionId, question: item.question, header: item.header })),
    answers: Object.entries(question.answers || {}).map(([id, selected]) => ({ id, selected })),
  };
  return <div className="question-resolution" data-question-request={question.id} data-question-phase={question.status}>
    {question.publication && <><PublicationSubjectView subject={question.publication.subject} />
      <PublicationReviewLink publication={question.publication} /></>}
    <div className="question-resolution__status">{question.publication && question.resolvedBy === 'publication_host' ? '发布中心的决定已同步'
      : question.status === 'blocked' ? '等待确认超时，任务在需要授权的步骤受阻'
      : question.status === 'declined' ? '未获授权，操作未执行'
      : !settledStatuses.has(question.status || '') ? closedLabel
        : question.resolvedBy === 'default' || question.status === 'defaulted' ? '已按默认答案继续' : '已答复'}</div>
    {!executionApproval && (question.answers ? <QuestionReplyBubble data={data} t={key => replyLabels[key] || key} />
      : ['answered', 'resolved', 'defaulted'].includes(question.status || '')
        ? <p className="agent-event__note">答复已被服务端接受，正在同步记录。</p> : null)}
  </div>;
}

/**
 * ask_user 问答题卡片。
 *
 * OpenClaw 的 ask_user 一次可带 1-3 个问题（question.questions[]）。gateway 的
 * question.resolve 要求 answers 里**每个问题都有答案**（单个缺失即报
 * QUESTION_INVALID_ANSWER: "question 'xxx' requires an answer"）。
 * 多问题保留完整题目，用户选齐后明确提交一次。
 *
 * 多选：问题对象带 multiSelect: true 时，answers[qid] 可携带多个 label
 * （gateway 对 multiSelect=false 的多值直接报 "does not allow multiple answers"，
 * 前端只按数据渲染，不绕过校验）。
 */
function QuestionCard({ question: original, sessionId, requestKey, onReceipt }: {
  question: QuestionDetails; sessionId: string; requestKey: string;
  onReceipt?: (receipt: ChatQuestionClosed) => void;
}) {
  const [resolution, setResolution] = useState<ChatQuestionClosed>();
  const authoritativeStatus = terminalStatuses.has(original.status || '') ? original.status : undefined;
  const question = { ...original, ...(authoritativeStatus ? {} : resolution) };
  const draftStore = useMemo(() => createQuestionDraftStore(sessionId), [sessionId]);
  const draftKey = original.turnId || original.publication || original.kind === 'approval' || original.requiresHuman
    ? requestKey : original.id;
  const [draft] = useState(() => draftStore.get(draftKey));
  const [phase, setPhase] = useState<SubmitPhase>('idle');
  const phaseRef = useRef<SubmitPhase>('idle');
  const submission = useRef<symbol | null>(null);
  const received = useRef(false);
  const mounted = useRef(true);
  const originalStatus = useRef(authoritativeStatus);
  originalStatus.current = authoritativeStatus;
  const receiptCallback = useRef(onReceipt);
  receiptCallback.current = onReceipt;
  const [reconcile, setReconcile] = useState(0);
  const [pendingConfirmed, setPendingConfirmed] = useState(false);
  const changePhase = useCallback((next: SubmitPhase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; submission.current = null; };
  }, []);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Record<string, string[]>>(() => Object.fromEntries(question.questions
    .map((item, index) => [item.questionId, draft?.drafts[index]?.selected || []])));
  const [custom, setCustom] = useState<Record<string, string>>(() => Object.fromEntries(question.questions
    .map((item, index) => [item.questionId, draft?.drafts[index]?.custom || ''])));
  const [customVals, setCustomVals] = useState<Record<string, string[]>>(() => Object.fromEntries(question.questions
    .map((item, index) => [item.questionId, (draft?.drafts[index]?.selected || [])
      .filter(value => !(item.options || []).some(option => option.label === value))])));
  const [showCustom, setShowCustom] = useState<Record<string, boolean>>({});
  const [now, setNow] = useState(Date.now());
  const executionApproval = isExecutionApproval(question);
  const requiresApproval = executionApproval || question.kind === 'approval' || question.requiresHuman === true;
  const defaultScheduled = !requiresApproval && typeof question.autoResolveAtMs === 'number'
    && !!question.defaultAnswers && Object.keys(question.defaultAnswers).length > 0;
  const deadlineReached = !defaultScheduled && typeof question.expiresAtMs === 'number' && now >= question.expiresAtMs;
  const observedStatus = question.status === 'unknown' && pendingConfirmed ? 'pending' : question.status;
  const status = authoritativeStatus || resolution?.status || observedStatus || 'pending';
  const settled = settledStatuses.has(status);
  const expired = deadlineReached || terminalStatuses.has(status);
  const locked = phase !== 'idle' || expired || status !== 'pending';
  const applyReceipt = useCallback((value: unknown): boolean => {
    const receipt = matchingReceipt(value, original.id, executionApproval);
    if (!receipt || !mounted.current || received.current || originalStatus.current) return false;
    received.current = true;
    setResolution(receipt);
    setError('');
    changePhase('idle');
    draftStore.clear(draftKey);
    receiptCallback.current?.(receipt);
    return true;
  }, [changePhase, draftKey, draftStore, original.id, executionApproval]);

  useEffect(() => {
    if (terminalStatuses.has(status)) { draftStore.clear(draftKey); return; }
    draftStore.replace(draftKey, { index: 0, drafts: question.questions.map(item => ({
      selected: selected[item.questionId] || [], custom: custom[item.questionId] || '', skipped: false,
    })) });
  }, [draftKey, draftStore, question.questions, selected, custom, status]);

  useEffect(() => {
    if (terminalStatuses.has(status)) return;
    let active = true;
    let polling = false;
    let lastPoll = 0;
    const tick = () => {
      setNow(Date.now());
      if (polling || Date.now() - lastPoll < 4500) return;
      polling = true;
      lastPoll = Date.now();
      void questionStatus([original.id], executionApproval && sessionId !== 'legacy' ? sessionId : undefined).then(states => {
        if (!active || !mounted.current || originalStatus.current || received.current) return;
        const details = states[original.id] as (Partial<ChatQuestionClosed> & { status: string }) | undefined;
        if (details?.id !== undefined && details.id !== original.id) return;
        const next = details?.status;
        if (next && (executionApproval ? terminalStatuses : definitiveStatuses).has(next)) {
          applyReceipt({ ...details, id: original.id });
        } else if (next === 'pending') {
          setPendingConfirmed(true);
          if (phaseRef.current === 'checking' && !submission.current) {
            changePhase('idle');
            setError('服务端仍在等待答复，请核对后重新提交。');
          }
        }
      }).catch(() => { /* Keep the current request visible until status is known. */ })
        .finally(() => { polling = false; });
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => { active = false; clearInterval(timer); };
  }, [applyReceipt, changePhase, original.id, reconcile, executionApproval, sessionId, status]);

  const items: ChatQuestionItem[] = question.questions || [];
  const valsOf = (it: ChatQuestionItem): string[] => selected[it.questionId] || [];
  const allAnswered = items.length > 0 && items.every((it) => valsOf(it).length > 0);

  const pick = (it: ChatQuestionItem, label: string) => {
    setSelected((s) => {
      const cur = s[it.questionId] || [];
      const next = it.multiSelect
        ? (cur.includes(label) ? cur.filter((v) => v !== label) : [...cur, label])
        : [label];
      return { ...s, [it.questionId]: next };
    });
  };

  const fillCustom = (it: ChatQuestionItem) => {
    const v = (custom[it.questionId] || '').trim();
    if (!v) return;
    setSelected((s) => {
      const cur = s[it.questionId] || [];
      return { ...s, [it.questionId]: it.multiSelect && !cur.includes(v) ? [...cur, v] : [v] };
    });
    setCustomVals((c) => {
      const cur = c[it.questionId] || [];
      return { ...c, [it.questionId]: cur.includes(v) ? cur : [...cur, v] };
    });
  };

  const removeCustom = (it: ChatQuestionItem, v: string) => {
    setSelected((s) => ({ ...s, [it.questionId]: (s[it.questionId] || []).filter((x) => x !== v) }));
    setCustomVals((c) => ({ ...c, [it.questionId]: (c[it.questionId] || []).filter((x) => x !== v) }));
  };

  const submit = async (override?: Record<string, string[]>) => {
    if (locked || submission.current || received.current || originalStatus.current || !items.length ||
        (!defaultScheduled && typeof question.expiresAtMs === 'number' && Date.now() >= question.expiresAtMs)) return;
    const answers: Record<string, string[]> = {};
    for (const it of items) {
      const values = override ? override[it.questionId] : valsOf(it);
      if (!Array.isArray(values) || !values.length || values.some(value => typeof value !== 'string' || !value.trim())) return;
      answers[it.questionId] = values;
    }
    const owner = Symbol('question submission');
    submission.current = owner;
    changePhase('submitting'); setError('');
    try {
      const response = await answerQuestion({ questionId: question.id, answers,
        ...(sessionId === 'legacy' ? {} : { sessionId }), ...(question.turnId ? { turnId: question.turnId } : {}) });
      if (!mounted.current || submission.current !== owner || received.current || originalStatus.current) return;
      if (applyReceipt(response.receipt)) return;
    } catch {
      if (!mounted.current || submission.current !== owner || received.current || originalStatus.current) return;
    } finally {
      if (submission.current === owner) submission.current = null;
    }
    changePhase('checking');
    setError('提交结果未确认，正在核对服务端状态。');
    setReconcile(value => value + 1);
  };

  if (settled || executionApproval && terminalStatuses.has(status)) return <QuestionResolution question={{ ...question, status }} />;
  const seconds = Math.max(0, Math.ceil(((question.autoResolveAtMs || 0) - now) / 1000));
  return (
    <div className="message-row assistant">
      <div className="msg-col assistant question-card-column">
        <div className="question-card" data-question-request={question.id}
          data-question-phase={expired ? status : phase} aria-busy={phase !== 'idle'}>
          <div className="question-card__mode">{requiresApproval ? '需要你确认' : '补充信息'}</div>
          {requiresApproval && <div className="question-card__hint">未答复不会自动授权。</div>}
          {question.publication && <><PublicationSubjectView subject={question.publication.subject} />
            <PublicationReviewLink publication={question.publication} />
            <p className="question-card__hint">本次批准摘要：{question.publication.payloadHash}</p></>}
          {defaultScheduled && !expired && <div className="question-card__default" role="status">
            {seconds > 0 ? `${seconds} 秒后将按默认答案继续，你可以在此前修改。` : '正在等待服务端按默认答案继续…'}
            <div className="question-card__actions">
              <button type="button" className="question-card__default-continue btn btn-secondary"
                disabled={locked || !items.every(item => Array.isArray(question.defaultAnswers?.[item.questionId])
                  && !!question.defaultAnswers[item.questionId].length)}
                onClick={() => void submit(question.defaultAnswers)}>按默认答案继续</button>
            </div>
          </div>}
          {expired ? <div className="question-card__hint">
              {status === 'revoked' ? '操作权限已撤销，无法提交。'
                : status === 'replaced' ? '该请求已被替换，请查看新的确认卡。'
                  : deadlineReached && !terminalStatuses.has(status) ? '本地截止时间已到，正在核对是否已过期或关闭。'
                    : '该问题已过期或关闭，无法再提交。请在聊天输入框发送补充答复。'}
            </div>
            : !defaultScheduled && typeof question.expiresAtMs === 'number' && <div className="question-card__hint">
              本次输入截止时间：{new Date(question.expiresAtMs).toLocaleString()} · 剩余约 {Math.max(0, Math.ceil((question.expiresAtMs - now) / 60000))} 分钟（可能提前关闭）
            </div>}
          {items.map((it, idx) => {
            const options: { label: string; description?: string }[] = it.options || [];
            const vals = valsOf(it);
            const ctl = showCustom[it.questionId];
            const cvals = customVals[it.questionId] || [];
            return (
              <div key={it.questionId} className="question-card__item">
                {idx > 0 && <div className="question-card__divider" />}
                {it.header && <div className="question-card__chip">{it.header}</div>}
                <div className="question-card__title">
                  {it.question || '请选择'}
                  {it.multiSelect && <span className="question-card__multi">可多选</span>}
                </div>
                {defaultScheduled && question.defaultAnswers?.[it.questionId]?.length
                  ? <div className="question-card__hint">默认：{question.defaultAnswers[it.questionId].join('、')}</div> : null}
                <div className="question-card__options">
                  {options.map((opt) => (
                    <button key={opt.label}
                      className={`question-card__option${vals.includes(opt.label) ? ' question-card__option--selected' : ''}`}
                      disabled={locked}
                      onClick={() => pick(it, opt.label)}>
                      <strong>{opt.label}</strong>
                      {opt.description && <span>{opt.description}</span>}
                    </button>
                  ))}
                  {cvals.map((v) => (
                    <button key={`custom-${v}`}
                      className="question-card__option question-card__option--selected"
                      disabled={locked}
                      title="点击移除"
                      onClick={() => removeCustom(it, v)}>
                      <strong>{v}</strong>
                      <span>自定义 · 点击移除</span>
                    </button>
                  ))}
                  <button className="question-card__other" disabled={locked}
                    onClick={() => setShowCustom((s) => ({ ...s, [it.questionId]: !s[it.questionId] }))}>
                    {ctl ? '收起自定义输入' : '自行输入…'}
                  </button>
                </div>
                {ctl && (
                  <div className="question-card__custom">
                    <input
                      type="text"
                      disabled={locked}
                      value={custom[it.questionId] || ''}
                      placeholder="输入你的答案"
                      onChange={(e) => setCustom((c) => ({ ...c, [it.questionId]: e.target.value }))}
                      onKeyDown={event => {
                        if (event.key === 'Enter' && !event.nativeEvent.isComposing && event.keyCode !== 229) {
                          event.preventDefault(); fillCustom(it);
                        }
                      }}
                    />
                    <button disabled={locked || !(custom[it.questionId] || '').trim()}
                      onClick={() => fillCustom(it)}>
                      填入
                    </button>
                  </div>
                )}
              </div>
            );
          })}

          {(items.length === 1 || allAnswered) && <div className="question-card__actions">
            <button className="question-card__submit" disabled={locked || !allAnswered}
              onClick={() => void submit()}>{phase === 'submitting' ? '提交中…'
                : phase === 'checking' ? '核对中…' : items.length === 1 ? '提交' : '全部已选，提交'}</button>
          </div>}
          {phase === 'submitting' && <div className="question-card__hint" role="status">提交中，等待服务端确认…</div>}
          {phase === 'idle' && status === 'unknown' && <div className="question-card__hint" role="status">正在核对服务端请求状态。</div>}
          {error && <div className="question-card__hint question-card__error">{error}</div>}
        </div>
      </div>
    </div>
  );
}

/** Keep a request visible until its exact terminal observation arrives. */
export default function QuestionCards({ questions, sessionId = 'legacy', onDone, historical = false }: {
  questions: ChatQuestion[];
  sessionId?: string;
  historical?: boolean;
  onDone?: (receipt: ChatQuestionClosed) => void;
}) {
  if (!questions.length) return null;
  return <>
    {questions.map(question => {
      const key = requestIdentity(question, sessionId);
      if (historical && isExecutionApproval(question)) {
        const status = terminalStatuses.has(question.status || '') ? question.status : 'historical';
        return <QuestionResolution key={key} question={{ ...question, status }} />;
      }
      return <QuestionCard key={key} question={question} sessionId={sessionId} requestKey={key} onReceipt={onDone} />;
    })}
  </>;
}
