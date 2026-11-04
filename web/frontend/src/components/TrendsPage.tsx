import { Component, useState, useEffect, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';
import { fetchTrendSources, fetchTrends, fetchIdeas, createIdea } from '../lib/api';
import type { TrendGroup, TrendSource, TrendItem } from '../lib/api';
import { IconFire, IconRefresh, IconBookmark, IconCheck } from './icons';
import TrendSourcesDialog from './TrendSourcesDialog';
import { useIpPermissions } from './ProjectSpace';

interface TrendsPageProps {
  onUseSavedIdea: (id: string) => void | Promise<void>;
}

class TrendBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <div className="page-scroll trends-page"><div className="notice-error" role="alert">热点页面暂时无法显示，其他工作台功能仍可使用。<button className="btn btn-sm" onClick={() => this.setState({ failed: false })}>重新加载热点</button></div></div> : this.props.children;
  }
}

const trendKey = (platform: string, url: string, title: string) => JSON.stringify([platform, url, title.trim()]);

export default function TrendsPage(props: TrendsPageProps) {
  return <TrendBoundary><TrendsPanel {...props} /></TrendBoundary>;
}

function TrendsPanel({ onUseSavedIdea }: TrendsPageProps) {
  const { canOperate } = useIpPermissions();
  const [sources, setSources] = useState<TrendSource[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [groups, setGroups] = useState<TrendGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [updated, setUpdated] = useState(0);
  const [managerOpen, setManagerOpen] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const requestId = useRef(0);
  const discoveryId = useRef(0);
  const initialized = useRef(false);

  const saved = useRef(new Map<string, string>());
  const pending = useRef(new Set<string>());
  const mounted = useRef(true);
  const [busyKeys, setBusyKeys] = useState(new Set<string>());
  const [catalog, setCatalog] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [lookupAttempt, setLookupAttempt] = useState(0);
  const [saveError, setSaveError] = useState('');

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!canOperate) return;
    let active = true;
    setCatalog('loading');
    fetchIdeas().then(ideas => {
      if (!active) return;
      const known = new Map<string, string>();
      for (const { id, title, provenance } of ideas) {
        if (!id || !provenance) continue;
        const key = trendKey(provenance.platform, provenance.url, title);
        if (!known.has(key)) known.set(key, id);
      }
      saved.current = known;
      setCatalog('ready');
    }).catch(() => { if (active) setCatalog('failed'); });
    return () => { active = false; };
  }, [canOperate, lookupAttempt]);

  const save = async (group: TrendGroup, item: TrendItem, start: boolean) => {
    const key = trendKey(group.platform, item.url, item.title);
    if (!canOperate || catalog !== 'ready' || pending.current.has(key)) return;
    pending.current.add(key);
    setBusyKeys(new Set(pending.current));
    setSaveError('');
    let id = saved.current.get(key);
    try {
      if (!id) {
        const idea = await createIdea({ title: item.title, source: `${group.label}热搜`, status: 'pending',
          provenance: { platform: group.platform, url: item.url, collectedAt: updated } });
        if (!idea.id) throw new Error('Missing saved idea ID');
        id = idea.id;
        saved.current.set(key, id);
      }
      if (start && mounted.current) await onUseSavedIdea(id);
    } catch {
      if (mounted.current) setSaveError(id ? '打开创作失败，请重试。' : '保存选题失败，请重试。');
    } finally {
      pending.current.delete(key);
      if (mounted.current) setBusyKeys(new Set(pending.current));
    }
  };

  const load = useCallback((pfs: string[]) => {
    const id = ++requestId.current;
    if (pfs.length === 0) { setGroups([]); setLoading(false); setError(''); return; }
    setGroups((prev) => prev.filter((g) => pfs.includes(g.platform)));
    setLoading(true);
    setError('');
    fetchTrends(pfs.join(','), 15)
      .then((d) => { if (requestId.current === id) { setGroups(d.trends); setUpdated(d.updated); setWarnings(d.warnings); } })
      .catch((e: unknown) => {
        if (requestId.current === id) setError(e instanceof Error ? e.message : '热点拉取失败，请稍后重试。');
      })
      .finally(() => { if (requestId.current === id) setLoading(false); });
  }, []);

  const discover = useCallback(() => {
    const id = ++discoveryId.current;
    requestId.current++;
    setReady(false);
    setLoading(false);
    setError('');
    return fetchTrendSources().then((d) => {
      if (discoveryId.current !== id) return;
      setSources(d.sources);
      setWarnings(d.warnings);
      const first = !initialized.current;
      initialized.current = true;
      setSelected((prev) => first ? d.sources.filter((s) => s.default).map((s) => s.id)
        : d.sources.filter((s) => prev.includes(s.id)).map((s) => s.id));
      setGroups((prev) => prev.filter((g) => d.sources.some((s) => s.id === g.platform)));
      setReady(true);
    }).catch((e: unknown) => {
      if (discoveryId.current === id) {
        setGroups([]);
        setSources([]);
        setError(e instanceof Error ? e.message : '无法加载热点数据源。');
      }
    });
  }, []);

  const invalidate = useCallback(() => { requestId.current++; discoveryId.current++; }, []);
  useEffect(() => { void discover(); return invalidate; }, [discover, invalidate]);
  useEffect(() => { if (ready) load(selected); }, [load, selected, ready]);
  useEffect(() => {
    if (!groups.some(group => group.error?.code === 'busy')) return;
    const timer = window.setTimeout(() => load(selected), 1500);
    return () => window.clearTimeout(timer);
  }, [groups, selected, load]);

  const toggle = (k: string) =>
    setSelected((prev) => prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]);

  return (
    <div className="page-scroll trends-page" aria-busy={loading}>
      <div className="page-head">
        <div>
          <h1 className="page-title"><IconFire size={22} /> 热点雷达</h1>
          <p className="page-subtitle">
            汇集平台热搜与自定义数据源，挑选题，一键交给 AI 做成你的内容。
            {updated > 0 && <span style={{ color: 'var(--text-tertiary)' }}> · {new Date(updated * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 查询</span>}
          </p>
        </div>
        <div className="trend-actions"><button className="btn btn-sm" onClick={() => setManagerOpen(true)}>数据源</button>
        <button className="btn btn-sm" onClick={() => { void discover(); }} disabled={loading}>
          <IconRefresh size={14} /> {loading ? '刷新中…' : '刷新'}
        </button></div>
      </div>

      <div className="trend-platforms">
        {sources.map((p) => (
          <button key={p.id} className={`chip ${selected.includes(p.id) ? 'active' : ''}`}
            aria-pressed={selected.includes(p.id)} onClick={() => toggle(p.id)}>{p.label}</button>
        ))}
      </div>

      {error && <div className="notice-error" role="alert">{error}</div>}
      {saveError && <div className="notice-error" role="alert">{saveError}</div>}
      {warnings.map((warning, i) => <div className="notice-error" role="status" key={i}>{warning}</div>)}
      {!ready && !error && <div className="trend-platforms" role="status">正在加载数据源…</div>}
      {ready && sources.length === 0 && <div className="trend-platforms">尚未启用数据源，请在本地热点数据源配置中添加或启用。</div>}
      {ready && sources.length > 0 && selected.length === 0 && <div className="trend-platforms">请选择上方的数据源查看热点。</div>}
      {loading && <div className="trend-platforms" role="status">正在获取热点…</div>}
      {canOperate && catalog === 'failed' && <div className="notice-error" role="alert">
        读取已保存选题失败。 <button className="btn btn-sm" onClick={() => setLookupAttempt(value => value + 1)}>重试</button>
      </div>}

      <div className="trend-grid">
        {groups.map((g) => (
          <div key={g.platform} className="card trend-col">
            <div className="trend-col-head">{g.label}<span className="trend-count">{g.items.length}</span></div>
            <div className="trend-source-status">
              {g.fetched_at && <span>采集于 {new Date(g.fetched_at).toLocaleString('zh-CN')}</span>}
              {g.stale && <span> · 缓存已过期</span>}
              {g.error && <div role="status">{g.error.message}{g.stale ? '，暂用上次结果' : ''}</div>}
            </div>
            <div className="trend-list">
              {g.items.length === 0 && !loading && (
                <div className="trend-empty">
                  {!g.ok ? '该数据源暂时不可用' : '采集成功，暂无热点'}
                </div>
              )}
              {g.items.map((it, i) => {
                const key = trendKey(g.platform, it.url, it.title);
                const isSaved = saved.current.has(key);
                const busy = catalog !== 'ready' || busyKeys.has(key);
                return <div key={it.id} className="trend-item">
                  <span className={`trend-rank ${i < 3 ? 'top' : ''}`}>{i + 1}</span>
                  <div className="trend-main">
                    <a className="trend-title" href={it.url || undefined} target="_blank" rel="noreferrer"
                      title={it.title}>{it.title}</a>
                    {it.hot && <span className="trend-hot">{it.hot}</span>}
                  </div>
                  {canOperate && <><button className="trend-save" title={isSaved ? '已收藏到选题库' : '收藏到选题库'}
                    aria-label={`收藏选题：${it.title}`} disabled={busy || isSaved} onClick={() => void save(g, it, false)}>
                    {isSaved ? <IconCheck size={14} /> : <IconBookmark size={14} />}
                  </button>
                  <button className="trend-use" disabled={busy} onClick={() => void save(g, it, true)}>
                    {isSaved ? '做内容' : '保存并做内容'}
                  </button></>}
                </div>;
              })}
            </div>
          </div>
        ))}
      </div>
      {managerOpen && <TrendSourcesDialog onClose={() => setManagerOpen(false)} onChanged={addedId => {
        void discover().then(() => { if (addedId) setSelected(prev => [...new Set([...prev, addedId])]); });
      }} />}
    </div>
  );
}
