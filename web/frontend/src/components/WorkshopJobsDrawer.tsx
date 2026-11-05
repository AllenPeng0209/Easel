/**
 * Read-only job roster adapted from DeepSeek Harness ui-jobs/JobListAction.tsx
 * @ ce7073d (MIT). 响芽 watches the current IP's stored render observations.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchWorkshopJobs, nextWorkshopJobObservations, observeWorkshopJob, requestWorkshopJobCancellation, WORKSHOP_JOB_LABELS, workshopJobElapsed, WorkshopCancelRequestError, WorkshopJobsReadError,
  type WorkshopJob, type WorkshopJobCancellation, type WorkshopJobRoster } from '../lib/workshopJobs';
import { ACTIVE_PROJECT_ID } from '../lib/projectScope';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import './WorkshopJobsDrawer.css';

const POLL_INTERVAL_MS = 5_000;
const REQUEST_TIMEOUT_MS = 12_000;
type RosterRequest = { controller: AbortController; deadline?: ReturnType<typeof setTimeout> };

type CancelMode = 'cancel' | 'verify';
type CancelNotice = { receipt: WorkshopJobCancellation } | { problem: 'unconfirmed' | 'rejected' };
type Confirmation = { job: WorkshopJob; mode: CancelMode };

function cancellationKey(job: WorkshopJob): string {
  return JSON.stringify([job.workshopProjectId, job.id, job.taskId]);
}

function currentCancellation(job: WorkshopJob, notice?: CancelNotice): WorkshopJobCancellation | undefined {
  const saved = job.cancellation;
  const latest = notice && 'receipt' in notice ? notice.receipt : undefined;
  if (!saved || !latest) return saved ?? latest;
  if (saved.status === 'cancelled' || saved.status === 'already_finished') return saved;
  if (latest.status === 'cancelled' || latest.status === 'already_finished') return latest;
  return (saved.updatedAt ?? -1) >= (latest.updatedAt ?? -1) ? saved : latest;
}

function cancellationAction(job: WorkshopJob, notice?: CancelNotice): CancelMode | undefined {
  if (!job.canCancel || !job.taskId) return undefined;
  const receipt = currentCancellation(job, notice);
  if (receipt?.status === 'cancelled' || receipt?.status === 'already_finished') return undefined;
  if (job.cancellation && (receipt?.status === 'pending' || receipt?.status === 'unconfirmed')) return 'verify';
  if (notice || receipt || job.status !== 'queued' && job.status !== 'running') return undefined;
  return 'cancel';
}

function cancellationLabel(receipt: WorkshopJobCancellation | undefined, notice?: CancelNotice): string | undefined {
  if (receipt?.status === 'pending') return '取消请求已记录，正在核对结果。';
  if (receipt?.status === 'unconfirmed') return '取消结果未确认，请核对已有请求。';
  if (receipt?.status === 'cancelled') return '平台已确认取消。';
  if (receipt?.status === 'already_finished') return receipt.jobStatus === 'failed'
    ? '平台返回任务已失败。' : '任务已结束，请查看最新记录。';
  return notice && 'problem' in notice
    ? notice.problem === 'rejected' ? '取消请求未获接纳，请刷新任务记录。' : '取消请求结果未确认，请刷新核对。'
    : undefined;
}

function WorkshopCancelConfirmation({ confirmation, pending, valid, onClose, onConfirm }: {
  confirmation: Confirmation; pending: boolean; valid: boolean; onClose: () => void; onConfirm: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const verify = confirmation.mode === 'verify';
  const title = verify ? '核对取消状态' : '确认取消工坊任务';
  useModalLayer(dialog, true, onClose);
  return createPortal(<div className="workshop-cancel-overlay" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section ref={dialog} className="workshop-cancel-dialog" role="dialog" aria-modal="true"
      aria-label={title} tabIndex={-1} data-workshop-cancel-confirmation>
      <h2>{title}</h2>
      <dl>
        <dt>IP</dt><dd>{ACTIVE_PROJECT_ID === 'default' ? '默认 IP' : ACTIVE_PROJECT_ID}</dd>
        <dt>任务类型</dt><dd>原生成片合成</dd>
        <dt>工坊</dt><dd>{confirmation.job.title}</dd>
        <dt>任务 ID</dt><dd><code>{confirmation.job.taskId}</code></dd>
      </dl>
      <p>{verify ? '读取原取消请求的最新状态。' : '取消请求仅关联此任务，结果以平台回执为准。'}</p>
      {!valid && !pending && <p role="status">任务状态已更新，请返回列表后重新选择。</p>}
      <footer>
        <button type="button" data-modal-autofocus disabled={pending} onClick={onClose}>返回</button>
        <button type="button" data-confirm-workshop-cancel disabled={pending || !valid} onClick={onConfirm}>
          {pending ? '等待回执…' : verify ? '确认核对状态' : '确认取消任务'}
        </button>
      </footer>
    </section>
  </div>, document.body);
}

export default function WorkshopJobsDrawer({ open, onClose, onOpenWorkshop, canOperate = false }: {
  open: boolean;
  canOperate?: boolean;
  onClose: () => void;
  onOpenWorkshop: (id: string, artifactId?: string) => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const observationCursor = useRef<string | undefined>(undefined);
  const [roster, setRoster] = useState<WorkshopJobRoster>();
  const [loading, setLoading] = useState(false);
  const [observationIncomplete, setObservationIncomplete] = useState(false);
  const [error, setError] = useState<'scope' | 'read'>();
  const [refresh, setRefresh] = useState(0);
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [cancelNotices, setCancelNotices] = useState(() => new Map<string, CancelNotice>());
  const [submitting, setSubmitting] = useState(false);
  const cancellationRequest = useRef<RosterRequest | null>(null);
  const closeDrawer = () => { if (!cancellationRequest.current) onClose(); };
  const closeConfirmation = () => { if (!cancellationRequest.current) setConfirmation(undefined); };
  useModalLayer(dialog, open, closeDrawer);

  useEffect(() => {
    if (!open) {
      setConfirmation(undefined);
      setSubmitting(false);
      return;
    }
    return () => {
      const request = cancellationRequest.current;
      cancellationRequest.current = null;
      if (request?.deadline !== undefined) clearTimeout(request.deadline);
      request?.controller.abort();
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const document = dialog.current?.ownerDocument;
    if (!document) return;
    let mounted = true;
    let scopeUnavailable = false;
    let poll: ReturnType<typeof setTimeout> | undefined;
    let request: RosterRequest | undefined;
    const visible = () => document.visibilityState !== 'hidden';
    const stop = () => {
      if (poll !== undefined) clearTimeout(poll);
      poll = undefined;
      if (request?.deadline !== undefined) clearTimeout(request.deadline);
      request?.controller.abort();
      request = undefined;
    };
    const schedule = () => {
      if (mounted && visible() && !scopeUnavailable) poll = setTimeout(() => {
        poll = undefined;
        void load();
      }, POLL_INTERVAL_MS);
    };
    const load = async () => {
      if (!mounted || !visible() || request) return;
      const owner: RosterRequest = { controller: new AbortController() };
      request = owner;
      setLoading(true);
      owner.deadline = setTimeout(() => {
        if (request !== owner) return;
        owner.controller.abort();
        request = undefined;
        setLoading(false);
        setError('read');
        schedule();
      }, REQUEST_TIMEOUT_MS);
      try {
        const current = await fetchWorkshopJobs(owner.controller.signal);
        if (request !== owner || !mounted || !visible()) return;
        scopeUnavailable = false;
        setRoster(current);
        setError(undefined);
        const batch = nextWorkshopJobObservations(current.jobs, observationCursor.current);
        observationCursor.current = batch.cursor;
        if (batch.jobs.length) {
          const observations = await Promise.allSettled(batch.jobs.map(job => observeWorkshopJob(job, owner.controller.signal)));
          if (request !== owner || !mounted || !visible()) return;
          setObservationIncomplete(observations.some(observation => observation.status === 'rejected'));
          const latest = await fetchWorkshopJobs(owner.controller.signal);
          if (request !== owner || !mounted || !visible()) return;
          setRoster(latest);
        } else setObservationIncomplete(false);
      } catch (failure) {
        if (request !== owner || !mounted || !visible()) return;
        scopeUnavailable = failure instanceof WorkshopJobsReadError && failure.scopeUnavailable;
        if (scopeUnavailable) setRoster(undefined);
        setError(scopeUnavailable ? 'scope' : 'read');
      } finally {
        if (request === owner) {
          if (owner.deadline !== undefined) clearTimeout(owner.deadline);
          request = undefined;
          setLoading(false);
          schedule();
        }
      }
    };
    const visibilityChanged = () => {
      stop();
      if (visible()) void load();
      else setLoading(false);
    };
    document.addEventListener('visibilitychange', visibilityChanged);
    void load();
    return () => {
      mounted = false;
      document.removeEventListener('visibilitychange', visibilityChanged);
      stop();
    };
  }, [open, refresh]);

  const selectedKey = confirmation && cancellationKey(confirmation.job);
  const selected = selectedKey ? roster?.jobs.find(job => cancellationKey(job) === selectedKey) : undefined;
  const canConfirm = !!(canOperate && confirmation && selected && !error &&
    cancellationAction(selected, cancelNotices.get(cancellationKey(selected))) === confirmation.mode);
  const confirm = async () => {
    if (!canConfirm || !selected || cancellationRequest.current) return;
    const key = cancellationKey(selected);
    const request: RosterRequest = { controller: new AbortController() };
    cancellationRequest.current = request;
    setSubmitting(true);
    const finish = (notice: CancelNotice) => {
      if (cancellationRequest.current !== request) return;
      cancellationRequest.current = null;
      if (request.deadline !== undefined) clearTimeout(request.deadline);
      setCancelNotices(previous => new Map(previous).set(key, notice));
      setSubmitting(false);
      setConfirmation(undefined);
      setRefresh(value => value + 1);
    };
    request.deadline = setTimeout(() => {
      request.controller.abort();
      finish({ problem: 'unconfirmed' });
    }, REQUEST_TIMEOUT_MS);
    try {
      finish({ receipt: await requestWorkshopJobCancellation(selected, request.controller.signal) });
    } catch (failure) {
      const rejected = failure instanceof WorkshopCancelRequestError && [400, 401, 403, 404, 409, 422].includes(failure.status);
      finish({ problem: rejected ? 'rejected' : 'unconfirmed' });
    }
  };

  if (!open) return null;
  return createPortal(<div className="workshop-jobs-overlay" onMouseDown={event => {
    if (event.target === event.currentTarget) closeDrawer();
  }}>
    <section ref={dialog} role="dialog" aria-modal="true" aria-label="工坊任务" className="workshop-jobs-drawer" tabIndex={-1}>
      <header>
        <div><h2>工坊任务</h2><p>当前 IP 的已保存任务记录</p></div>
        <button type="button" aria-label="关闭工坊任务" data-modal-autofocus disabled={submitting} onClick={closeDrawer}>×</button>
      </header>
      <div className="workshop-jobs-body" aria-busy={loading}>
        <button type="button" onClick={() => setRefresh(value => value + 1)} disabled={loading || submitting}>刷新任务列表</button>
        {loading && !roster && <p role="status">正在读取工坊任务…</p>}
        {error && <p role="status" data-workshop-jobs-error>
          {error === 'scope' ? '当前 IP 的任务记录不可用。' : roster ? '刷新失败，当前显示上次读取的记录。' : '工坊任务暂未读取，请重试。'}
        </p>}
        {roster && <>
          {observationIncomplete && <p role="status">部分运行状态暂未刷新，以下为已保存的记录。</p>}
          {roster.incomplete && <p role="status">部分任务记录无法核验。</p>}
          {!roster.jobs.length && !roster.incomplete && <p>当前 IP 暂无工坊任务。</p>}
          <ul>
            {roster.jobs.map(job => {
              const key = cancellationKey(job);
              const notice = cancelNotices.get(key);
              const receipt = currentCancellation(job, notice);
              const label = cancellationLabel(receipt, notice);
              const mode = canOperate && !error ? cancellationAction(job, notice) : undefined;
              const active = job.status === 'queued' || job.status === 'running' || job.status === 'stopping';
              const status = receipt?.status === 'cancelled' ? 'cancelled'
                : active && receipt?.status === 'pending' ? 'stopping'
                  : active && receipt?.status === 'unconfirmed' ? 'unconfirmed'
                    : active && receipt?.status === 'already_finished' && receipt.jobStatus === 'failed' ? 'failed' : job.status;
              return <li key={key} data-workshop-job={job.id}>
                <div className="workshop-jobs-row-title">
                  <strong>{job.title}</strong>
                  <span data-workshop-job-status={status}>{WORKSHOP_JOB_LABELS[status]}</span>
                </div>
                <p>任务 ID <code>{job.taskId ?? '未记录'}</code></p>
                <div className="workshop-jobs-progress">
                  {job.progress === null ? <span>进度未知</span> : <>
                    <progress value={job.progress} max={1} aria-label="任务进度" data-workshop-job-progress={job.progress} />
                    <span>{Math.round(job.progress * 100)}%</span>
                  </>}
                  <span>{job.elapsedMs === null ? workshopJobElapsed(job.elapsedMs) : `耗时 ${workshopJobElapsed(job.elapsedMs)}`}</span>
                </div>
                {label && <p role="status" data-workshop-cancel-result={receipt?.status ?? (notice && 'problem' in notice ? notice.problem : undefined)}>{label}</p>}
                {canOperate && !mode && !label && !job.canCancel && (active || job.status === 'unconfirmed') &&
                  <p>{job.taskId ? '服务端未提供此任务的取消操作。' : '缺少已确认的任务编号，无法取消。'}</p>}
                <div className="workshop-jobs-actions">
                  <button type="button" data-open-workshop-job disabled={submitting} onClick={() => {
                    if (cancellationRequest.current) return;
                    closeDrawer();
                    onOpenWorkshop(job.workshopProjectId, job.artifactSha256 ? job.artifactId ?? undefined : undefined);
                  }}>打开工坊</button>
                  {mode && <button type="button" data-cancel-workshop-job={mode} disabled={submitting}
                    onClick={() => setConfirmation({ job, mode })}>{mode === 'verify' ? '核对取消状态' : '取消任务'}</button>}
                </div>
              </li>;
            })}
          </ul>
        </>}
      </div>
    </section>
    {confirmation && <WorkshopCancelConfirmation confirmation={confirmation} pending={submitting} valid={canConfirm}
      onClose={closeConfirmation} onConfirm={() => void confirm()} />}
  </div>, document.body);
}
