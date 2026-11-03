import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchTrends, fetchIdeas, createIdea } from '../lib/api';
import type { TrendGroup, TrendItem } from '../lib/api';
import { IconFire, IconRefresh, IconBookmark, IconCheck } from './icons';
import { useIpPermissions } from './ProjectSpace';

interface TrendsPageProps {
  onUseSavedIdea: (id: string) => void | Promise<void>;
}

const ALL_PLATFORMS: { key: string; label: string }[] = [
  { key: 'weibo', label: '微博' },
  { key: 'douyin', label: '抖音' },
  { key: 'zhihu', label: '知乎' },
  { key: 'bilibili', label: 'B站' },
  { key: 'baidu', label: '百度' },
  { key: 'toutiao', label: '头条' },
];

const trendKey = (platform: string, url: string, title: string) => JSON.stringify([platform, url, title.trim()]);

export default function TrendsPage({ onUseSavedIdea }: TrendsPageProps) {
  const { canOperate } = useIpPermissions();
  const [selected, setSelected] = useState<string[]>(['weibo', 'douyin', 'zhihu']);
  const [groups, setGroups] = useState<TrendGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [updated, setUpdated] = useState(0);
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
    if (pfs.length === 0) { setGroups([]); return; }
    setLoading(true);
    setError('');
    fetchTrends(pfs.join(','), 15)
      .then((d) => { setGroups(d.trends); setUpdated(d.updated); })
      .catch(() => setError('热点拉取失败——请确认已配置外网代理（EASEL_PROXY）。'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(selected); }, [load, selected]);

  const toggle = (k: string) =>
    setSelected((prev) => prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]);

  return (
    <div className="page-scroll trends-page">
      <div className="page-head">
        <div>
          <h1 className="page-title"><IconFire size={22} /> 热点雷达</h1>
          <p className="page-subtitle">
            多平台实时热搜，挑值得蹭的选题，一键交给 AI 做成你的内容。
            {updated > 0 && <span style={{ color: 'var(--text-tertiary)' }}> · {new Date(updated * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} 更新</span>}
          </p>
        </div>
        <button className="btn btn-sm" onClick={() => load(selected)} disabled={loading}>
          <IconRefresh size={14} /> {loading ? '刷新中…' : '刷新'}
        </button>
      </div>

      <div className="trend-platforms">
        {ALL_PLATFORMS.map((p) => (
          <button key={p.key} className={`chip ${selected.includes(p.key) ? 'active' : ''}`}
            onClick={() => toggle(p.key)}>{p.label}</button>
        ))}
      </div>

      {error && <div className="notice-error">{error}</div>}
      {saveError && <div className="notice-error" role="alert">{saveError}</div>}
      {canOperate && catalog === 'failed' && <div className="notice-error" role="alert">
        读取已保存选题失败。 <button className="btn btn-sm" onClick={() => setLookupAttempt(value => value + 1)}>重试</button>
      </div>}

      <div className="trend-grid">
        {groups.map((g) => (
          <div key={g.platform} className="card trend-col">
            <div className="trend-col-head">{g.label}<span className="trend-count">{g.items.length}</span></div>
            <div className="trend-list">
              {g.items.length === 0 && !loading && (
                <div className="trend-empty">
                  {g.ok === false ? '该平台暂时取不到（数据源不可用）' : '暂无数据'}
                </div>
              )}
              {g.items.map((it, i) => {
                const key = trendKey(g.platform, it.url, it.title);
                const isSaved = saved.current.has(key);
                const busy = catalog !== 'ready' || busyKeys.has(key);
                return <div key={i} className="trend-item">
                  <span className={`trend-rank ${i < 3 ? 'top' : ''}`}>{i + 1}</span>
                  <div className="trend-main">
                    <a className="trend-title" href={it.url || undefined} target="_blank" rel="noreferrer"
                      title={it.title}>{it.title}</a>
                    {it.hot && <span className="trend-hot">{it.hot}</span>}
                  </div>
                  {canOperate && <><button className="trend-save" title={isSaved ? '已收藏到选题库' : '收藏到选题库'}
                    disabled={busy || isSaved} onClick={() => void save(g, it, false)}>
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
    </div>
  );
}
