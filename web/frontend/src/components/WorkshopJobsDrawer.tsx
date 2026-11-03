/**
 * Read-only job roster adapted from DeepSeek Harness ui-jobs/JobListAction.tsx
 * @ ce7073d (MIT). Easel watches the current IP's stored render observations.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchWorkshopJobs, nextWorkshopJobObservations, observeWorkshopJob, WORKSHOP_JOB_LABELS, workshopJobElapsed, WorkshopJobsReadError,
  type WorkshopJobRoster } from '../lib/workshopJobs';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import './WorkshopJobsDrawer.css';

const POLL_INTERVAL_MS = 5_000;
const REQUEST_TIMEOUT_MS = 12_000;
type RosterRequest = { controller: AbortController; deadline?: ReturnType<typeof setTimeout> };

export default function WorkshopJobsDrawer({ open, onClose, onOpenWorkshop }: {
  open: boolean;
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
  useModalLayer(dialog, open, onClose);

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

  if (!open) return null;
  return createPortal(<div className="workshop-jobs-overlay" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section ref={dialog} role="dialog" aria-modal="true" aria-label="工坊任务" className="workshop-jobs-drawer" tabIndex={-1}>
      <header>
        <div><h2>工坊任务</h2><p>当前 IP 的已保存任务记录</p></div>
        <button type="button" aria-label="关闭工坊任务" data-modal-autofocus onClick={onClose}>×</button>
      </header>
      <div className="workshop-jobs-body" aria-busy={loading}>
        <button type="button" onClick={() => setRefresh(value => value + 1)} disabled={loading}>刷新任务列表</button>
        {loading && !roster && <p role="status">正在读取工坊任务…</p>}
        {error && <p role="status" data-workshop-jobs-error>
          {error === 'scope' ? '当前 IP 的任务记录不可用。' : roster ? '刷新失败，当前显示上次读取的记录。' : '工坊任务暂未读取，请重试。'}
        </p>}
        {roster && <>
          {observationIncomplete && <p role="status">部分运行状态暂未刷新，以下为已保存的记录。</p>}
          {roster.incomplete && <p role="status">部分任务记录无法核验。</p>}
          {!roster.jobs.length && !roster.incomplete && <p>当前 IP 暂无工坊任务。</p>}
          <ul>
            {roster.jobs.map(job => <li key={JSON.stringify([job.workshopProjectId, job.id])} data-workshop-job={job.id}>
              <div className="workshop-jobs-row-title">
                <strong>{job.title}</strong>
                <span data-workshop-job-status={job.status}>{WORKSHOP_JOB_LABELS[job.status]}</span>
              </div>
              <p>任务 ID <code>{job.taskId ?? '未记录'}</code></p>
              <div className="workshop-jobs-progress">
                {job.progress === null ? <span>进度未知</span> : <>
                  <progress value={job.progress} max={1} aria-label="任务进度" data-workshop-job-progress={job.progress} />
                  <span>{Math.round(job.progress * 100)}%</span>
                </>}
                <span>{job.elapsedMs === null ? workshopJobElapsed(job.elapsedMs) : `耗时 ${workshopJobElapsed(job.elapsedMs)}`}</span>
              </div>
              <button type="button" data-open-workshop-job onClick={() => {
                onClose();
                onOpenWorkshop(job.workshopProjectId, job.artifactSha256 ? job.artifactId ?? undefined : undefined);
              }}>打开工坊</button>
            </li>)}
          </ul>
        </>}
      </div>
    </section>
  </div>, document.body);
}
