import { useState, useEffect, useRef } from 'react';
import type { ComponentType } from 'react';
import { fetchPersonas, fetchPersonaFiles, savePersonaFile, deletePersona, fetchAccountAnalytics } from '../lib/api';
import type { PersonaItem, PersonaFile, AccountAnalytics } from '../lib/api';
import { renderMarkdown } from '../lib/sanitize';
import { IconProfile, IconPlus, IconSkills, IconInteractions, IconLayout, IconBookmark, IconIdea, IconEdit, IconTrash, IconChevron, IconCheck, IconChat, IconFile } from './icons';
import { useIpPermissions } from './ProjectSpace';
import '../styles/profile.css';

// 粉丝量级：把粉丝数映射成人话档位（画像里“粉丝量级”一栏要的是量级而非精确值）
function fanTier(n: number): string {
  if (n <= 0) return '新号 / 0';
  if (n < 500) return '冷启动 <500';
  if (n < 1000) return '数百';
  if (n < 5000) return '千级';
  if (n < 10000) return '数千';
  if (n < 100000) return '万级';
  if (n < 1000000) return '十万级';
  return '百万+';
}

// 用注释围栏包住自动抓取段，重复抓取只替换该段、不动用户手写内容（幂等）
const WX_START = '<!-- wechat-oa:auto:start -->';
const WX_END = '<!-- wechat-oa:auto:end -->';

function buildWechatBlock(a: AccountAnalytics): string {
  const fans = a.followers ?? 0;
  const posts = a.posts ?? 0;
  const reads = a.likes ?? 0;
  const tops = [...new Set(a.notes.map((n) => n.title).filter(Boolean))].slice(0, 3);
  const stamp = new Date().toLocaleString('zh-CN', { hour12: false });
  const lines = [
    `### 微信公众号（自动抓取 · ${stamp}）`,
    '',
    `- **粉丝量级**：${fans}（${fanTier(fans)}）`,
    `- **内容形式**：图文推文（公众号文章）；已发表 ${posts} 篇${reads ? `，累计阅读 ${reads}` : ''}`,
  ];
  if (a.metrics?.length) {
    lines.push(`- **后台数据**：${a.metrics.map((m) => `${m.label} ${m.value}`).join(' · ')}`);
  }
  if (tops.length) lines.push(`- **代表内容**：${tops.join('｜')}`);
  return lines.join('\n');
}

function mergeWechatBlock(existing: string, block: string): string {
  const wrapped = `${WX_START}\n${block}\n${WX_END}`;
  const s = existing.indexOf(WX_START);
  const e = existing.indexOf(WX_END);
  if (s !== -1 && e !== -1 && e > s) {
    return existing.slice(0, s) + wrapped + existing.slice(e + WX_END.length);
  }
  const head = existing.replace(/\s+$/, '');
  return head ? `${head}\n\n${wrapped}` : wrapped;
}

interface ProfilePageProps {
  persona: string;
  onUsePersona: (name: string) => void;
  onNewProfile: () => void;
  onDeleted: (name: string) => void;
}

const DIM_META: Record<string, { label: string; Icon: ComponentType<{ size?: number }>; description: string }> = {
  'identity.md': { label: '身份定位', Icon: IconProfile, description: '账号定位、个人特色与创作方向' },
  'style.md': { label: '内容风格', Icon: IconSkills, description: '语气、表达方式与视觉偏好' },
  'audience.md': { label: '目标受众', Icon: IconInteractions, description: '为谁创作，回应哪些需求' },
  'platforms.md': { label: '平台运营', Icon: IconLayout, description: '各个平台的账号与内容侧重' },
  'preferences.md': { label: '偏好与红线', Icon: IconBookmark, description: '喜欢的内容，以及需要避开的边界' },
  'memory.md': { label: '经验沉淀', Icon: IconIdea, description: '积累复盘结论与有效的创作经验' },
};

function ProfileIllustration() {
  return <div className="profile-illustration" aria-hidden="true">
    <div className="profile-preview-sheet profile-preview-sheet-back" />
    <div className="profile-preview-sheet profile-preview-sheet-front">
      <div className="profile-preview-top"><span className="profile-preview-avatar"><IconProfile size={22} /></span>
        <span><strong>创作画像</strong><small>让表达更有自己的风格</small></span><IconCheck size={16} /></div>
      <div className="profile-preview-lines"><i /><i /><i /></div>
      <div className="profile-preview-tags"><span>定位</span><span>风格</span><span>受众</span></div>
      <div className="profile-preview-note"><IconSkills size={13} /><span>为每次创作提供方向</span></div>
    </div>
    <span className="profile-illustration-caption">你的风格，值得被记住</span>
  </div>;
}

function ProfileDimensionsGuide() {
  return <section className="profile-guide" aria-labelledby="profile-guide-title">
    <div className="profile-section-heading"><h2 id="profile-guide-title">画像里可以记录什么</h2><span>六个维度，逐步完善</span></div>
    <div className="profile-guide-grid">{Object.entries(DIM_META).map(([filename, meta]) => <article key={filename} aria-label={meta.label} className="profile-guide-item">
      <span className="profile-guide-icon"><meta.Icon size={17} /></span><div><h3>{meta.label}</h3><p>{meta.description}</p></div>
    </article>)}</div>
  </section>;
}

export default function ProfilePage(props: ProfilePageProps) {
  const { canOperate } = useIpPermissions();
  const operationPermission = useRef(canOperate);
  operationPermission.current = canOperate;
  const [viewedPersona, setViewedPersona] = useState(props.persona);
  const [personas, setPersonas] = useState<PersonaItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => { setViewedPersona(props.persona); }, [props.persona]);
  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError('');
    fetchPersonas().then((list) => {
      if (!ignore) setPersonas(list);
    }).catch(() => {
      if (!ignore) setError('加载画像列表失败，请重试。');
    }).finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [revision]);

  if (viewedPersona) {
    return <ProfileDetail key={viewedPersona} {...props} persona={viewedPersona}
      inUse={viewedPersona === props.persona}
      onBack={() => { setViewedPersona(''); setRevision((n) => n + 1); }}
      onDeleted={(name) => {
        setPersonas((list) => list.filter((p) => p.name !== name));
        setViewedPersona('');
        setRevision((n) => n + 1);
        props.onDeleted(name);
      }} />;
  }

  return (
    <div className="profile-page"><div className="profile-shell">
      <header className="profile-page-header">
        <div><span className="profile-eyebrow">内容生产</span><h1 className="profile-page-title">创作画像</h1>
          <p className="profile-page-description">{canOperate ? '把定位、风格和受众记下来，让 AI 更懂你的创作。' : '查看当前 IP 的定位、风格与受众资料。'}</p></div>
        {canOperate && !loading && !error && personas.length > 0 && <button className="btn profile-primary" onClick={() => { if (operationPermission.current) props.onNewProfile(); }}><IconPlus size={16} />新建画像</button>}
      </header>
      <div className="profile-conversation-meta"><span>当前对话</span><span className="profile-context-chip"><IconChat size={13} />{props.persona || '通用模式'}</span>
        <span className="profile-context-help">{canOperate ? '在 AI 对话中使用你的创作画像' : '当前权限：只读'}</span></div>
      {loading ? <div className="profile-feedback" role="status"><div className="spinner" />加载画像…</div>
        : error ? <div className="profile-feedback"><IconProfile size={24} /><p role="alert">{error}</p><button className="btn" onClick={() => setRevision((n) => n + 1)}>重新加载</button></div>
        : personas.length === 0 ? <>
          <section className="profile-zero" aria-labelledby="profile-zero-title">
            <div className="profile-zero-copy"><span className="profile-zero-kicker">{canOperate ? '从认识你开始' : '创作资料库'}</span>
              <h2 id="profile-zero-title">{canOperate ? '让 AI 更懂你的创作' : '此 IP 尚未创建画像'}</h2>
              <p>{canOperate ? '你是谁、为谁创作、喜欢怎样表达。把这些记成画像，为选题、脚本和文案提供一致的创作方向。' : '运营成员创建画像后，你可以在这里查看定位、风格和受众信息。'}</p>
              {canOperate && <div className="profile-zero-actions"><button className="btn profile-primary" onClick={() => { if (operationPermission.current) props.onNewProfile(); }}><IconPlus size={16} />新建画像</button><span>创建后可随时补充和调整</span></div>}
            </div><ProfileIllustration />
          </section><ProfileDimensionsGuide />
        </> : <section className="profile-library" aria-labelledby="profile-library-title">
          <div className="profile-section-heading"><h2 id="profile-library-title">全部画像 <span className="profile-count">{personas.length}</span></h2><span>{canOperate ? '选择适合这次创作的表达方式' : '当前 IP 的已保存画像'}</span></div>
          <div className="profile-grid">
            {personas.map((p) => <article className={`profile-summary ${p.name === props.persona ? 'is-current' : ''}`} key={p.name}>
              <div className="profile-summary-head"><span className="profile-avatar">{p.name.slice(0, 1)}</span>
                {p.name === props.persona && <span className="profile-current-badge"><IconCheck size={12} />当前使用</span>}</div>
              <h2>{p.name}</h2><p className="profile-summary-description">{p.description || '添加你的定位与内容方向，逐步建立自己的创作风格。'}</p>
              <div className="profile-summary-actions"><button className="btn profile-secondary" onClick={() => setViewedPersona(p.name)}>{canOperate ? '查看 / 编辑' : '查看'}<IconChevron size={13} /></button>
                {canOperate && <button className="btn profile-soft-action" disabled={p.name === props.persona} onClick={() => { if (operationPermission.current) props.onUsePersona(p.name); }}>{p.name === props.persona ? '正在使用' : '切换画像'}</button>}</div>
            </article>)}
          </div>
        </section>}
    </div></div>
  );
}

function ProfileDetail({ persona, onDeleted, onBack, onUsePersona, inUse }: ProfilePageProps & {
  onBack: () => void;
  inUse: boolean;
}) {
  const { canOperate } = useIpPermissions();
  const operationPermission = useRef(canOperate);
  operationPermission.current = canOperate;
  const [files, setFiles] = useState<PersonaFile[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [savingFile, setSavingFile] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState('');
  const [fetchingWx, setFetchingWx] = useState(false);

  useEffect(() => {
    if (!persona) { setFiles([]); return; }
    let ignore = false;   // 切 persona 丢弃旧请求（F2）
    setLoading(true);
    setError('');
    setEditing(false);
    fetchPersonaFiles(persona)
      .then((d) => {
        if (ignore) return;
        setFiles(d.files);
        setDrafts(Object.fromEntries(d.files.map((f) => [f.filename, f.content])));
      })
      .catch(() => { if (!ignore) setError('加载画像失败'); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [persona]);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 2500); };

  const handleSave = async (filename: string) => {
    if (!operationPermission.current) return;
    setSavingFile(filename);
    try {
      await savePersonaFile(persona, filename, drafts[filename] ?? '');
      setFiles((prev) => prev.map((f) => f.filename === filename ? { ...f, content: drafts[filename] ?? '' } : f));
      showToast(`已保存 ${DIM_META[filename]?.label || filename} ✓`);
    } catch (e) {
      showToast(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSavingFile('');
    }
  };

  // 「平台运营」一键抓取公众号数据：复用数据中心同一个 mp 后台会话接口，把粉丝量级/内容形式
  // 抓完“直接落盘”并刷新视图——不进编辑态、不用用户点保存（否则会露出原始 markdown 破坏观感）。
  const handleFetchWechat = async (filename: string) => {
    if (!operationPermission.current) return;
    setFetchingWx(true);
    try {
      const a = await fetchAccountAnalytics('wechat-oa');
      if (!operationPermission.current) return;
      if (!a.loggedIn) {
        showToast('公众号后台未登录：请先到「账号」页扫码登录后再抓取');
        return;
      }
      const base = files.find((f) => f.filename === filename)?.content ?? '';
      const merged = mergeWechatBlock(base, buildWechatBlock(a));
      await savePersonaFile(persona, filename, merged);
      // 同步更新 files（视图）与 drafts（若正在编辑也一致），无需手动保存
      setFiles((prev) => prev.map((f) => f.filename === filename ? { ...f, content: merged } : f));
      setDrafts((p) => ({ ...p, [filename]: merged }));
      showToast(`已抓取并更新画像（粉丝 ${a.followers ?? 0}，已发表 ${a.posts ?? 0} 篇）✓`);
    } catch (e) {
      showToast(e instanceof Error ? `抓取失败：${e.message}` : '抓取失败（可能未登录或平台改版）');
    } finally {
      setFetchingWx(false);
    }
  };

  const handleDelete = async () => {
    if (!operationPermission.current) return;
    if (!window.confirm(`确定删除画像「${persona}」吗？\n此操作不可恢复，将删除该画像的全部六维文件。`)) return;
    setDeleting(true);
    try {
      await deletePersona(persona);
      onDeleted(persona);
      showToast(`已删除画像「${persona}」`);
    } catch (e) {
      showToast(e instanceof Error ? e.message : '删除失败');
    } finally {
      setDeleting(false);
    }
  };


  return (
    <div className="profile-page profile-detail-page"><div className="profile-shell">
      <div className="profile-detail-nav">
        <button className="btn btn-sm" disabled={!!savingFile || fetchingWx || deleting} onClick={() => {
          if (files.some((f) => (drafts[f.filename] ?? '') !== f.content)
              && !window.confirm('还有未保存的修改，确定返回画像列表吗？')) return;
          onBack();
        }}>← 全部画像</button>
        {inUse ? <span className="badge badge-accent">当前对话使用中</span>
          : canOperate && <button className="btn btn-sm" onClick={() => { if (operationPermission.current) onUsePersona(persona); }}>切换画像</button>}
      </div>
      <div className="profile-page-header">
        <div>
          <span className="profile-eyebrow">创作画像</span><h1 className="profile-page-title">{persona}</h1>
          <p className="profile-page-description">{canOperate ? '六个维度构成一个完整人设，可随时编辑保存。' : '只读查看已保存的六维画像资料。'}</p>
        </div>
        {canOperate && <div className="profile-detail-actions">
          <button className={`btn ${editing ? 'profile-primary' : 'profile-secondary'}`} onClick={() => { if (operationPermission.current) setEditing((v) => !v); }}>
            <IconEdit size={15} />{editing ? '完成编辑' : '编辑资料'}
          </button>
          <button className="btn profile-delete-action"
            disabled={deleting} onClick={handleDelete}>
            <IconTrash size={14} />{deleting ? '删除中…' : '删除画像'}
          </button>
        </div>}
      </div>

      {error && <div style={{ color: 'var(--red)', fontSize: 14, marginTop: 12 }}>{error}</div>}

      {loading ? (
        <div className="loading"><div className="spinner" />加载中…</div>
      ) : (
        files.map((f) => {
          const meta = DIM_META[f.filename] || { label: f.filename, Icon: IconFile, description: '补充创作资料' };
          const dirty = editing && (drafts[f.filename] ?? '') !== f.content;
          return (
            <section key={f.filename} className="profile-dim" aria-label={meta.label}>
              <div className="profile-dim-head">
                <div className="profile-dim-title"><span className="profile-dim-icon"><meta.Icon size={18} /></span><span>{meta.label}<small>{meta.description}</small></span></div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {canOperate && f.filename === 'platforms.md' && (
                    <button className="btn btn-sm" disabled={fetchingWx}
                      title="用已登录的公众号后台会话抓取粉丝/内容数据，写入本栏（与数据中心同源）"
                      onClick={() => handleFetchWechat(f.filename)}>
                      {fetchingWx ? '抓取中…' : '抓取公众号数据'}
                    </button>
                  )}
                  {canOperate && editing && (
                    <button className="btn btn-sm profile-primary" disabled={!dirty || savingFile === f.filename}
                      onClick={() => handleSave(f.filename)}>
                      {savingFile === f.filename ? '保存中…' : dirty ? '保存' : '已保存'}
                    </button>
                  )}
                </div>
              </div>
              {canOperate && editing ? (
                <textarea
                  className="field profile-dimension-editor"
                  aria-label={`编辑${meta.label}`}
                  value={drafts[f.filename] ?? ''}
                  onChange={(e) => setDrafts((p) => ({ ...p, [f.filename]: e.target.value }))}
                />
              ) : (
                <div className="profile-dim-body">
                  <div className="profile-content"
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(f.content || '_（空）_') }} />
                </div>
              )}
            </section>
          );
        })
      )}

      {toast && <div className="toast ok"><span className="toast-icon">✓</span>{toast}</div>}
    </div></div>
  );
}
