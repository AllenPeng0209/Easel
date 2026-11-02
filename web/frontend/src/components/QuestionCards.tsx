import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChatQuestion, ChatQuestionItem } from '../lib/api';
import { answerQuestion, questionStatus } from '../lib/api';
import { createQuestionDraftStore } from '../vendor/deepseek-harness/message/question-drafts';
import { QuestionReplyBubble } from '../vendor/deepseek-harness/message/QuestionReplyBubble';
import type { QuestionReplyData } from '../vendor/deepseek-harness/message/question-reply';
import '../styles/agent-chat.css';
import { PublicationSubjectView } from './PublicationApprovalDialog';

type QuestionDetails = ChatQuestion & {
  kind?: 'preference' | 'approval';
  defaultAnswers?: Record<string, string[]>;
  autoResolveAtMs?: number | null;
  resolvedBy?: string;
  answers?: Record<string, string[]>;
  resolvedAtMs?: number;
};
const settledStatuses = new Set(['answered', 'resolved', 'defaulted', 'declined', 'blocked']);
const replyLabels: Record<string, string> = {
  'reply.label': '已接受的问答', 'reply.skipped': '未提供答案', 'reply.answerLabel': '答复：', copied: '已复制', copy: '复制',
};

function QuestionResolution({ question }: { question: QuestionDetails }) {
  const data: QuestionReplyData = {
    callId: question.id, outcome: 'answered', time: question.resolvedAtMs || 0, text: '',
    questions: question.questions.map(item => ({ id: item.questionId, question: item.question, header: item.header })),
    answers: Object.entries(question.answers || {}).map(([id, selected]) => ({ id, selected })),
  };
  return <div className="question-resolution">
    {question.publication && <PublicationSubjectView subject={question.publication.subject} />}
    <div className="question-resolution__status">{question.status === 'blocked' ? '等待确认超时，任务在需要授权的步骤受阻'
      : question.status === 'declined' ? '未获授权，操作未执行'
      : question.resolvedBy === 'default' || question.status === 'defaulted' ? '已按默认答案继续' : '已答复'}</div>
    {question.answers ? <QuestionReplyBubble data={data} t={key => replyLabels[key] || key} />
      : <p className="agent-event__note">答复已被服务端接受，正在同步记录。</p>}
  </div>;
}

/**
 * ask_user 问答题卡片。
 *
 * OpenClaw 的 ask_user 一次可带 1-3 个问题（question.questions[]）。gateway 的
 * question.resolve 要求 answers 里**每个问题都有答案**（单个缺失即报
 * QUESTION_INVALID_ANSWER: "question 'xxx' requires an answer"）。
 * 因此多问题时渲染全部题目，用户每问答完一项，全部选齐后自动提交一次。
 *
 * 多选：问题对象带 multiSelect: true 时，answers[qid] 可携带多个 label
 * （gateway 对 multiSelect=false 的多值直接报 "does not allow multiple answers"，
 * 前端只按数据渲染，不绕过校验）。
 */
function QuestionCard({ question: original, sessionId, onAnswered, onClosed }: { question: QuestionDetails; sessionId: string;
  onAnswered: (resolved: QuestionDetails) => void; onClosed?: (status: string) => void }) {
  const [resolution, setResolution] = useState<Partial<QuestionDetails>>({});
  const question = { ...original, ...resolution };
  const draftStore = useMemo(() => createQuestionDraftStore(sessionId), [sessionId]);
  const [draft] = useState(() => draftStore.get(question.id));
  const [busy, setBusy] = useState(false);
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
  const [closed, setClosed] = useState('');
  const requiresApproval = question.kind === 'approval' || question.requiresHuman === true;
  const defaultScheduled = !requiresApproval && typeof question.autoResolveAtMs === 'number'
    && !!question.defaultAnswers && Object.keys(question.defaultAnswers).length > 0;
  const deadlineReached = !defaultScheduled && typeof question.expiresAtMs === 'number' && now >= question.expiresAtMs;
  const status = closed || question.status || 'pending';
  const settled = settledStatuses.has(status);
  const expired = deadlineReached || (status !== 'pending' && status !== 'unknown');
  const locked = busy || expired;
  const closedCallback = useRef(onClosed);
  closedCallback.current = onClosed;

  useEffect(() => {
    if (settled || expired) { draftStore.clear(question.id); return; }
    draftStore.replace(question.id, { index: 0, drafts: question.questions.map(item => ({
      selected: selected[item.questionId] || [], custom: custom[item.questionId] || '', skipped: false,
    })) });
  }, [draftStore, question.id, question.questions, selected, custom, settled, expired]);

  useEffect(() => {
    if (status !== 'pending' || deadlineReached) return;
    let active = true;
    let lastPoll = 0;
    const tick = () => {
      setNow(Date.now());
      if (Date.now() - lastPoll < 4500) return;
      lastPoll = Date.now();
      void questionStatus([question.id]).then(states => {
        const details = states[question.id] as Partial<QuestionDetails> | undefined;
        const next = details?.status;
        if (active && next && next !== 'pending' && next !== 'unknown') {
          setResolution(details || {});
          setClosed(next);
          closedCallback.current?.(next);
        }
      });
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => { active = false; clearInterval(timer); };
  }, [question.id, status, deadlineReached]);

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
    if (locked || !items.length) return;
    const answers: Record<string, string[]> = {};
    for (const it of items) {
      const values = override ? override[it.questionId] : valsOf(it);
      if (!Array.isArray(values) || !values.length || values.some(value => typeof value !== 'string' || !value.trim())) return;
      answers[it.questionId] = values;
    }
    setBusy(true); setError('');
    try {
      const res = await answerQuestion({ questionId: question.id, answers });
      if (!res.ok && (res.expired || (res.status && res.status !== 'pending'))) {
        setClosed(res.status || 'expired');
        onClosed?.(res.status || 'expired');
        setError('该问题已过期或关闭。若仍需补充，请在聊天输入框发送答复。');
      } else if (!res.ok) setError(res.error || '提交失败');
      else {
        const accepted = { ...question, status: 'answered', answers, resolvedBy: 'human' };
        setResolution(accepted);
        setClosed('answered');
        draftStore.clear(question.id);
        onAnswered(accepted);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (settled) return <QuestionResolution question={{ ...question, status }} />;
  const seconds = Math.max(0, Math.ceil(((question.autoResolveAtMs || 0) - now) / 1000));
  return (
    <div className="message-row assistant">
      <div className="msg-col assistant question-card-column">
        <div className="question-card">
          <div className="question-card__mode">{requiresApproval ? '需要你确认' : '补充信息'}</div>
          {requiresApproval && <div className="question-card__hint">未答复不会自动授权。</div>}
          {question.publication && <><PublicationSubjectView subject={question.publication.subject} />
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
          {expired ? <div className="question-card__hint">该问题已过期或关闭，无法再提交。请在聊天输入框发送补充答复。</div>
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

          {/* 单选题：选完点提交；多题：全部选齐后提交（单选/多选同一规则） */}
          {items.length === 1 ? (
            <div className="question-card__actions">
              <button className="question-card__submit" disabled={locked || !allAnswered}
                onClick={() => void submit()}>
                提交
              </button>
            </div>
          ) : (
            allAnswered && !locked && (
              <div className="question-card__actions">
                <button className="question-card__submit" disabled={locked} onClick={() => void submit()}>
                  全部已选，提交
                </button>
              </div>
            )
          )}
          {busy && <div className="question-card__hint">已提交，Agent 继续处理中…</div>}
          {error && <div className="question-card__hint question-card__error">{error}</div>}
        </div>
      </div>
    </div>
  );
}

/** 流式中的 ask_user 问答题列表（一次性卡片，答完即从当前流移除）。 */
export default function QuestionCards({ questions, sessionId = 'legacy', onDone, onClosed }: { questions: ChatQuestion[];
  sessionId?: string; onDone?: (questionId: string) => void; onClosed?: (id: string, status: string) => void }) {
  const [answered, setAnswered] = useState<Record<string, QuestionDetails>>({});
  const visible = useMemo(() => questions.map(question => ({ ...question, ...answered[question.id] })), [questions, answered]);
  if (!visible.length) return null;
  return (
    <>
      {visible.map((q) => (
        <QuestionCard key={q.id} question={q} sessionId={sessionId} onClosed={status => onClosed?.(q.id, status)} onAnswered={resolved => {
          setAnswered((a) => ({ ...a, [q.id]: resolved }));
          onDone?.(q.id);   // 通知 App：此题已答，重放/恢复不再出现
        }} />
      ))}
    </>
  );
}
