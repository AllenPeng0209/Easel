import { useEffect, useRef, useState, type FormEvent } from 'react';
import ToolValueInspector from './ToolValueInspector';
import './SuperagentPanel.css';

export type SuperagentTaskStatus = 'queued' | 'running' | 'waiting_input' | 'stopping' | 'stopped' | 'completed' | 'failed' | 'unconfirmed';

export interface SuperagentQuestion {
  id: string;
  prompt: string;
  options: string[];
  requiresHuman: boolean;
  defaultAnswer?: string;
  deadlineAt: number;
  status: 'pending' | 'answered' | 'defaulted' | 'expired' | 'cancelled';
  answer?: string;
  resolvedBy?: 'human' | 'default';
  createdAt: number;
  resolvedAt?: number;
}

export interface SuperagentTask {
  id: string;
  actorId: string;
  projectId: string;
  title: string;
  brief: string;
  status: SuperagentTaskStatus;
  createdAt: number;
  updatedAt: number;
  steps: {
    id: string; kind: string; payload?: unknown; retrySafe: boolean; status: string;
    attempts: number; receipt?: unknown; error?: string | { code: string; message: string }; question?: SuperagentQuestion;
  }[];
  question?: SuperagentQuestion | null;
  leaseExpiresAt?: number;
}

interface ProjectOption { id: string; name: string; canOperate?: boolean }
interface TaskScope { projectId: string }
interface CreateTask extends TaskScope { title: string; brief: string }

export interface SuperagentPanelProps {
  embedded?: boolean;
  projects: readonly ProjectOption[];
  loadTasks: (signal?: AbortSignal) => Promise<SuperagentTask[]>;
  createTask: (request: CreateTask) => Promise<SuperagentTask>;
  queuePublication?: (request: TaskScope & { approvalId: string }) => Promise<SuperagentTask>;
  answerTask: (id: string, request: TaskScope & { questionId: string; answer: string }) => Promise<SuperagentTask>;
  stopTask: (id: string, request: TaskScope) => Promise<SuperagentTask>;
  retryTask: (id: string, request: TaskScope) => Promise<SuperagentTask>;
}

const labels = new Map<string, string>([
  ['queued', '排队中'], ['running', '执行中'], ['waiting_input', '等待答复'], ['stopping', '正在停止'],
  ['stopped', '已停止'], ['completed', '已完成'], ['failed', '执行失败'], ['unconfirmed', '结果待核对'],
  ['pending', '待执行'], ['skipped', '已跳过'],
]);
const stepLabels = new Map([
  ['preference', '创作偏好'], ['draft', '生成草稿'], ['review', '人工审核草稿'], ['publish', '执行已批准发布'],
]);
const activeStatuses = new Set<SuperagentTaskStatus>(['queued', 'running', 'waiting_input']);
const dateTime = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const refreshInterval = 3000;
type Operation = 'answer' | 'stop' | 'retry';
const pendingLabels: Record<Operation, string> = { answer: '正在提交答复…', stop: '正在请求停止…', retry: '正在请求重试…' };

function timeText(value: number): string {
  return Number.isFinite(value) && Math.abs(value) <= 8.64e15 ? dateTime.format(value) : '时间未确认';
}

function TaskCard({ task, canOperate, pending, error, answer, stop, retry }: {
  task: SuperagentTask; canOperate: boolean; pending?: Operation; error?: string;
  answer: (questionId: string, choice: string) => void; stop: () => void; retry: () => void;
}) {
  const question = task.status === 'waiting_input' && task.question?.status === 'pending' ? task.question : undefined;
  const deadlineReached = question && Number.isFinite(question.deadlineAt) && Date.now() >= question.deadlineAt;
  const failedStep = task.steps.find(step => step.status === 'failed');
  const canRetry = task.status === 'failed' && failedStep?.retrySafe === true;
  const resolutions = new Map<string, SuperagentQuestion>();
  for (const candidate of [...task.steps.map(step => step.question), task.question]) {
    if (candidate && candidate.status !== 'pending') resolutions.set(candidate.id, candidate);
  }

  return <article className="superagent-task" data-superagent-task={task.id} data-superagent-status={task.status} aria-busy={!!pending}>
    <header>
      <div><h3>{task.title}</h3><span>更新于 {timeText(task.updatedAt)}</span></div>
      <span className="superagent-badge" data-status={task.status}>{labels.get(task.status) ?? '状态未确认'}</span>
    </header>
    <p className="superagent-brief">{task.brief}</p>

    {question && <section className="superagent-question" aria-label="待答问题" data-superagent-question={question.id}>
      <strong>{question.requiresHuman ? '待人工确认' : '待答偏好'}</strong>
      <p>{question.prompt}</p>
      <p data-superagent-question-deadline>答复截止 {timeText(question.deadlineAt)}</p>
      {question.requiresHuman ? <p>此问题需要人工选择，超时不会自动批准。</p>
        : question.defaultAnswer && <p>无人答复时采用默认选项：{question.defaultAnswer}</p>}
      {deadlineReached && <p role="status">已到截止时间，正在等待服务端结算。</p>}
      <div className="superagent-actions">
        {question.options.map(choice => <button className="btn btn-sm" type="button" key={choice}
          disabled={!canOperate || !!pending || deadlineReached} data-superagent-answer={choice}
          onClick={() => answer(question.id, choice)}>{choice}</button>)}
      </div>
    </section>}

    {task.steps.length > 0 && <details className="superagent-steps">
      <summary>执行步骤 · {task.steps.length} 项</summary>
      <ol>{task.steps.map(step => {
        const message = typeof step.error === 'string' ? step.error : step.error?.message;
        return <li key={step.id} data-superagent-step={step.id}>
          <div className="superagent-step-line"><strong>{stepLabels.get(step.kind) ?? step.kind}</strong>
            <span>{labels.get(step.status) ?? '状态未确认'} · 尝试 {step.attempts} 次</span></div>
          {step.error && <p className="superagent-error">
            {typeof message === 'string' && message ? message.replace(/[\p{Cc}]/gu, ' ').slice(0, 500) : '步骤结果暂未确认。'}
          </p>}
          {step.receipt !== undefined && step.receipt !== null && <ToolValueInspector label="步骤回执" value={step.receipt} />}
        </li>;
      })}</ol>
    </details>}
    {[...resolutions.values()].map(resolution => <p key={resolution.id} className="superagent-resolution"
      data-superagent-question-resolution>
      {resolution.resolvedBy === 'default' ? '按默认选项继续'
        : resolution.resolvedBy === 'human' ? '人工答复' : resolution.status === 'expired' ? '答复已过期' : '答复已关闭'}
      {resolution.answer && <> · {resolution.answer}</>}
      {resolution.resolvedAt !== undefined && <> · {timeText(resolution.resolvedAt)}</>}
    </p>)}

    <div className="superagent-actions">
      {canOperate && activeStatuses.has(task.status) && <button type="button" className="btn btn-sm" data-superagent-stop
        disabled={!!pending} onClick={stop}>停止任务</button>}
      {canOperate && canRetry && <button type="button" className="btn btn-sm" data-superagent-retry
        disabled={!!pending} onClick={retry}>重试安全步骤</button>}
      {pending && <span role="status">{pendingLabels[pending]}</span>}
    </div>
    {error && <p className="superagent-error" role="alert">{error}</p>}
  </article>;
}

function PublicationIntake({ projects, onQueue, onQueued }: {
  projects: readonly ProjectOption[];
  onQueue: NonNullable<SuperagentPanelProps['queuePublication']>;
  onQueued: (task: SuperagentTask) => void;
}) {
  const [projectId, setProjectId] = useState('');
  const [approvalId, setApprovalId] = useState('');
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<{ error: boolean; text: string }>();
  const pendingRef = useRef(false);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const eligible = /^[a-f0-9]{32}$/.test(approvalId.trim()) &&
    projects.some(project => project.id === projectId && project.canOperate !== false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!eligible || pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setNotice(undefined);
    try {
      const task = await onQueue({ projectId, approvalId: approvalId.trim() });
      if (!mounted.current) return;
      if (task.projectId !== projectId) throw new Error('Mismatched publication task scope');
      onQueued(task);
      setApprovalId('');
      setNotice({ error: false, text: '已取得发布任务记录，请在下方核对状态和回执。' });
    } catch {
      if (mounted.current) setNotice({ error: true, text: '发布任务未确认入队，请核对批准编号和任务列表。' });
    } finally {
      pendingRef.current = false;
      if (mounted.current) setPending(false);
    }
  };
  return <details className="superagent-publication">
    <summary>已有发布批准</summary>
    <form data-superagent-queue-publication aria-label="将已批准的发布加入任务" onSubmit={event => void submit(event)}>
      <p>草稿审核与正式发布分别记录。此入口使用已确认的发布批准，加入后会执行该版本的发布任务。</p>
      <div className="superagent-create-fields">
        <label>发布所属 IP<select className="field" value={projectId} disabled={pending} required
          onChange={event => setProjectId(event.target.value)}>
          <option value="">选择批准记录所属 IP</option>
          {projects.filter(project => project.canOperate !== false).map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select></label>
        <label>已批准发布编号<input className="field" value={approvalId} disabled={pending} maxLength={32}
          pattern="[a-f0-9]{32}" required spellCheck={false} onChange={event => setApprovalId(event.target.value)} /></label>
      </div>
      <div className="superagent-actions"><button type="submit" className="btn btn-sm" data-superagent-queue-publication-submit
        disabled={!eligible || pending}>{pending ? '正在核对批准…' : '加入已批准发布任务'}</button></div>
      {notice && <p role={notice.error ? 'alert' : 'status'} className={notice.error ? 'superagent-error' : undefined}>{notice.text}</p>}
    </form>
  </details>;
}

export default function SuperagentPanel({ embedded = false, projects, loadTasks, createTask, queuePublication, answerTask, stopTask, retryTask }: SuperagentPanelProps) {
  const [tasks, setTasks] = useState<SuperagentTask[]>();
  const [listError, setListError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [projectId, setProjectId] = useState('');
  const [title, setTitle] = useState('');
  const [brief, setBrief] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [pending, setPending] = useState<Record<string, Operation>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const alive = useRef(false);
  const mutationEpoch = useRef(0);
  const createPending = useRef(false);
  const taskRequests = useRef(new Set<string>());

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  useEffect(() => {
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    const pageVisible = () => document.visibilityState !== 'hidden';
    const refreshList = async () => {
      if (closed || controller || !pageVisible()) return;
      const request = new AbortController();
      controller = request;
      const epoch = mutationEpoch.current;
      try {
        const result = await loadTasks(request.signal);
        if (!request.signal.aborted && epoch === mutationEpoch.current) {
          setTasks(result);
          setListError('');
        }
      } catch {
        if (!request.signal.aborted) setListError('任务列表暂时无法读取，保留上次记录。');
      } finally {
        if (controller === request) controller = undefined;
        if (!closed && pageVisible()) timer = setTimeout(() => void refreshList(), refreshInterval);
      }
    };
    const visibility = () => {
      clearTimeout(timer);
      if (!pageVisible()) controller?.abort();
      else void refreshList();
    };
    document.addEventListener('visibilitychange', visibility);
    void refreshList();
    return () => {
      closed = true;
      controller?.abort();
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [loadTasks, refresh]);

  const remember = (task: SuperagentTask) => {
    mutationEpoch.current += 1;
    setTasks(previous => [task, ...(previous ?? []).filter(item => item.id !== task.id || item.projectId !== task.projectId)]);
  };

  const canCreate = projects.some(project => project.id === projectId && project.canOperate !== false) && !!title.trim() && !!brief.trim();
  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!canCreate || createPending.current) return;
    createPending.current = true;
    setCreating(true);
    setCreateError('');
    try {
      const task = await createTask({ projectId, title: title.trim(), brief: brief.trim() });
      if (!alive.current) return;
      if (task.projectId !== projectId) throw new Error('Mismatched task scope');
      remember(task);
      setTitle('');
      setBrief('');
    } catch {
      if (alive.current) setCreateError('任务未确认创建，请刷新列表核对后再试。');
    } finally {
      createPending.current = false;
      if (alive.current) setCreating(false);
    }
  };

  const execute = async (task: SuperagentTask, operation: Operation, run: () => Promise<SuperagentTask>) => {
    const key = JSON.stringify([task.projectId, task.id]);
    if (taskRequests.current.has(key)) return;
    taskRequests.current.add(key);
    setPending(previous => ({ ...previous, [key]: operation }));
    setErrors(previous => ({ ...previous, [key]: '' }));
    try {
      const next = await run();
      if (!alive.current) return;
      if (next.id !== task.id || next.projectId !== task.projectId) throw new Error('Mismatched task receipt');
      remember(next);
    } catch {
      if (alive.current) setErrors(previous => ({ ...previous, [key]: '操作尚未确认，请刷新任务状态后核对。' }));
    } finally {
      taskRequests.current.delete(key);
      if (alive.current) setPending(previous => {
        const next = { ...previous };
        delete next[key];
        return next;
      });
    }
  };

  const byProject = new Map<string, SuperagentTask[]>();
  for (const task of tasks ?? []) {
    const group = byProject.get(task.projectId) ?? [];
    group.push(task);
    byProject.set(task.projectId, group);
  }

  if (embedded && !tasks?.length && !listError) return null;
  const Container = embedded ? 'section' : 'main';
  return <Container className={`superagent-panel${embedded ? ' superagent-panel-embedded' : ''}`} data-superagent-panel aria-label="Super Agent 任务">
    <header className="superagent-header">
      <div>{embedded ? <h2>Super Agent · 后台任务</h2> : <><p>跨 IP 任务管理</p><h1>Super Agent</h1></>}</div>
      <button type="button" className="btn btn-sm" data-superagent-refresh onClick={() => setRefresh(value => value + 1)}>刷新任务</button>
    </header>
    {!embedded && <form className="superagent-create" data-superagent-create aria-label="新建自主任务" onSubmit={event => void create(event)}>
      <h2>新建自主任务</h2>
      <div className="superagent-create-fields">
        <label>所属 IP<select className="field" value={projectId} disabled={creating} onChange={event => setProjectId(event.target.value)} required>
          <option value="">选择要执行的 IP</option>
          {projects.filter(project => project.canOperate !== false).map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select></label>
        <label>任务名称<input className="field" value={title} maxLength={160} disabled={creating} required
          onChange={event => setTitle(event.target.value)} /></label>
      </div>
      <label>任务目标<textarea className="field" rows={3} value={brief} maxLength={12000} disabled={creating} required
        onChange={event => setBrief(event.target.value)} placeholder="说明要完成的工作，以及什么结果算完成" /></label>
      <div className="superagent-actions"><button className="btn btn-primary" type="submit" disabled={!canCreate || creating}>
        {creating ? '正在创建…' : '创建任务'}</button></div>
      {createError && <p className="superagent-error" role="alert">{createError}</p>}
    </form>}
    {queuePublication && <PublicationIntake projects={projects} onQueue={queuePublication} onQueued={remember} />}
    {listError && <p className="superagent-error" role="alert">{listError}</p>}
    {!tasks && !listError && <p role="status">正在读取任务列表…</p>}
    {tasks?.length === 0 && <p>暂无自主任务。</p>}
    {[...byProject].map(([id, group]) => <section className="superagent-project" data-superagent-project={id} key={id}>
      <h2>{projects.find(project => project.id === id)?.name || id}</h2>
      {group.map(task => {
        const key = JSON.stringify([task.projectId, task.id]);
        const scope = { projectId: task.projectId };
        const canOperate = projects.some(project => project.id === task.projectId && project.canOperate !== false);
        return <TaskCard key={key} task={task} canOperate={canOperate} pending={pending[key]} error={errors[key]}
          answer={(questionId, answer) => void execute(task, 'answer', () => answerTask(task.id, { ...scope, questionId, answer }))}
          stop={() => void execute(task, 'stop', () => stopTask(task.id, scope))}
          retry={() => void execute(task, 'retry', () => retryTask(task.id, scope))} />;
      })}
    </section>)}
  </Container>;
}
