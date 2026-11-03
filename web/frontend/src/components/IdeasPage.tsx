import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { fetchIdeas, createIdea, updateIdea, deleteIdea, createSchedule } from '../lib/api';
import type { Idea, IdeaInput } from '../lib/api';
import { IconIdea, IconEdit, IconTrash, IconChat, IconCalendar, IconChevron } from './icons';
import { useIpPermissions } from './ProjectSpace';

interface IdeasPageProps {
  onUseTopic: (title: string) => void;
  initialIdeaId?: string;
}

const COLUMNS: { key: string; label: string; color: string }[] = [
  { key: 'pending', label: '待做', color: 'var(--text-tertiary)' },
  { key: 'doing', label: '进行中', color: 'var(--layer-attribute)' },
  { key: 'done', label: '已完成', color: 'var(--layer-publish)' },
];
const NEXT: Record<string, string> = { pending: 'doing', doing: 'done', done: 'pending' };
const EMPTY: IdeaInput = { title: '', note: '', source: '', status: 'pending' };

export default function IdeasPage({ onUseTopic, initialIdeaId }: IdeasPageProps) {
  const { canOperate } = useIpPermissions();
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [form, setForm] = useState<IdeaInput | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [toast, setToast] = useState('');
  const [readState, setReadState] = useState<'loading' | 'ready' | 'error'>('loading');
  const loadSequence = useRef(0);
  const mounted = useRef(true);
  const targetCard = useRef<HTMLDivElement>(null);
  const requestedId = initialIdeaId || '';
  const targetId = /^[A-Za-z0-9_-]{1,160}$/.test(requestedId) ? requestedId : '';
  const unavailable = !!requestedId && (!targetId || readState === 'error'
    || (readState === 'ready' && !ideas.some(item => item.id === targetId)));

  const load = useCallback(() => {
    const sequence = ++loadSequence.current;
    setReadState('loading');
    fetchIdeas().then(items => {
      if (!mounted.current || sequence !== loadSequence.current) return;
      setIdeas(items); setReadState('ready');
    }).catch(() => {
      if (mounted.current && sequence === loadSequence.current) setReadState('error');
    });
  }, []);
  useEffect(() => {
    mounted.current = true; load();
    return () => { mounted.current = false; loadSequence.current += 1; };
  }, [load]);
  useEffect(() => { setForm(null); setEditId(null); }, [requestedId]);
  useEffect(() => {
    if (!targetId || unavailable || readState !== 'ready') return;
    targetCard.current?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    targetCard.current?.focus({ preventScroll: true });
  }, [targetId, unavailable, readState, ideas]);

  const showToast = (m: string) => { setToast(m); setTimeout(() => setToast(''), 2200); };
  const byStatus = useMemo(() => {
    const g: Record<string, Idea[]> = { pending: [], doing: [], done: [] };
    for (const it of ideas) (g[it.status] || g.pending).push(it);
    return g;
  }, [ideas]);

  const openNew = () => { if (!canOperate) return; setEditId(null); setForm({ ...EMPTY }); };
  const openEdit = (it: Idea) => { if (!canOperate) return; setEditId(it.id); setForm({ title: it.title, note: it.note, source: it.source, status: it.status }); };
  const save = async () => {
    if (!canOperate || !form || !form.title.trim()) return;
    if (editId) await updateIdea(editId, form); else await createIdea(form);
    setForm(null); setEditId(null); load();
  };
  const advance = async (it: Idea) => { if (!canOperate) return; await updateIdea(it.id, { ...it, status: NEXT[it.status] }); load(); };
  const remove = async (it: Idea) => { if (!canOperate) return; await deleteIdea(it.id); load(); };
  const schedule = async (it: Idea) => {
    if (!canOperate) return;
    // 用本地日期（日历页按本地渲染）：toISOString 是 UTC，东八区 0-8 点会落到「昨天」
    const d = new Date();
    const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    await createSchedule({ title: it.title, date, platform: '', time: '', status: 'idea', note: it.note });
    showToast('已加入日历（今天）');
  };

  return (
    <div className="page-scroll ideas-page">
      <div className="page-head">
        <div>
          <h1 className="page-title"><IconIdea size={21} /> 选题库</h1>
          <p className="page-subtitle">攒住每一个灵感——从热点收藏或手动新增，推进到「做内容」再进日历。</p>
        </div>
        {canOperate && <button className="btn btn-sm btn-primary" onClick={openNew}>+ 新建选题</button>}
      </div>

      {!canOperate && <p className="page-subtitle" role="status">当前为查看者权限，可以浏览选题；编辑、创作与排期由 IP 的负责人和运营者处理。</p>}
      {unavailable && <p className="page-subtitle" role="alert">指定选题不可用，可能已删除或当前 IP 无权查看。请返回选题列表核对。</p>}
      {!!targetId && readState === 'loading' && <p className="page-subtitle" role="status">正在定位指定选题…</p>}
      {!requestedId && readState === 'error' && <p className="page-subtitle" role="alert">选题暂时不可用，请重新读取。</p>}

      <div className="kanban">
        {COLUMNS.map((col) => (
          <div key={col.key} className="kanban-col">
            <div className="kanban-col-head">
              <span className="kanban-dot" style={{ background: col.color }} />
              {col.label}<span className="kanban-count">{byStatus[col.key].length}</span>
            </div>
            <div className="kanban-list">
              {byStatus[col.key].length === 0 && <div className="kanban-empty">{canOperate ? '拖点选题进来吧' : '暂时没有选题'}</div>}
              {byStatus[col.key].map((it) => (
                <div key={it.id} className="card idea-card" data-idea-id={it.id}
                  data-idea-selected={it.id === targetId && !unavailable && readState === 'ready' ? true : undefined}
                  ref={it.id === targetId ? targetCard : undefined} tabIndex={it.id === targetId ? -1 : undefined}
                  aria-current={it.id === targetId && !unavailable && readState === 'ready' ? 'true' : undefined}
                  style={it.id === targetId && !unavailable && readState === 'ready' ? { outline: '2px solid var(--accent-end)', outlineOffset: 3 } : undefined}>
                  {canOperate && <div className="idea-card-actions">
                    <button className="session-act" title="编辑" onClick={() => openEdit(it)}><IconEdit size={13} /></button>
                    <button className="session-act danger" title="删除" onClick={() => remove(it)}><IconTrash size={13} /></button>
                  </div>}
                  <div className="idea-title">{it.title}</div>
                  {it.source && <span className="badge" style={{ marginTop: 6 }}>{it.source}</span>}
                  {it.note && <div className="idea-note">{it.note}</div>}
                  {canOperate && <div className="idea-foot">
                    <button className="idea-act" onClick={() => { if (canOperate) onUseTopic(it.title); }}><IconChat size={13} /> 做内容</button>
                    <button className="idea-act" onClick={() => schedule(it)}><IconCalendar size={13} /> 排期</button>
                    <button className="idea-act next" onClick={() => advance(it)} title="推进状态">
                      {COLUMNS.find((c) => c.key === NEXT[it.status])?.label} <IconChevron size={12} />
                    </button>
                  </div>}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {canOperate && form && (
        <div className="overlay" onClick={() => setForm(null)}>
          <div className="modal" style={{ width: 440, maxWidth: '100%' }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <h3 style={{ margin: 0 }}>{editId ? '编辑选题' : '新建选题'}</h3>
              <button className="icon-btn" onClick={() => setForm(null)}>×</button>
            </div>
            <label className="field-label">选题 *</label>
            <input className="field" value={form.title} autoFocus placeholder="想做的内容 / 角度"
              onChange={(e) => setForm({ ...form, title: e.target.value })} />
            <label className="field-label">备注 / 角度</label>
            <textarea className="field" style={{ minHeight: 70 }} value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })} />
            <label className="field-label">来源</label>
            <input className="field" value={form.source} placeholder="如：微博热搜 / 灵感"
              onChange={(e) => setForm({ ...form, source: e.target.value })} />
            <label className="field-label">状态</label>
            <div style={{ display: 'flex', gap: 7 }}>
              {COLUMNS.map((c) => (
                <button key={c.key} className={`chip ${form.status === c.key ? 'active' : ''}`}
                  onClick={() => setForm({ ...form, status: c.key })}>{c.label}</button>
              ))}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 18 }}>
              <button className="btn btn-sm" onClick={() => setForm(null)}>取消</button>
              <button className="btn btn-sm btn-primary" onClick={save} disabled={!form.title.trim()}>保存</button>
            </div>
          </div>
        </div>
      )}

      {toast && <div className="toast ok"><span className="toast-icon">✓</span>{toast}</div>}
    </div>
  );
}
