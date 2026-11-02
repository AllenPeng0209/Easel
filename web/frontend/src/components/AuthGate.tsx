import { createContext, Fragment, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  AccessError, createUser, fetchAuthBootstrap, fetchAuthUser, fetchUsers,
  loginAccount, logoutAccount, patchUser, setupAccount,
} from '../lib/access';
import type { User } from '../lib/access';
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
  const [phase, setPhase] = useState<'loading' | 'setup' | 'login' | 'ready'>('loading');
  const [user, setUser] = useState<User | null>(null);
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const callback = useRef(onAuthenticated);
  callback.current = onAuthenticated;
  const channel = useRef<BroadcastChannel | null>(null);
  const validating = useRef<Promise<User | null> | null>(null);
  const generation = useRef(0);
  const reloadRequired = useRef(false);

  const signedOut = useCallback(() => {
    generation.current += 1;
    setUser(null);
    setPassword('');
    setPhase('login');
    clearMarker();
    callback.current?.(null);
  }, []);

  const accepted = useCallback((account: User): boolean => {
    if (!account.active) { signedOut(); return false; }
    callback.current?.(account);
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
    setPassword('');
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
      await refresh();
    } catch (error) {
      if (version === generation.current) setError(errorMessage(error));
    }
  }, [refresh]);

  useEffect(() => { void bootstrap(); }, [bootstrap]);

  useEffect(() => {
    const expired = (event: Event) => {
      const expected = (event as CustomEvent<{ userId?: string | null }>).detail?.userId;
      if (expected && marker() && expected !== marker()) return;
      signedOut(); setError('登录已失效，请重新登录');
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
    signedOut();
    channel.current?.postMessage({ type: 'changed' });
    window.location.reload();
  }, [signedOut]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !username.trim() || !password || (phase === 'setup' && password.length < 10)) return;
    setBusy(true); setError('');
    const version = generation.current;
    try {
      const result = phase === 'setup'
        ? await setupAccount({ username: username.trim(), display_name: displayName.trim(), password })
        : await loginAccount({ username: username.trim(), password });
      if (version !== generation.current) return;
      channel.current?.postMessage({ type: 'changed' });
      accepted(result.user);
    } catch (error) { if (version === generation.current) setError(errorMessage(error)); }
    finally { setBusy(false); }
  };

  if (phase === 'ready' && user) return <AuthContext.Provider value={{ user, logout, refresh }}>
    <Fragment key={user.id}>{children}</Fragment>
  </AuthContext.Provider>;

  return <main className="auth-screen">
    <div className="auth-brand"><span>E</span><strong>Easel</strong></div>
    <section className="auth-card" aria-labelledby="auth-title">
      <div className="access-eyebrow">CREATOR WORKSPACE</div>
      <h1 id="auth-title">{phase === 'loading' ? '正在验证身份…' : phase === 'setup' ? '创建平台管理员' : '登录工作空间'}</h1>
      <p className="auth-intro">{phase === 'setup' ? '先创建你的管理账号，再添加团队成员，并为每个 IP 分配角色。' : '一个 IP，连接多个社交平台；每位成员按角色协作。'}</p>
      {phase !== 'loading' && <form className="access-form" onSubmit={submit}>
        <label htmlFor="auth-username">用户名</label>
        <input id="auth-username" name="username" autoComplete="username" required minLength={3} maxLength={64} autoFocus value={username}
          disabled={busy} onChange={event => setUsername(event.target.value)} placeholder="输入用户名" />
        {phase === 'setup' && <><label htmlFor="auth-display-name">显示名称</label>
          <input id="auth-display-name" name="display_name" autoComplete="nickname" maxLength={80} value={displayName}
            disabled={busy} onChange={event => setDisplayName(event.target.value)} placeholder="例如：团队管理员" /></>}
        <label htmlFor="auth-password">密码</label>
        <input id="auth-password" name="password" type="password" autoComplete={phase === 'setup' ? 'new-password' : 'current-password'} required
          minLength={phase === 'setup' ? 10 : undefined} maxLength={256} value={password} disabled={busy}
          onChange={event => setPassword(event.target.value)} placeholder={phase === 'setup' ? '至少 10 个字符' : '输入密码'} />
        {error && <p className="access-error" role="alert">{error}</p>}
        <button className="btn btn-primary auth-submit" disabled={busy || !username.trim() || !password || (phase === 'setup' && password.length < 10)}>
          {busy ? '正在验证…' : phase === 'setup' ? '创建管理员并进入' : '登录'}
        </button>
        <p className="auth-footnote">{phase === 'setup' ? '现有 IP 与内容会保留，并归属这个初始管理账号。' : '需要账号或忘记密码，请联系你的平台管理员。'}</p>
      </form>}
      {phase === 'loading' && error && <div className="auth-retry"><p className="access-error" role="alert">{error}</p><button className="btn" onClick={() => { void bootstrap(); }}>重试</button></div>}
    </section>
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
