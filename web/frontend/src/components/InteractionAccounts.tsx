import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ACTIVE_PROJECT_ID, projectFetch } from '../lib/projectScope';
import type { AccountDirectory, AccountDirectoryItem, AccountItem, RemoteLoginDescriptor } from '../lib/api';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import { useIpPermissions } from './ProjectSpace';
import { closeRemoteLogin } from './RemoteLoginView';
import PlatformLoginView from './PlatformLoginView';
import '../styles/interactionAccounts.css';

type NativePlatform = 'douyin' | 'xhs';
interface Login {
  platform: NativePlatform;
  accountId?: number;
  requestedAccountId?: number;
  taskId: string;
  status: string;
  message: string;
  remote?: RemoteLoginDescriptor;
  loginSaved?: boolean;
  stage?: string;
  capabilities?: AccountDirectoryItem['capabilities'];
}
const awaitingNativeScan = (state: { stage?: string; status: string; loginSaved?: boolean }) =>
  state.stage === 'read' && ['opening', 'waiting'].includes(state.status) && state.loginSaved !== true;
function connectionProgress(result: { loginSaved?: boolean; stage?: string; account_id?: number; capabilities?: AccountDirectoryItem['capabilities'] }, previous?: Login) {
  return { loginSaved: result.loginSaved ?? previous?.loginSaved, stage: result.stage ?? previous?.stage,
    capabilities: result.capabilities ?? previous?.capabilities, accountId: result.account_id ?? previous?.accountId };
}
const nativePlatform = (platform: string): NativePlatform | undefined => platform === 'douyin' ? 'douyin' : ['xhs', 'xiaohongshu'].includes(platform) ? 'xhs' : undefined;
const platformName = (platform: NativePlatform) => platform === 'douyin' ? '抖音' : '小红书';
const capabilityLabel = (status?: string) => status === 'verified' ? '已验证' : status === 'unavailable' ? '暂不支持' : '待验证';
const hasNativeAccount = (account: AccountDirectoryItem) => typeof account.nativeAccountId === 'number'
  && Number.isSafeInteger(account.nativeAccountId) && account.nativeAccountId > 0;
async function accountRequest(path: string, options?: RequestInit) {
  const response = await projectFetch(path, { cache: 'no-store', ...options });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : '账号操作未完成，请稍后重试');
  return data;
}

function Avatar({ url, name }: { url?: string; name: string }) {
  const [broken, setBroken] = useState('');
  if (url && broken !== url) return <img className="connected-account-avatar" src={url} alt={name} referrerPolicy="no-referrer" onError={() => setBroken(url)} />;
  return <div className="connected-account-avatar">{name.trim().slice(0, 1) || '?'}</div>;
}

/** One directory for all platform identities; adapters remain an internal detail. */
export default function InteractionAccounts({ refreshToken = 0, onPlatformLogin, onConnected }: {
  refreshToken?: number;
  onPlatformLogin?: (account: AccountItem) => void;
  onConnected?: () => void;
}) {
  const { canManageAccounts } = useIpPermissions();
  const [directory, setDirectory] = useState<AccountDirectory>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [expanded, setExpanded] = useState<string>();
  const [adding, setAdding] = useState(false);
  const [selectedPlatform, setSelectedPlatform] = useState('douyin');
  const [login, setLogin] = useState<Login>();
  const [busy, setBusy] = useState('');
  const mounted = useRef(true);
  const loadVersion = useRef(0);
  const actionVersion = useRef(0);
  const backendOpenVersion = useRef<number | null>(null);
  const connectOpen = useRef(false);
  const managePermission = useRef(canManageAccounts);
  const connectedCallback = useRef(onConnected);
  connectedCallback.current = onConnected;
  managePermission.current = canManageAccounts;
  const dialog = useRef<HTMLElement>(null);
  const loginRef = useRef(login);
  loginRef.current = login;
  const replacement = useRef({ taskId: '', revision: 0 });
  const closingLogin = useRef<{ remote: RemoteLoginDescriptor; version: number } | null>(null);
  const refreshAfterClose = useRef<() => Promise<void>>(async () => {});
  const closedRemotes = useRef(new Map<string, boolean>());
  const settledRemote = useRef<(remote: RemoteLoginDescriptor, acknowledged: boolean) => void>(() => {});
  const closeConnect = useCallback(() => {
    const previousLogin = loginRef.current;
    const version = ++actionVersion.current;
    connectOpen.current = false;
    setAdding(false); setLogin(undefined);
    if (previousLogin?.remote && managePermission.current) {
      closingLogin.current = { remote: previousLogin.remote, version };
      setBusy('closing-login');
      const acknowledged = closedRemotes.current.get(previousLogin.remote.id);
      if (acknowledged !== undefined) {
        const remote = previousLogin.remote;
        queueMicrotask(() => settledRemote.current(remote, acknowledged));
      }
    } else {
      if (!managePermission.current) closingLogin.current = null;
      setBusy(previous => previous === 'login' ? '' : previous);
      if (previousLogin) void refreshAfterClose.current();
    }
  }, []);
  const dialogOpen = canManageAccounts && adding;
  useModalLayer(dialog, dialogOpen, closeConnect);
  useLayoutEffect(() => {
    const element = dialog.current;
    if (dialogOpen && element && !element.contains(document.activeElement)) {
      element.querySelector<HTMLElement>('[data-modal-autofocus]')?.focus();
    }
  }, [dialogOpen, login?.status]);
  useEffect(() => { if (!canManageAccounts) { closeConnect(); setBusy(''); } }, [canManageAccounts, closeConnect]);
  useEffect(() => {
    mounted.current = true;
    const pendingActions = actionVersion;
    const pendingLoads = loadVersion;
    return () => { mounted.current = false; pendingActions.current++; pendingLoads.current++; };
  }, []);

  const load = useCallback(async (signal?: AbortSignal) => {
    const version = ++loadVersion.current;
    const current = () => mounted.current && version === loadVersion.current && !signal?.aborted;
    try {
      const data = await accountRequest('/api/accounts/directory', { signal });
      if (!data || !Array.isArray(data.platforms) || !Array.isArray(data.accounts)) throw new Error('账号目录暂时无法读取，请重新加载。');
      if (data.project_id !== ACTIVE_PROJECT_ID) throw new Error('账号目录不属于当前 IP，请重新读取。');
      if (current()) {
        setDirectory(data);
        setSelectedPlatform(previous => data.platforms.some((item: AccountItem) => item.platform === previous) ? previous : data.platforms[0]?.platform || '');
        setError('');
      }
    } catch (cause) {
      if (current()) setError(cause instanceof Error ? cause.message : '读取账号失败');
    } finally { if (current()) setLoading(false); }
  }, []);
  refreshAfterClose.current = load;
  const remoteSettled = useCallback(async (remote: RemoteLoginDescriptor, acknowledged: boolean) => {
    if (!mounted.current) return;
    closedRemotes.current.set(remote.id, acknowledged);
    if (closedRemotes.current.size > 64) closedRemotes.current.delete(closedRemotes.current.keys().next().value!);
    const closing = closingLogin.current;
    if (closing?.remote.id === remote.id && closing.remote.platform === remote.platform
        && closing.version === actionVersion.current) {
      if (!acknowledged) {
        setError('登录窗口尚未结束，请重试关闭后再添加账号。已保存的登录不会退出。');
        return;
      }
      closingLogin.current = null;
      setBusy(previous => previous === 'closing-login' ? '' : previous);
      setNotice('登录窗口已关闭，请查看下方账号状态。');
    }
    await load();
  }, [load]);
  settledRemote.current = remoteSettled;
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, refreshToken, canManageAccounts]);

  const loginTaskId = login?.taskId;
  useEffect(() => {
    if (!loginTaskId) return;
    const version = actionVersion.current;
    const controller = new AbortController();
    const deadline = Date.now() + 620000;
    const visible = () => document.visibilityState !== 'hidden';
    let active = true, polling = false, finished = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!active || polling || finished || !visible()
          || version !== actionVersion.current || !managePermission.current) return;
      if (replacement.current.taskId === loginTaskId) { timer = setTimeout(poll, 1800); return; }
      const revision = replacement.current.revision;
      clearTimeout(timer); polling = true;
      try {
        const result = await accountRequest(`/api/operations/api/accounts/connect/poll?task_id=${encodeURIComponent(loginTaskId)}`, { signal: controller.signal });
        if (!active || version !== actionVersion.current || !managePermission.current || revision !== replacement.current.revision) return;
        if (result.status === 'confirmed') {
          finished = true;
          setLogin(undefined); setAdding(false); connectOpen.current = false;
          setNotice(result.capabilities?.creator === 'verified' && result.capabilities?.comments === 'verified'
            ? '账号已连接，内容后台与主站登录已完成检查。'
            : '账号已连接；未完成验证的功能可在账号管理中继续检查。');
          connectedCallback.current?.();
          await load();
        } else if (['error', 'expired'].includes(result.status)) {
          finished = true;
          setLogin(previous => previous?.taskId === loginTaskId ? { ...previous, ...connectionProgress(result, previous), remote: result.remote || previous.remote, status: result.status, message: result.hint || result.error || result.message || '本次登录未完成，请重试。' } : previous);
        } else if (Date.now() > deadline) {
          // Hidden tabs can finish login on the phone. Check the real result
          // above before treating an old, still-pending attempt as timed out.
          finished = true;
          setLogin(previous => previous?.taskId === loginTaskId ? { ...previous, ...connectionProgress(result, previous), status: 'expired', message: '登录等待超时，请重新连接。' } : previous);
        } else {
          setLogin(previous => previous?.taskId === loginTaskId ? { ...previous, ...connectionProgress(result, previous), remote: result.remote || previous.remote, status: result.status, message: result.hint || '在打开的浏览器中完成登录，响芽 会自动检查可用功能。' } : previous);
        }
      } catch (cause) {
        if (revision !== replacement.current.revision) return;
        finished = true;
        if (active && version === actionVersion.current && managePermission.current) setLogin(previous => previous?.taskId === loginTaskId ? { ...previous, status: 'error', message: cause instanceof Error ? cause.message : '读取登录状态失败' } : previous);
      } finally {
        polling = false;
        if (active && !finished && visible()) timer = setTimeout(poll, 1800);
      }
    };
    const visibility = () => { clearTimeout(timer); if (visible()) void poll(); };
    document.addEventListener('visibilitychange', visibility);
    if (visible()) timer = setTimeout(poll, 800);
    return () => { active = false; controller.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [loginTaskId, load]);

  const connect = async (platform: NativePlatform, accountId?: number) => {
    if (!mounted.current || !managePermission.current || busy) return false;
    const version = ++actionVersion.current;
    const current = () => mounted.current && version === actionVersion.current && managePermission.current;
    setBusy('login'); setError('');
    setLogin({ platform, accountId, taskId: '', status: 'opening', message: '正在检查账号登录…' });
    connectOpen.current = true;
    setAdding(true);
    try {
      const path = `/api/operations/api/accounts/connect/start?platform=${platform}${accountId ? `&account_id=${accountId}` : ''}`;
      const result = await accountRequest(path, { method: 'POST' });
      if (!current()) {
        if (result.remote) void closeRemoteLogin(result.remote);
        return false;
      }
      setLogin({ platform, requestedAccountId: accountId, ...connectionProgress(result, { platform, accountId, taskId: '', status: '', message: '' }), remote: result.remote, taskId: result.task_id, status: result.status || 'opening', message: result.hint || '响芽 正在检查已有登录，平台需要验证时会提示。' });
      return !!result.task_id;
    } catch (cause) {
      if (current()) setLogin({ platform, accountId, taskId: '', status: 'error', message: cause instanceof Error ? cause.message : '启动登录失败' });
      return false;
    } finally { if (current()) setBusy(''); }
  };

  const restartLogin = async (remote: RemoteLoginDescriptor, closeOld: () => Promise<boolean>) => {
    const original = loginRef.current;
    const version = actionVersion.current;
    const current = () => mounted.current && managePermission.current && connectOpen.current && version === actionVersion.current
      && loginRef.current?.remote?.id === remote.id && loginRef.current.remote.platform === remote.platform
      && awaitingNativeScan(loginRef.current);
    if (!original?.taskId || replacement.current.taskId || !current()) return false;
    const taskId = original.taskId;
    const progress = () => accountRequest(`/api/operations/api/accounts/connect/poll?task_id=${encodeURIComponent(taskId)}`);
    replacement.current = { taskId, revision: replacement.current.revision + 1 };
    try {
      const before = await progress();
      if (!current() || before.task_id !== taskId || before.platform !== original.platform
          || before.remote?.id !== remote.id || before.remote?.platform !== remote.platform || !awaitingNativeScan(before)) return false;
      if (!await closeOld() || !current()) return false;
      // A phone confirmation can race cancellation. Only replace a cancelled,
      // unsaved native task. A proved account is kept by the existing flow.
      const after = await progress();
      if (!current() || after.task_id !== taskId || after.platform !== original.platform || after.loginSaved
          || after.status !== 'error' || after.stage !== 'canceled') return false;
      return await connect(original.platform, original.requestedAccountId);
    } finally {
      if (replacement.current.taskId === taskId) replacement.current.taskId = '';
    }
  };

  const operateAccount = async (account: AccountDirectoryItem, action: 'verify' | 'logout') => {
    if (!mounted.current || !managePermission.current || busy) return;
    if (action === 'logout' && !window.confirm(`确定退出「${account.name}」？该账号在当前 IP 各功能中的登录将一并退出。`)) return;
    const version = ++actionVersion.current;
    loadVersion.current++;
    const current = () => mounted.current && version === actionVersion.current && managePermission.current;
    setBusy(`${action}:${account.key}`); setError(''); setNotice('');
    try {
      await accountRequest(`/api/accounts/directory/${encodeURIComponent(account.key)}/${action}`, { method: 'POST' });
      if (!current()) return;
      setNotice(action === 'logout' ? '该账号已退出登录。' : '账号登录状态已更新。');
      await load();
    } catch (cause) { if (current()) setError(cause instanceof Error ? cause.message : '账号操作未完成'); }
    finally { if (current()) setBusy(''); }
  };
  const checkAccount = (account: AccountDirectoryItem) => {
    const platform = nativePlatform(account.platform);
    if (platform && typeof account.nativeAccountId === 'number' && account.nativeAccountId > 0) void connect(platform, account.nativeAccountId);
    else void operateAccount(account, 'verify');
  };
  const openBackend = async (account: AccountDirectoryItem) => {
    if (!mounted.current || !managePermission.current || busy || !hasNativeAccount(account)
        || backendOpenVersion.current === actionVersion.current) return;
    const version = ++actionVersion.current;
    backendOpenVersion.current = version;
    const current = () => mounted.current && version === actionVersion.current && managePermission.current;
    setBusy(`open-backend:${account.key}`); setError(''); setNotice('');
    try {
      const result = await accountRequest(`/api/accounts/directory/${encodeURIComponent(account.key)}/open-backend`, { method: 'POST' });
      if (!current()) return;
      if (result?.opened !== true || typeof result.summary !== 'string' || !result.summary.trim()) {
        throw new Error('平台后台未打开，请稍后重试。');
      }
      setNotice(result.summary);
    } catch (cause) { if (current()) setError(cause instanceof Error ? cause.message : '平台后台未打开，请稍后重试。'); }
    finally {
      if (backendOpenVersion.current === version) backendOpenVersion.current = null;
      if (current()) setBusy('');
    }
  };

  const accounts = directory?.accounts || [];
  const platforms = directory?.platforms || [];
  const selected = platforms.find(item => item.platform === selectedPlatform);
  const selectedNative = nativePlatform(selectedPlatform);
  const canConnect = !!selected?.supported && (!!selectedNative || !!onPlatformLogin);
  const selectionVersion = actionVersion.current;
  const confirmConnection = () => {
    if (!mounted.current || !managePermission.current || !connectOpen.current || selectionVersion !== actionVersion.current || busy || !canConnect) return;
    if (selectedNative) void connect(selectedNative);
    else if (selected && onPlatformLogin) { closeConnect(); onPlatformLogin(selected); }
  };

  return <section className="connected-accounts" aria-label="平台账号">
    <div className="connected-accounts-heading"><div><h2>已添加账号</h2><p>{loading && !directory ? '正在读取账号…' : directory ? `${accounts.length} 个账号 · 支持 ${platforms.length} 个平台` : '账号暂时无法读取'}</p></div>
      {canManageAccounts && <button className="btn btn-primary btn-sm" disabled={!!busy || loading || !directory || !!error || !platforms.length} onClick={() => { if (!mounted.current || !managePermission.current) return; actionVersion.current++; connectOpen.current = true; setLogin(undefined); setAdding(true); }}>＋ 添加账号</button>}</div>
    {error && <p className="account-feedback error" role="alert">{error}<button className="btn btn-sm btn-ghost" onClick={() => {
      const closing = closingLogin.current;
      if (closing) void closeRemoteLogin(closing.remote).then(acknowledged => remoteSettled(closing.remote, acknowledged));
      else void load();
    }}>{closingLogin.current ? '重试关闭' : '重新读取'}</button></p>}
    {busy === 'closing-login' && <p className="account-feedback" role="status">正在结束本次验证并更新账号状态…</p>}
    {notice && <p className="account-feedback" role="status">{notice}</p>}
    <div className="connected-account-table" role="table" aria-label="平台账号列表">
      <div className="connected-account-labels" role="row"><span role="columnheader">账号</span><span role="columnheader">平台</span><span role="columnheader">登录状态</span><span role="columnheader">操作</span></div>
      {accounts.map(account => {
        const ready = account.loggedIn && account.status === 'logged_in';
        const capabilities = ([['creator', '内容后台'], ['comments', '评论读取'], ['dm', '私信读取']] as const).filter(([key]) => account.capabilities[key] !== 'unavailable');
        return <div key={account.key} className="connected-account-item">
          <div className="connected-account-row" role="row">
            <div className="connected-account-person" role="cell"><Avatar url={account.avatar} name={account.name || account.platformName} />
              <div><strong>{account.name || '平台账号'}</strong><small>{account.platformName}{account.current ? ' · 当前账号' : !account.identityVerified ? ' · 身份待核实' : ''}</small></div></div>
            <span role="cell">{account.platformName}</span>
            <span role="cell" className={`connected-account-state ${ready ? 'ready' : ''}`}><i /><span>{account.status === 'unknown' ? '待核实' : ready ? '已登录' : '需要登录'}
              {ready && account.capabilities.creator === 'needs_verification' && <small>创作后台待验证</small>}</span></span>
            <div role="cell"><button className="btn btn-sm btn-ghost" aria-expanded={expanded === account.key} onClick={() => setExpanded(expanded === account.key ? undefined : account.key)}>{expanded === account.key ? '收起' : '管理'}</button></div>
          </div>
          {expanded === account.key && <div className="connected-account-detail"><div><p>同一账号由 响芽 各功能统一使用。</p>
            {capabilities.length ? <p className="connected-account-capabilities">{capabilities.map(([key, label]) => <span key={key}>{label}：{capabilityLabel(account.capabilities[key])}</span>)}</p>
              : <p className="connected-account-capabilities">此平台当前提供账号登录，可用操作由相应功能页面检查。</p>}</div>
            {canManageAccounts && <div><button className="btn btn-sm" disabled={!!busy} onClick={() => checkAccount(account)}>{busy === `verify:${account.key}` ? '检查中…' : ready && hasNativeAccount(account) && account.capabilities.creator === 'needs_verification' ? '补充验证' : '检查登录'}</button>
              {hasNativeAccount(account) && <button className="btn btn-sm btn-ghost" disabled={!!busy} onClick={() => void openBackend(account)}>{busy === `open-backend:${account.key}` ? '打开中…' : '打开平台后台'}</button>}
              <button className="btn btn-sm btn-ghost" disabled={!!busy} onClick={() => void operateAccount(account, 'logout')}>{busy === `logout:${account.key}` ? '退出中…' : '退出登录'}</button></div>}</div>}
        </div>;
      })}
      {directory && !loading && !error && !accounts.length && <div className="connected-account-empty"><strong>当前 IP 还没有账号</strong><p>{canManageAccounts ? '添加一个平台账号，登录后在 响芽 中统一使用。' : '平台账号由当前 IP 的负责人添加。'}</p></div>}
    </div>
    {dialogOpen && <div className="account-connect-backdrop"><section ref={dialog} role="dialog" aria-modal="true" aria-label="连接平台账号" className={`account-connect-dialog${login?.remote ? ' account-connect-remote' : ''}`} tabIndex={-1}>
      <div><h2>{login ? login.loginSaved ? `${platformName(login.platform)}已登录` : `登录${platformName(login.platform)}` : '添加平台账号'}</h2><button className="icon-btn" data-modal-autofocus={login ? true : undefined} aria-label="关闭账号连接" onClick={closeConnect}>×</button></div>
      {login ? <><p role="status">{login.remote && !login.loginSaved && !['error', 'expired'].includes(login.status)
        ? '请先扫码连接；如平台需要额外验证，可按提示继续。' : login.message}</p>
        {login.loginSaved && <p className="account-login-saved" role="status">主站登录已保存。{login.capabilities?.creator === 'verified'
          ? '正在检查其余可用功能。' : '创作后台仍待补充验证。'}可以关闭此窗口，稍后继续验证。</p>}
        {login.remote && !['error', 'expired'].includes(login.status) ? <PlatformLoginView remote={login.remote} onSettled={remoteSettled}
          onRestart={awaitingNativeScan(login) ? restartLogin : undefined} />
          : <small>如需补充验证，请在 响芽 打开的平台窗口中完成。</small>}
        {login.loginSaved && <button className="btn" onClick={closeConnect}>保留登录并关闭</button>}
        {['error', 'expired'].includes(login.status) && <button className="btn btn-primary" disabled={!!busy} onClick={() => void connect(login.platform, login.accountId)}>重试登录</button>}</>
        : <><p>共 {platforms.length} 个平台，选择平台后完成登录。</p><label>平台<select className="field" data-modal-autofocus value={selectedPlatform} onChange={event => setSelectedPlatform(event.target.value)}>
          {platforms.map(item => <option key={item.platform} value={item.platform} disabled={!item.supported}>{item.name}{!item.supported ? '（暂不可用）' : ''}</option>)}
        </select></label>
          <p className="account-connect-capability">{selectedNative ? '登录后自动检查内容后台、评论和私信。平台要求额外验证时，会在同一流程继续提示。' : selected?.supported ? '连接该平台账号，由已支持的功能统一使用。' : '此平台目前暂不可连接。'}</p>
          <button className="btn btn-primary" disabled={!!busy || !canConnect} onClick={confirmConnection}>{busy ? '正在打开…' : '打开登录窗口'}</button></>}
    </section></div>}
  </section>;
}
