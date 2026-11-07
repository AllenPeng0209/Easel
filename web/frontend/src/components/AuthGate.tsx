import { createContext, Fragment, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  AccessError, createUser, fetchAuthBootstrap, fetchAuthUser, fetchUsers,
  loginAccount, logoutAccount, patchUser, registerAccount, setupAccount,
} from '../lib/access';
import type { User } from '../lib/access';
import LandingPage from './LandingPage';
import '../styles/access.css';

export interface AuthState {
  user: User;
  logout: () => Promise<void>;
  refresh: () => Promise<User | null>;
}

// The gate and its public hook share one session lifecycle; HMR may reload this boundary.
// oxlint-disable-next-line react/only-export-components
export const AuthContext = createContext<AuthState | null>(null);
// oxlint-disable-next-line react/only-export-components
export function useAuth(): AuthState | null { return useContext(AuthContext); }

const USER_MARKER = 'easel_authenticated_user';
const PAGE_PATH = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '');
const AUTH_PAGE = PAGE_PATH.endsWith('/register') ? 'register' : PAGE_PATH.endsWith('/login') ? 'login' : null;
const PUBLIC_BASE = AUTH_PAGE ? PAGE_PATH.slice(0, -(AUTH_PAGE.length + 1)) : PAGE_PATH;

function marker(): string | null {
  try { return sessionStorage.getItem(USER_MARKER); } catch { return null; }
}

function clearMarker(): void {
  try { sessionStorage.removeItem(USER_MARKER); } catch { /* cookie remains the identity source */ }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作未完成，请重试';
}

export default function AuthGate({ children, onAuthenticated }: {
  children: ReactNode;
  onAuthenticated?: (user: User | null) => void;
}) {
  const [phase, setPhase] = useState<'loading' | 'setup' | 'login' | 'register' | 'ready'>('loading');
  const [user, setUser] = useState<User | null>(null);
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const callback = useRef(onAuthenticated);
  callback.current = onAuthenticated;
  const channel = useRef<BroadcastChannel | null>(null);
  const validating = useRef<Promise<User | null> | null>(null);
  const generation = useRef(0);
  const reloadRequired = useRef(false);
  const wasAuthenticated = useRef(false);

  const signedOut = useCallback(() => {
    generation.current += 1;
    setUser(null);
    setPassword('');
    setConfirmPassword('');
    setPhase(AUTH_PAGE || 'login');
    clearMarker();
    callback.current?.(null);
  }, []);

  const accepted = useCallback((account: User): boolean => {
    if (!account.active) { signedOut(); return false; }
    callback.current?.(account);
    if (AUTH_PAGE) {
      setUser(null); setPhase('loading');
      try {
        sessionStorage.setItem(USER_MARKER, account.id);
        window.location.replace(`${PUBLIC_BASE}/#projects`);
      } catch { setError('无法切换用户工作空间，请允许会话存储并刷新页面后重试。'); }
      return false;
    }
    if (reloadRequired.current || marker() !== account.id) {
      // This marker only selects local draft namespaces. Authentication stays in
      // the server's HttpOnly cookie, and a reload discards the previous user's UI.
      reloadRequired.current = true;
      setUser(null);
      setPhase('loading');
      try {
        sessionStorage.setItem(USER_MARKER, account.id);
        window.location.reload();
      } catch {
        setError('无法切换用户工作空间，请允许会话存储并刷新页面后重试。');
      }
      return false;
    }
    setUser(account);
    wasAuthenticated.current = true;
    setPassword('');
    setConfirmPassword('');
    setError('');
    setPhase('ready');
    return true;
  }, [signedOut]);

  const refresh = useCallback((): Promise<User | null> => {
    if (validating.current) return validating.current;
    const version = generation.current;
    const request = fetchAuthUser().then(account => {
      if (version === generation.current) accepted(account);
      return account;
    }).catch(error => {
      if (error instanceof AccessError && error.status === 401) {
        signedOut();
        if (wasAuthenticated.current) window.location.assign(`${PUBLIC_BASE}/login/`);
        return null;
      }
      throw error;
    }).finally(() => { if (validating.current === request) validating.current = null; });
    validating.current = request;
    return request;
  }, [accepted, signedOut]);

  const bootstrap = useCallback(async () => {
    setPhase('loading');
    setError('');
    const version = generation.current;
    try {
      const status = await fetchAuthBootstrap();
      if (version !== generation.current) return;
      if (!status.configured) { setPhase('setup'); return; }
      if ('user' in status) {
        if (status.user) accepted(status.user);
        else signedOut();
      } else await refresh();
    } catch (error) {
      if (version === generation.current) setError(errorMessage(error));
    }
  }, [refresh, accepted, signedOut]);

  useEffect(() => { void bootstrap(); }, [bootstrap]);

  useEffect(() => {
    const expired = (event: Event) => {
      const expected = (event as CustomEvent<{ userId?: string | null }>).detail?.userId;
      if (expected && marker() && expected !== marker()) return;
      signedOut(); setError('登录已失效，请重新登录'); window.location.assign(`${PUBLIC_BASE}/login/`);
    };
    window.addEventListener('easel:auth-required', expired);
    return () => window.removeEventListener('easel:auth-required', expired);
  }, [signedOut]);

  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return;
    const current = new BroadcastChannel('easel-auth');
    channel.current = current;
    current.onmessage = () => {
      // Cookies are shared by tabs. Hide the old space before revalidating a new login.
      generation.current += 1;
      setUser(null);
      setPhase('loading');
      validating.current = null;
      void refresh().catch(error => { setError(errorMessage(error)); setPhase('login'); });
    };
    return () => { channel.current = null; current.close(); };
  }, [refresh]);

  useEffect(() => {
    if (phase !== 'ready') return;
    const verify = () => { void refresh().catch(() => { /* transient network failure keeps the current screen */ }); };
    window.addEventListener('focus', verify);
    const timer = setInterval(verify, 60_000);
    return () => { window.removeEventListener('focus', verify); clearInterval(timer); };
  }, [phase, refresh]);

  const logout = useCallback(async () => {
    try { await logoutAccount(); }
    catch (error) { if (!(error instanceof AccessError) || error.status !== 401) throw error; }
    wasAuthenticated.current = false; signedOut();
    channel.current?.postMessage({ type: 'changed' });
    window.location.reload();
  }, [signedOut]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !username.trim() || !password || (phase !== 'login' && password.length < 10)) return;
    if (phase === 'register' && password !== confirmPassword) { setError('两次输入的密码不一致'); return; }
    setBusy(true); setError('');
    const version = generation.current;
    try {
      const result = phase === 'setup'
        ? await setupAccount({ username: username.trim(), display_name: displayName.trim(), password })
        : phase === 'register'
          ? await registerAccount({ username: username.trim(), display_name: displayName.trim(), password })
          : await loginAccount({ username: username.trim(), password });
      if (version !== generation.current) return;
      channel.current?.postMessage({ type: 'changed' });
      if (!AUTH_PAGE) window.location.hash = '#projects';
      accepted(result.user);
    } catch (error) { if (version === generation.current) setError(errorMessage(error)); }
    finally { setBusy(false); }
  };

  if (phase === 'ready' && user) return <AuthContext.Provider value={{ user, logout, refresh }}>
    <Fragment key={user.id}>{children}</Fragment>
  </AuthContext.Provider>;

  const registering = phase === 'register';
  const openingWorkspace = phase === 'loading' && /^(?:#workspace|#projects|#\/business\/)/.test(window.location.hash || '');
  if (!AUTH_PAGE && !openingWorkspace && phase !== 'setup' && !(phase === 'loading' && error)) return <LandingPage />;
  return <main className="auth-screen auth-landing">
    <header className="auth-site-header"><a className="chirpiva-wordmark" href={`${PUBLIC_BASE}/`} aria-label="Chirpiva 响芽首页"><span className="chirpiva-mark" aria-hidden="true">c</span><strong>Chirpiva<span>响芽</span></strong></a>
      <a className="auth-back-home" href={`${PUBLIC_BASE}/`}>← 返回官网</a></header>
    <div className="auth-landing-grid">
    <section className="auth-story" aria-labelledby="auth-story-title">
      <div className="auth-story-eyebrow"><span />YOUR IP. YOUR SPACE.</div>
      <h1 id="auth-story-title">让好内容生长，<br /><em>让你的 IP 发声。</em></h1>
      <p className="auth-story-description">从一个想法，到一个持续生长的品牌。<br />把 AI 创作、内容发布和团队协作，放进属于你的 IP 项目。</p>
      <div className="auth-project-preview" aria-label="IP 项目协作示意">
        <div className="auth-preview-head"><span className="auth-preview-icon" aria-hidden="true">✳</span><div><small>你的名字 /</small><strong>下一个好 IP</strong></div><span className="repo-private">私有项目</span></div>
        <div className="auth-preview-workflow"><span>灵感选题</span><i>→</i><span>AI 创作</span><i>→</i><span>多平台发布</span></div>
        <div className="auth-preview-foot"><div className="auth-preview-avatars"><span>你</span><span>创</span><span>运</span></div><span>你来拥有，与伙伴一起运营。</span><b>Owner</b></div>
      </div>
      <div className="auth-story-points"><span>项目独立</span><span>按角色协作</span><span>内容与账号归项目</span></div>
    </section>
    <section className="auth-card" aria-labelledby="auth-title" aria-busy={busy || phase === 'loading'}>
      {phase !== 'setup' && phase !== 'loading' && <div className="auth-mode-tabs" aria-label="账号入口">
        <a data-auth-mode="login" href={`${PUBLIC_BASE}/login/`} aria-current={!registering ? 'page' : undefined} onClick={event => { if (busy) event.preventDefault(); }}>登录</a>
        <a data-auth-mode="register" href={`${PUBLIC_BASE}/register/`} aria-current={registering ? 'page' : undefined} onClick={event => { if (busy) event.preventDefault(); }}>注册</a>
      </div>}
      <h2 id="auth-title">{phase === 'loading' ? '正在打开你的空间…' : phase === 'setup' ? '初始化 Chirpiva' : registering ? '开启你的创作事业' : '欢迎回来'}</h2>
      <p className="auth-intro">{phase === 'setup' ? '创建平台管理员，完成首次初始化。' : registering ? '创建账号后，建立你的第一个 IP 项目。' : '登录后，继续经营你的 IP 项目。'}</p>
      {phase !== 'loading' && <form className="access-form" onSubmit={submit}>
        <label htmlFor="auth-username">用户名</label>
        <input id="auth-username" name="username" autoComplete="username" required minLength={3} maxLength={64} autoFocus value={username}
          disabled={busy} onChange={event => setUsername(event.target.value)} placeholder={registering ? '3–64 个字符，用于登录和成员查找' : '输入你的用户名'} />
        {(phase === 'setup' || registering) && <><label htmlFor="auth-display-name">怎么称呼你 <span className="auth-optional">选填</span></label>
          <input id="auth-display-name" name="display_name" autoComplete="nickname" maxLength={80} value={displayName}
            disabled={busy} onChange={event => setDisplayName(event.target.value)} placeholder="你的名字或创作昵称" /></>}
        <label htmlFor="auth-password">密码</label>
        <div className="auth-password-field"><input id="auth-password" name="password" type={showPassword ? 'text' : 'password'} autoComplete={phase === 'login' ? 'current-password' : 'new-password'} required
          minLength={phase === 'login' ? undefined : 10} maxLength={1024} value={password} disabled={busy}
          onChange={event => setPassword(event.target.value)} placeholder={phase === 'login' ? '输入密码' : '至少 10 个字符'} />
          <button type="button" aria-label={showPassword ? '隐藏密码' : '显示密码'} aria-pressed={showPassword} disabled={busy} onClick={() => setShowPassword(!showPassword)}>{showPassword ? '隐藏' : '显示'}</button></div>
        {registering && <><label htmlFor="auth-confirm-password">确认密码</label><input id="auth-confirm-password" name="confirm_password" type="password" autoComplete="new-password" required minLength={10} maxLength={1024}
          disabled={busy} value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} placeholder="再输入一次密码" /></>}
        {error && <p className="access-error" role="alert">{error}</p>}
        <button className="btn btn-primary auth-submit" disabled={busy || !username.trim() || !password || (phase !== 'login' && password.length < 10) || (registering && !confirmPassword)}>
          {busy ? registering ? '正在创建账号…' : '正在验证…' : phase === 'setup' ? '完成初始化' : registering ? '注册并创建我的 IP' : '登录我的工作台'}<span aria-hidden="true">↗</span>
        </button>
        <p className="auth-footnote">{phase === 'setup' ? '此入口仅限服务器本机。已有内容会保留。' : registering ? '你的项目由你拥有，协作者由你授权。' : '忘记密码？请联系平台管理员重置。'}</p>
      </form>}
      {phase === 'loading' && error && <div className="auth-retry"><p className="access-error" role="alert">{error}</p><button className="btn" onClick={() => { void bootstrap(); }}>重试</button></div>}
    </section>
    </div>
    <footer className="auth-site-footer"><span>© {new Date().getFullYear()} Chirpiva 响芽</span><span>一个 IP，一处专属的创作空间。</span></footer>
  </main>;
}

export function AuthAccountMenu() {
  const auth = useAuth();
  const [manage, setManage] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!auth) return null;
  const logout = async () => {
    setBusy(true); setError('');
    try { await auth.logout(); }
    catch (error) { setError(errorMessage(error)); setBusy(false); }
  };
  return <>
    <div className="auth-account-menu">
      <span className="access-avatar">{(auth.user.display_name || auth.user.username).slice(0, 1)}</span>
      <div className="auth-account-name"><strong>{auth.user.display_name || auth.user.username}</strong><small>{auth.user.is_admin ? '平台管理员' : auth.user.username}</small></div>
      {auth.user.is_admin && <button className="access-icon-button" title="用户管理" aria-label="用户管理" onClick={() => setManage(true)}>⚙</button>}
      <button className="access-text-button" disabled={busy} onClick={() => { void logout(); }}>{busy ? '退出中…' : '退出'}</button>
    </div>
    {error && <p className="access-error auth-account-error" role="alert">{error}</p>}
    {manage && <PlatformUsersPanel onClose={() => setManage(false)} />}
  </>;
}

export function PlatformUsersPanel({ onClose }: { onClose: () => void }) {
  const auth = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<User | null>(null);
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [isAdmin, setIsAdmin] = useState(false);
  const [active, setActive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const dialog = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  const allowed = !!auth?.user.is_admin;

  useEffect(() => {
    alive.current = true;
    if (!allowed) return;
    void fetchUsers().then(users => { if (alive.current) setUsers(users); })
      .catch(error => { if (alive.current) setError(errorMessage(error)); })
      .finally(() => { if (alive.current) setLoading(false); });
    return () => { alive.current = false; };
  }, [allowed]);
  useAccessDialog(dialog, onClose, busy);
  if (!allowed) return null;

  const edit = (user: User | null) => {
    setEditing(user); setUsername(user?.username || ''); setDisplayName(user?.display_name || '');
    setPassword(''); setIsAdmin(user?.is_admin || false); setActive(user?.active ?? true); setError(''); setNotice('');
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || (!editing && (!username.trim() || password.length < 10)) || (password && password.length < 10)) return;
    setBusy(true); setError(''); setNotice('');
    try {
      if (editing) await patchUser(editing.id, { display_name: displayName.trim(), active, is_admin: isAdmin, ...(password ? { password } : {}) });
      else await createUser({ username: username.trim(), display_name: displayName.trim(), password, is_admin: isAdmin });
      if (!alive.current) return;
      const next = await fetchUsers();
      if (!alive.current) return;
      setUsers(next); edit(null); setNotice(editing ? '用户信息已保存' : '用户已创建，请为其分配 IP 角色');
      if (editing?.id === auth.user.id) await auth.refresh();
    } catch (error) { if (alive.current) setError(errorMessage(error)); }
    finally { if (alive.current) setBusy(false); }
  };

  return createPortal(<div className="overlay access-overlay" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div ref={dialog} className="modal access-users-modal" role="dialog" aria-modal="true" aria-labelledby="access-users-title">
      <div className="access-dialog-heading"><div><span className="access-eyebrow">TEAM ACCESS</span><h2 id="access-users-title">用户管理</h2></div>
        <button autoFocus className="access-close" aria-label="关闭用户管理" disabled={busy} onClick={onClose}>×</button></div>
      <p className="access-intro">先创建平台账号，再到对应 IP 的「成员与权限」添加成员。每个人在不同 IP 可以拥有不同角色。</p>
      <div className="access-users-layout">
        <div className="access-users-list" aria-label="平台用户列表">
          {loading && <p className="access-empty">正在加载用户…</p>}
          {!loading && users.map(user => <button key={user.id} className={`access-user-row ${editing?.id === user.id ? 'selected' : ''}`} disabled={busy} onClick={() => edit(user)}>
            <span className="access-avatar">{(user.display_name || user.username).slice(0, 1)}</span>
            <span className="access-user-copy"><strong>{user.display_name || user.username}</strong><small>@{user.username}{user.is_admin ? ' · 平台管理员' : ''}</small></span>
            <span className={`access-status ${user.active ? '' : 'inactive'}`}>{user.active ? '可登录' : '已停用'}</span>
          </button>)}
          <button className="access-add-user" disabled={busy} onClick={() => edit(null)}>＋ 新建用户</button>
        </div>
        <form className="access-form access-user-form" onSubmit={save}>
          <h3>{editing ? '编辑用户' : '新建用户'}</h3>
          <label htmlFor="team-username">用户名</label><input id="team-username" name="username" autoComplete="off" minLength={3} maxLength={64} required disabled={busy || !!editing}
            value={username} onChange={event => setUsername(event.target.value)} placeholder="用于登录的平台账号" />
          <label htmlFor="team-display-name">显示名称</label><input id="team-display-name" name="display_name" maxLength={80} disabled={busy}
            value={displayName} onChange={event => setDisplayName(event.target.value)} placeholder="成员姓名或称呼" />
          <label htmlFor="team-password">{editing ? '重置密码（选填）' : '初始密码'}</label><input id="team-password" name="password" type="password" autoComplete="new-password" minLength={10} maxLength={256}
            required={!editing} disabled={busy} value={password} onChange={event => setPassword(event.target.value)} placeholder={editing ? '留空保留当前密码' : '至少 10 个字符'} />
          <label className="access-checkbox"><input type="checkbox" checked={isAdmin} disabled={busy} onChange={event => setIsAdmin(event.target.checked)} />平台管理员</label>
          <p className="access-field-help">管理平台用户、系统与模型配置。IP 内容权限由各 IP 成员角色决定。</p>
          {editing && <label className="access-checkbox"><input type="checkbox" checked={active} disabled={busy} onChange={event => setActive(event.target.checked)} />允许此用户登录</label>}
          {editing && <p className="access-field-help">停用或重置密码后，该用户现有会话会失效。</p>}
          {error && <p className="access-error" role="alert">{error}</p>}
          {notice && <p className="access-notice" role="status">{notice}</p>}
          <div className="access-form-actions">{editing && <button type="button" className="btn" disabled={busy} onClick={() => edit(null)}>取消编辑</button>}
            <button className="btn btn-primary" disabled={busy || (!editing && (!username.trim() || password.length < 10)) || !!(password && password.length < 10)}>{busy ? '保存中…' : editing ? '保存用户' : '创建用户'}</button></div>
        </form>
      </div>
    </div>
  </div>, document.body);
}

// oxlint-disable-next-line react/only-export-components
export function useAccessDialog(dialog: { current: HTMLDivElement | null }, onClose: () => void, busy: boolean) {
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) { event.preventDefault(); onClose(); }
      if (event.key !== 'Tab') return;
      const fields = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]');
      if (!fields?.length) return;
      const first = fields[0], last = fields[fields.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', handler);
    return () => { window.removeEventListener('keydown', handler); previous?.focus(); };
  }, [dialog, onClose, busy]);
}
