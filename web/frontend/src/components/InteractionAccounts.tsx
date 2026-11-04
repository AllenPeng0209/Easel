import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { projectFetch } from '../lib/projectScope';
import type { AccountItem } from '../lib/api';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import { useIpPermissions } from './ProjectSpace';
import '../styles/interactionAccounts.css';

interface Account {
  id: number;
  platform: 'douyin' | 'xhs';
  nickname: string;
  avatar?: string;
  status: string;
  has_read_login?: boolean;
  has_creator?: boolean;
  capabilities?: Record<string, string | boolean>;
}
interface Login {
  platform: Account['platform'];
  accountId?: number;
  taskId: string;
  status: string;
  message: string;
}
const platformName = (platform: Account['platform']) => platform === 'douyin' ? '抖音' : '小红书';
async function accountRequest(path: string, options?: RequestInit) {
  const response = await projectFetch(`/api/operations/api${path}`, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : '账号操作未完成，请稍后重试');
  return data;
}

export default function InteractionAccounts({ refreshToken = 0, platforms = [], onPlatformLogin, onConnected }: {
  refreshToken?: number;
  platforms?: AccountItem[];
  onPlatformLogin?: (account: AccountItem) => void;
  onConnected?: () => void;
}) {
  const { canManageAccounts, canOperate } = useIpPermissions();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [expanded, setExpanded] = useState<number>();
  const [adding, setAdding] = useState(false);
  const [selectedPlatform, setSelectedPlatform] = useState('douyin');
  const [login, setLogin] = useState<Login>();
  const [busy, setBusy] = useState('');
  const mounted = useRef(true);
  const loadVersion = useRef(0);
  const actionVersion = useRef(0);
  const connectOpen = useRef(false);
  const managePermission = useRef(canManageAccounts);
  const connectedCallback = useRef(onConnected);
  connectedCallback.current = onConnected;
  managePermission.current = canManageAccounts;
  const dialog = useRef<HTMLElement>(null);
  const closeConnect = useCallback(() => {
    actionVersion.current++;
    connectOpen.current = false;
    setAdding(false); setLogin(undefined);
    setBusy(previous => previous === 'login' ? '' : previous);
  }, []);
  const dialogOpen = canManageAccounts && adding;
  useModalLayer(dialog, dialogOpen, closeConnect);
  useLayoutEffect(() => {
    const element = dialog.current;
    if (dialogOpen && element && !element.contains(document.activeElement)) {
      element.querySelector<HTMLElement>('[data-modal-autofocus]')?.focus();
    }
  }, [dialogOpen, login?.status]);
  useEffect(() => { if (!canManageAccounts) closeConnect(); }, [canManageAccounts, closeConnect]);
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
      const data = await accountRequest('/accounts', { signal });
      if (current()) {
        setAccounts(data.filter((item: Account) => ['douyin', 'xhs'].includes(item.platform)));
        setError('');
      }
    } catch (cause) {
      if (current()) setError(cause instanceof Error ? cause.message : '读取账号失败');
    } finally { if (current()) setLoading(false); }
  }, []);
  useEffect(() => {
    if (!canOperate) { setLoading(false); return; }
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load, refreshToken, canOperate]);

  const loginTaskId = login?.taskId;
  useEffect(() => {
    if (!loginTaskId) return;
    const version = actionVersion.current;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await accountRequest(`/accounts/connect/poll?task_id=${encodeURIComponent(loginTaskId)}`);
        if (!active || version !== actionVersion.current || !managePermission.current) return;
        if (result.status === 'confirmed') {
          setLogin(undefined); setAdding(false); setNotice(result.capabilities?.creator === 'verified' && result.capabilities?.comments === 'verified'
            ? '账号已连接，内容后台与主站登录已完成检查。'
            : '账号已连接；未完成验证的功能可在账号管理中继续检查。');
          connectedCallback.current?.();
          await load();
        } else if (['error', 'expired'].includes(result.status)) {
          setLogin(previous => previous?.taskId === loginTaskId ? { ...previous, status: result.status, message: result.hint || result.error || result.message || '本次登录未完成，请重试。' } : previous);
        } else {
          setLogin(previous => previous?.taskId === loginTaskId ? { ...previous, status: result.status, message: result.hint || '在打开的浏览器中完成登录，Easel 会自动检查内容后台和互动功能。' } : previous);
          timer = setTimeout(poll, 1800);
        }
      } catch (cause) {
        if (active && version === actionVersion.current && managePermission.current) setLogin(previous => previous?.taskId === loginTaskId ? { ...previous, status: 'error', message: cause instanceof Error ? cause.message : '读取登录状态失败' } : previous);
      }
    };
    timer = setTimeout(poll, 800);
    return () => { active = false; clearTimeout(timer); };
  }, [loginTaskId, load]);

  const connect = async (platform: Account['platform'], accountId?: number) => {
    if (!mounted.current || !managePermission.current || busy) return;
    const version = ++actionVersion.current;
    const current = () => mounted.current && version === actionVersion.current && managePermission.current;
    setBusy('login'); setError('');
    setLogin({ platform, accountId, taskId: '', status: 'opening', message: '正在打开平台登录窗口…' });
    connectOpen.current = true;
    setAdding(true);
    try {
      const path = `/accounts/connect/start?platform=${platform}${accountId ? `&account_id=${accountId}` : ''}`;
      const result = await accountRequest(path, { method: 'POST' });
      if (!current()) return;
      setLogin({ platform, accountId, taskId: result.task_id, status: result.status || 'opening', message: result.hint || 'Easel 正在检查已有登录，平台需要验证时会提示。' });
    } catch (cause) {
      if (current()) setLogin({ platform, accountId, taskId: '', status: 'error', message: cause instanceof Error ? cause.message : '启动登录失败' });
    } finally { if (current()) setBusy(''); }
  };
  const verify = async (account: Account) => {
    if (!mounted.current || !managePermission.current || busy) return;
    setBusy(`verify-${account.id}`); setError(''); setNotice('');
    try {
      const result = await accountRequest(`/accounts/${account.id}/refresh-profile`, { method: 'POST' });
      setNotice(result.skipped ? '平台暂缓了校验，请稍后重试。' : '已完成平台账号校验。');
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '账号校验失败'); }
    finally { setBusy(''); }
  };

  const availablePlatforms: { platform: string; name: string; interaction: boolean; account?: AccountItem }[] = [
    { platform: 'douyin', name: '抖音', interaction: true },
    { platform: 'xhs', name: '小红书', interaction: true },
  ];
  for (const account of platforms) {
    const platform = account.platform === 'xiaohongshu' ? 'xhs' : account.platform;
    const existing = availablePlatforms.find(item => item.platform === platform);
    if (existing) { existing.account = account; existing.name = account.name; }
    else availablePlatforms.push({ platform, name: account.name, interaction: false, account });
  }
  const selected = availablePlatforms.find(item => item.platform === selectedPlatform);
  const interactionLogin = !!selected?.interaction;
  const canConnect = interactionLogin || !!(selected?.account?.supported && onPlatformLogin);
  const selectionVersion = actionVersion.current;
  const confirmConnection = () => {
    if (!mounted.current || !managePermission.current || !connectOpen.current || selectionVersion !== actionVersion.current || busy || !canConnect) return;
    if (interactionLogin && (selectedPlatform === 'douyin' || selectedPlatform === 'xhs')) {
      void connect(selectedPlatform);
    } else if (selected?.account?.supported && onPlatformLogin) {
      closeConnect();
      onPlatformLogin(selected.account);
    }
  };

  if (!canOperate) return <p className="account-feedback">互动账号由当前 IP 的负责人管理。</p>;
  return <section className="connected-accounts" aria-label="互动账号">
    <div className="connected-accounts-heading"><div><h2>已连接账号</h2><p>{loading ? '正在读取账号…' : `${accounts.length} 个账号 · 内容与互动`}</p></div>
      {canManageAccounts && <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => { if (!mounted.current || !managePermission.current) return; actionVersion.current++; connectOpen.current = true; setLogin(undefined); setAdding(true); }}>＋ 添加账号</button>}</div>
    {error && <p className="account-feedback error" role="alert">{error}</p>}
    {notice && <p className="account-feedback" role="status">{notice}</p>}
    <div className="connected-account-table" role="table" aria-label="互动平台账号列表">
      <div className="connected-account-labels" role="row"><span role="columnheader">账号</span><span role="columnheader">平台</span><span role="columnheader">登录状态</span><span role="columnheader">操作</span></div>
      {accounts.map(account => {
        const ready = account.status === 'active' && (account.has_read_login || account.has_creator);
        return <div key={account.id} className="connected-account-item">
          <div className="connected-account-row" role="row">
            <div className="connected-account-person" role="cell"><span className={`connected-account-avatar ${account.platform}`}>{account.nickname.slice(0, 1) || platformName(account.platform).slice(0, 1)}</span>
              <div><strong>{account.nickname || '平台账号'}</strong><small>内容与互动</small></div></div>
            <span role="cell">{platformName(account.platform)}</span>
            <span role="cell" className={`connected-account-state ${ready ? 'ready' : ''}`}><i />{ready ? '已登录' : '需要登录'}</span>
            <div role="cell"><button className="btn btn-sm btn-ghost" aria-expanded={expanded === account.id} onClick={() => setExpanded(expanded === account.id ? undefined : account.id)}>{expanded === account.id ? '收起' : '管理'}</button></div>
          </div>
          {expanded === account.id && <div className="connected-account-detail"><div><p>一次连接，自动检查内容后台、评论和私信。</p>
            <p className="connected-account-capabilities">{([['creator', '内容后台'], ['comments', '评论读取'], ['dm', '私信读取']] as const).map(([key, label]) => <span key={key}>{label}：{account.capabilities?.[key] === 'verified' ? '已验证' : '待验证'}</span>)}</p></div>
            {canManageAccounts && <div><button className="btn btn-sm" disabled={!!busy} onClick={() => void verify(account)}>{busy === `verify-${account.id}` ? '校验中…' : '校验登录'}</button>
              <button className="btn btn-sm btn-ghost" disabled={!!busy} onClick={() => void connect(account.platform, account.id)}>检查并补充验证</button></div>}</div>}
        </div>;
      })}
      {!loading && !accounts.length && <div className="connected-account-empty"><strong>连接第一个平台账号</strong><p>选择平台登录，Easel 会自动检查该账号的可用功能。</p></div>}
    </div>
    {dialogOpen && <div className="account-connect-backdrop"><section ref={dialog} role="dialog" aria-modal="true" aria-label="连接平台账号" className="account-connect-dialog" tabIndex={-1}>
      <div><h2>{login ? `登录${platformName(login.platform)}` : '添加平台账号'}</h2><button className="icon-btn" data-modal-autofocus={login ? true : undefined} aria-label="关闭账号连接" onClick={closeConnect}>×</button></div>
      {login ? <><p role="status">{login.message}</p><small>如需补充验证，请在 Easel 打开的平台窗口中完成。</small>
        {['error', 'expired'].includes(login.status) && <button className="btn btn-primary" disabled={!!busy} onClick={() => void connect(login.platform, login.accountId)}>重试登录</button>}</>
        : <><p>{platforms.length ? `共 ${availablePlatforms.length} 个平台，选择平台后完成登录。` : '正在读取完整平台目录；也可先连接抖音或小红书账号。'}</p><label>平台<select className="field" data-modal-autofocus value={selectedPlatform} onChange={event => setSelectedPlatform(event.target.value)}>
          {availablePlatforms.map(item => <option key={item.platform} value={item.platform} disabled={!item.interaction && !item.account?.supported}>{item.name}{!item.interaction && !item.account?.supported ? '（暂不可用）' : ''}</option>)}
        </select></label>
          <p className="account-connect-capability">{interactionLogin ? '登录后自动检查内容后台、评论和私信。平台要求额外验证时，会在同一流程继续提示。' : selected?.account?.supported ? '使用 Easel 原有平台账号功能；互动消息尚未接入。' : '此平台目前暂不可连接。'}</p>
          <button className="btn btn-primary" disabled={!!busy || !canConnect} onClick={confirmConnection}>{busy ? '正在打开…' : '打开登录窗口'}</button></>}
    </section></div>}
  </section>;
}
