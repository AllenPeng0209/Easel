/**
 * Version card and separate plan review actions adapted from DeepSeek Harness
 * ui-plan/src/client/{PlanCard.tsx,PlanPreview.tsx} @ ce7073d (MIT).
 */
import { useEffect, useRef, useState } from 'react';
import { approveChatPlan, chatPlanKey, ChatPlanReadError, fetchChatPlans, saveChatPlan,
  type ChatPlanRecord, type ChatPlanRoster } from '../lib/chatPlans';
import './ChatPlanPanel.css';

type Props = {
  sessionId: string;
  canOperate: boolean;
  onExecute?: (plan: ChatPlanRecord) => boolean | Promise<boolean>;
};
type Draft = { mode: 'brief' | 'text'; value: string; base: ChatPlanRecord | null };

const states = { proposed: '待批准', approved: '已批准', claimed: '执行已领取', superseded: '已被新版本替代' };

function requestError(error: unknown): string {
  if (error instanceof ChatPlanReadError) {
    if (error.status === 409) return '方案版本已更新，请刷新后核对最新方案。';
    if (error.status === 401 || error.status === 403) return '当前账号无法操作此对话的方案。';
    if (error.status === 422) return '方案输入不符合要求，请修改后再保存。';
  }
  return '方案操作未确认，请刷新记录后核对。';
}

export default function ChatPlanPanel(props: Props) {
  return <SessionPlanPanel key={props.sessionId} {...props} />;
}

function SessionPlanPanel({ sessionId, canOperate, onExecute }: Props) {
  const [roster, setRoster] = useState<ChatPlanRoster>();
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [draft, setDraft] = useState<Draft>();
  const [preview, setPreview] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [notice, setNotice] = useState('');
  const mutation = useRef<AbortController | null>(null);
  const executions = useRef(new Set<string>());
  const active = useRef(true);

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      mutation.current?.abort();
      mutation.current = null;
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void fetchChatPlans(sessionId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setRoster(value);
      setProblem('');
    }).catch(error => {
      if (controller.signal.aborted) return;
      if (error instanceof ChatPlanReadError && (error.status === 401 || error.status === 403)) setRoster(undefined);
      setProblem(requestError(error));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh, sessionId]);

  const latest = roster?.latest;
  const selected = roster?.plans.find(plan => chatPlanKey(plan) === preview);
  const reviewingLatest = !!selected && !!latest && chatPlanKey(selected) === chatPlanKey(latest);
  const editable = canOperate && !loading && !busy && !problem;
  const writing = canOperate ? draft ?? (!loading && roster && !latest
    ? { mode: 'brief' as const, value: '', base: null } : undefined) : undefined;
  const stale = !!writing && (writing.base ? !latest || chatPlanKey(writing.base) !== chatPlanKey(latest) : !!latest);

  const mutate = async (operation: (signal: AbortSignal) => Promise<ChatPlanRecord>, savedDraft: boolean) => {
    if (!editable || mutation.current) return;
    const controller = new AbortController();
    mutation.current = controller;
    setBusy(true);
    setNotice('');
    try {
      const record = await operation(controller.signal);
      if (mutation.current !== controller || controller.signal.aborted) return;
      setRoster(previous => ({
        plans: [record, ...(previous?.plans.filter(plan => chatPlanKey(plan) !== chatPlanKey(record)) ?? [])],
        latest: record,
      }));
      if (savedDraft) {
        setDraft(undefined);
        setPreview(undefined);
        setNotice(`方案 V${record.revision} 已保存，等待查看和批准。`);
        setLoading(true);
        setRefresh(value => value + 1);
      } else setNotice(`方案 V${record.revision} 已批准。`);
    } catch (error) {
      if (mutation.current === controller && !controller.signal.aborted) setProblem(requestError(error));
    } finally {
      if (mutation.current === controller) {
        mutation.current = null;
        setBusy(false);
      }
    }
  };
  const execute = async (plan: ChatPlanRecord) => {
    const key = chatPlanKey(plan);
    if (!editable || !reviewingLatest || writing || plan.state !== 'approved' || !onExecute ||
        executions.current.has(key)) return;
    executions.current.add(key);
    setBusy(true);
    setNotice('');
    try {
      const accepted = await onExecute(plan);
      if (active.current) setNotice(accepted ? `方案 V${plan.revision} 的执行请求已接收。` : '执行结果未确认，请先核对已有回合。');
    } catch {
      if (active.current) setNotice('执行结果未确认，请先核对已有回合。');
    } finally {
      if (active.current) {
        setBusy(false);
        setLoading(true);
        setRefresh(value => value + 1);
      }
    }
  };

  return <section className="chat-plan-panel" data-chat-plan-panel aria-label="先计划后执行">
    <header><div><h2>先计划后执行</h2><p>先核对方案版本，再分别批准和执行。公开发布另行确认最终内容。</p></div>
      <button type="button" disabled={loading || busy} onClick={() => setRefresh(value => value + 1)}>刷新方案</button>
    </header>
    {loading && !roster && <p role="status">正在读取方案…</p>}
    {problem && <p role="alert" data-chat-plan-error>{problem}</p>}
    {notice && <p role="status" data-chat-plan-notice>{notice}</p>}
    {roster && <>
      {!roster.plans.length && <p>此对话暂无已保存方案。</p>}
      <ul className="chat-plan-versions">
        {roster.plans.map(plan => <li key={chatPlanKey(plan)}>
          <button type="button" data-chat-plan-preview={plan.revision}
            aria-pressed={preview === chatPlanKey(plan)} onClick={() => setPreview(chatPlanKey(plan))}>
            查看方案 V{plan.revision}
          </button>
          <span>{latest && chatPlanKey(plan) !== chatPlanKey(latest) ? '历史版本 · ' : ''}{states[plan.state]}</span>
        </li>)}
      </ul>
    </>}
    {selected && <article data-chat-plan-version={selected.revision} data-chat-plan-state={selected.state}>
      <strong>方案 V{selected.revision} · {states[selected.state]}</strong>
      <p>SHA-256 <code>{selected.digest}</code></p>
      <pre data-chat-plan-body>{selected.text}</pre>
      {canOperate && reviewingLatest && !writing && <div className="chat-plan-actions">
        {selected.state === 'proposed' && <button type="button" data-approve-chat-plan
          disabled={!editable} onClick={() => void mutate(signal => approveChatPlan(sessionId, selected, signal), false)}>
          批准方案 V{selected.revision}
        </button>}
        {selected.state === 'approved' && onExecute && <button type="button" data-execute-chat-plan
          disabled={!editable || executions.current.has(chatPlanKey(selected))}
          onClick={() => void execute(selected)}>执行已批准方案 V{selected.revision}</button>}
        <button type="button" data-edit-chat-plan disabled={!editable}
          onClick={() => setDraft({ mode: 'text', value: selected.text, base: selected })}>编辑方案</button>
      </div>}
    </article>}
    {writing && <form onSubmit={event => {
      event.preventDefault();
      if (!writing.value.trim() || stale) return;
      const payload = writing.mode === 'brief' ? { brief: writing.value } : { text: writing.value };
      void mutate(signal => saveChatPlan(sessionId, payload, writing.base, signal), true);
    }}>
      <div className="chat-plan-actions">
        <button type="button" aria-pressed={writing.mode === 'brief'} disabled={!editable}
          onClick={() => setDraft({ ...writing, mode: 'brief', value: '' })}>按简述生成</button>
        <button type="button" aria-pressed={writing.mode === 'text'} disabled={!editable}
          onClick={() => setDraft({ ...writing, mode: 'text', value: writing.base?.text ?? '' })}>手动编写</button>
      </div>
      <label>{writing.mode === 'brief' ? '工作简述' : '方案正文'}
        <textarea value={writing.value} disabled={busy} rows={5}
          onInput={event => setDraft({ ...writing, value: event.currentTarget.value })} />
      </label>
      {stale && <p role="status">草稿基于旧版本，请复制内容后重新编辑最新方案。</p>}
      <div className="chat-plan-actions">
        <button type="submit" data-save-chat-plan disabled={!editable || stale || !writing.value.trim()}>
          {writing.mode === 'brief' ? '生成方案草稿' : '保存草稿'}
        </button>
        {writing.base && <button type="button" disabled={busy} onClick={() => setDraft(undefined)}>放弃编辑</button>}
      </div>
    </form>}
  </section>;
}
