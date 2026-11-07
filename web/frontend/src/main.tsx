import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import AuthGate from './components/AuthGate'
import type { User } from './lib/access'
import ErrorBoundary from './components/ErrorBoundary.tsx'
import { startPerformanceMonitoring } from './lib/performance'

const App = lazy(() => import('./App.tsx'));
const ProjectSpace = lazy(() => import('./components/ProjectSpace'));
startPerformanceMonitoring();


const BUILD_ID = 'performance-20261107';
// 打到控制台，便于确认浏览器加载的是最新前端（排查缓存旧包）
console.log(`%cEasel build: ${BUILD_ID}`, 'color:#8b5cf6;font-weight:bold');


function preserveLegacyData(user: User | null) {
  if (!user?.is_admin) return;
  try {
    const owner = localStorage.getItem('easel_legacy_history_owner');
    if (owner && owner !== user.id) return;
    const previousBrand = ['post', 'craft'].join('');
    const scoped = /^(easel_(?:sessions|active_session|publish_draft|onboarding_seen|whoami_v4|analytics|last_project)|easel_project:[^:]+:easel_)/;
    for (const original of Object.keys(localStorage)) {
      const key = original.startsWith(previousBrand + '_') ? 'easel_' + original.slice(previousBrand.length + 1) : original;
      if (!scoped.test(key)) continue;
      const target = `easel_user:${user.id}:${key}`;
      if (localStorage.getItem(target) === null) localStorage.setItem(target, localStorage.getItem(original) || '');
      localStorage.removeItem(original);
    }
    localStorage.setItem('easel_legacy_history_owner', user.id);
  } catch { /* AuthGate separately refuses unsafe identity switching if storage is unavailable. */ }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <AuthGate onAuthenticated={preserveLegacyData}>
        <Suspense fallback={<div className="dash-empty" role="status">正在打开工作空间…</div>}>
          <ProjectSpace><App /></ProjectSpace>
        </Suspense>
      </AuthGate>
    </ErrorBoundary>
  </StrictMode>,
)
