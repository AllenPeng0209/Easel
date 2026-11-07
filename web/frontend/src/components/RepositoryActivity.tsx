import { useEffect, useRef, useState } from 'react';
import { fetchProjectActivity } from '../lib/access';
import type { RepositoryEvent } from '../lib/access';

const ACTIONS: Record<string, string> = { 'project.create': '创建项目', 'project.update': '更新项目设置',
  'member.add': '添加成员', 'member.role_change': '调整成员角色', 'member.remove': '移除成员',
  'member.leave': '退出项目', 'ownership.transfer': '转移项目所有权', 'ownership.migrate': '迁移项目所有权' };

export default function RepositoryActivity({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<RepositoryEvent[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const version = useRef(0);
  const load = async (before?: number) => {
    const expected = version.current;
    setLoading(true); setError('');
    try {
      const result = await fetchProjectActivity(projectId, before);
      if (version.current !== expected) return;
      setItems(previous => before ? [...previous, ...result.items] : result.items); setCursor(result.next_cursor);
    } catch (error) { if (version.current === expected) setError(error instanceof Error ? error.message : '权限记录加载失败'); }
    finally { if (version.current === expected) setLoading(false); }
  };
  useEffect(() => { version.current += 1; setItems([]); setCursor(null); void load(); return () => { version.current += 1; }; }, [projectId]);
  return <section className="repository-activity"><div className="repository-section-heading"><h3>权限与项目变更记录</h3><button className="project-text-button" disabled={loading} onClick={() => { void load(); }}>刷新 ↻</button></div>
    <p className="access-field-help">仅 Owner 可查看。记录项目设置、成员授权、退出与所有权交接。</p>
    <ol>{items.map(item => <li key={item.id}><span className="repository-activity-dot" /><div><strong>{item.actor.display_name || item.actor.username || '系统'}<span>{ACTIONS[item.action] || '更新项目权限'}</span></strong>
      {item.target.id && <p>@{item.target.username || item.target.id}{typeof item.details.role === 'string' ? ` · ${item.details.role}` : ''}</p>}
      <time dateTime={new Date(item.at * 1000).toISOString()}>{new Date(item.at * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</time></div></li>)}</ol>
    {!items.length && !loading && !error && <p className="access-empty">暂时没有权限变更记录</p>}
    {loading && <p className="access-empty" role="status">正在加载记录…</p>}{error && <p role="alert" className="access-error">{error}</p>}
    {cursor && <button className="btn" disabled={loading} onClick={() => { void load(cursor); }}>加载更早的记录</button>}
  </section>;
}
