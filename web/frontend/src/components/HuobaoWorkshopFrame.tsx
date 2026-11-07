import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { fetchWorkshopProject, openHuobaoStudio } from '../lib/workshop';
import type { WorkshopProject, WorkshopReference } from '../lib/workshop';
import type { ChatQuestion } from '../lib/api';
import WorkshopReviewPanel from './WorkshopReviewPanel';
import { recordPerformance, type PerformanceMetric } from '../lib/performance';
const Questions = lazy(() => import('./QuestionCards'));
interface Props {
  project: WorkshopProject; initialView?: 'production' | 'review'; initialArtifactId?: string;
  onUpdate: (project: WorkshopProject) => void; onBack: () => void;
  onUseWorkshop?: (reference: WorkshopReference) => void | Promise<void>;
  onOpenChat?: (reference: WorkshopReference) => void | Promise<void>;
  presentation?: 'contained' | 'fullscreen';
}
export default function HuobaoWorkshopFrame({ project, initialView, initialArtifactId, onUpdate, onBack, onUseWorkshop, onOpenChat, presentation = 'fullscreen' }: Props) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [target, setTarget] = useState<{ id: string; url: string }>();
  const [error, setError] = useState('');
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [review, setReview] = useState(initialView === 'review');
  const [question, setQuestion] = useState<{ session: string; questions: ChatQuestion[] }>();
  const current = useRef(project); current.current = project;
  const openChat = useRef(onOpenChat); openChat.current = onOpenChat;
  useEffect(() => {
    if (initialView === 'review') return;
    const controller = new AbortController();
    setTarget(undefined); setError('');
    void openHuobaoStudio(current.current, controller.signal).then(result => {
      if (!controller.signal.aborted) setTarget({ id: project.id, url: result.url });
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '火宝工作台未能连接'); });
    return () => controller.abort();
  }, [project.id, initialView, connectionAttempt]);
  useEffect(() => {
    const controller = new AbortController();
    const receive = async (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== frame.current?.contentWindow || event.data?.project !== project.id) return;
      if (event.data.type === 'chirpiva-performance') {
        recordPerformance(event.data.metric as PerformanceMetric, event.data.valueMs,
          typeof event.data.label === 'string' ? event.data.label : 'workshop');
        return;
      }
      if (event.data.type !== 'xiangya-huobao') return;
      if (event.data.action === 'back') { onBack(); return; }
      if (event.data.action === 'job') {
        const job = event.data.job;
        setQuestion(job?.questions?.length ? { session: job.session_id, questions: job.questions } : undefined);
        return;
      }
      if (!['review', 'chat'].includes(event.data.action)) return;
      try {
        const fresh = await fetchWorkshopProject(project.id, controller.signal);
        if (controller.signal.aborted) return;
        onUpdate(fresh);
        if (event.data.action === 'review') setReview(true);
        else await openChat.current?.({ id: fresh.id, revision: fresh.revision });
      } catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '无法读取当前工坊'); }
    };
    window.addEventListener('message', receive);
    return () => { controller.abort(); window.removeEventListener('message', receive); };
  }, [project.id, onBack, onUpdate]);
  return <section className={`huobao-workshop-shell${presentation === 'fullscreen' ? ' huobao-workshop-shell--fullscreen' : ''}`} data-workshop-project-id={project.id} aria-label="视频制作工作台">
    <div className="huobao-host-toolbar"><button className="btn btn-sm" onClick={onBack}>返回视频列表</button><span title={project.title}>{project.title}</span></div>
    <div className="huobao-workshop-content">
    {target?.id === project.id ? <iframe ref={frame} src={target.url} title="火宝原版视频制作工作台"
      allow="clipboard-write; fullscreen" /> : !review && <div className="huobao-loading"><h2>{error || '正在打开视频制作工作台…'}</h2>
      {error && <button className="btn btn-sm" onClick={() => setConnectionAttempt(attempt => attempt + 1)}>重新连接</button>}</div>}
    {error && target && <div className="workshop-error" role="alert">{error}</div>}
    {review && <div className="huobao-review-overlay native-workshop" role="dialog" aria-modal="true" aria-label="成片审核与发布">
      <header className="workshop-section-title"><h2>{project.title} · 成片审核</h2><div className="workshop-material-review-actions">
        <button className="btn btn-sm" onClick={() => { if (target) setReview(false); else onBack(); }}>返回制作</button></div></header>
      {error && <p className="workshop-error" role="alert">{error}</p>}
      <WorkshopReviewPanel project={project} onUpdate={onUpdate} onUseWorkshop={onUseWorkshop} initialArtifactId={initialArtifactId} />
    </div>}
    {question && <div className="huobao-question-overlay" role="dialog" aria-modal="true" aria-label="AI 制作确认"><div className="workshop-panel">
      <Suspense fallback={<p>正在读取制作确认…</p>}><Questions sessionId={question.session} questions={question.questions} /></Suspense>
    </div></div>}
    </div>
  </section>;
}
