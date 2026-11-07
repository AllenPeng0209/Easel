import { projectFetch, projectUrl } from './projectScope';

export type PerformanceMetric = 'api' | 'fcp' | 'lcp' | 'interaction' | 'video_metadata' | 'video_first_frame' | 'video_stall' | 'job_accept' | 'job_complete';
type Metric = { metric: PerformanceMetric; label: string; value_ms: number; network: string; device: string };
const allowed = new Set(['api', 'fcp', 'lcp', 'interaction', 'video_metadata', 'video_first_frame', 'video_stall', 'job_accept', 'job_complete']);
const pending: Metric[] = [];
let started = false, lastLcp = 0;

export function recordPerformance(metric: PerformanceMetric, valueMs: number, label = 'document'): void {
  if (!started || !allowed.has(metric) || !Number.isFinite(valueMs) || valueMs < 0 || valueMs > 14400000
    || !/^[a-z][a-z0-9_.-]{0,63}$/.test(label)) return;
  const connection = typeof navigator === 'undefined' ? undefined
    : (navigator as Navigator & { connection?: { effectiveType?: string } }).connection;
  const network = connection?.effectiveType || 'unknown';
  pending.push({ metric, label, value_ms: Math.round(valueMs * 10) / 10,
    network: ['slow-2g', '2g', '3g', '4g'].includes(network) ? network : 'unknown',
    device: window.innerWidth < 768 ? 'mobile' : 'desktop' });
  if (pending.length > 80) pending.shift();
}

export async function flushPerformanceMetrics(): Promise<void> {
  if (lastLcp) { recordPerformance('lcp', lastLcp); lastLcp = 0; }
  try { if (!sessionStorage.getItem('easel_authenticated_user')) return; } catch { return; }
  if (!pending.length) return;
  const metrics = pending.splice(0, 30);
  const base = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '').replace(/\/(?:login|register)$/, '');
  try {
    await projectFetch(projectUrl(`${base}/api/performance/metrics`), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store',
      keepalive: true, body: JSON.stringify({ metrics }),
    });
  } catch { /* Measurements never delay a user operation or retry business work. */ }
}

export function startPerformanceMonitoring(): void {
  if (started || typeof window === 'undefined') return;
  started = true;
  let fcpRecorded = false;
  const paint = (entry: PerformanceEntry) => { if (entry.name === 'first-contentful-paint' && !fcpRecorded) { fcpRecorded = true; recordPerformance('fcp', entry.startTime); } };
  performance.getEntriesByType('paint').forEach(paint);
  if (typeof PerformanceObserver !== 'undefined') {
    const observe = (type: string, callback: (entry: PerformanceEntry) => void, extra = {}) => {
      try { new PerformanceObserver(list => list.getEntries().forEach(callback)).observe({ type, buffered: true, ...extra }); } catch { /* Browser support varies. */ }
    };
    observe('paint', paint);
    observe('largest-contentful-paint', entry => { lastLcp = entry.startTime; });
    observe('event', entry => {
      if (entry.name === 'click' || entry.name === 'keydown') recordPerformance('interaction', entry.duration);
    }, { durationThreshold: 40 });
  }
  setInterval(() => { void flushPerformanceMetrics(); }, 30000);
  window.addEventListener('pagehide', () => { void flushPerformanceMetrics(); });
}
