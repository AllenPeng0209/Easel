import { useCallback, useEffect, useRef, useState } from 'react';
import type { RemoteLoginDescriptor } from '../lib/api';
import { projectFetch } from '../lib/projectScope';
import RemoteLoginView, { closeRemoteLogin } from './RemoteLoginView';
import '../styles/platformLogin.css';

type Props = {
  remote: RemoteLoginDescriptor;
  onClose?: () => void;
  onRestart?: (remote: RemoteLoginDescriptor, closeOld: () => Promise<boolean>) => Promise<boolean>;
  onSettled?: (remote: RemoteLoginDescriptor, acknowledged: boolean) => void;
};
type QrState = 'starting' | 'ready' | 'expired' | 'verification_required' | 'unavailable' | 'closed';
type Qr = { state: QrState; image?: string; pageId?: string; version?: string; awaitingPhone?: boolean };
type Owner = { key: string; active: boolean; close: (retry?: boolean) => Promise<boolean> };
type Recovery = 'idle' | 'queued' | 'refreshing' | 'checking' | 'stalled' | 'restarting' | 'close_failed';
type RequestedAction = { kind: 'refresh' | 'restart'; source: Qr };
const pollDelay = 2500;
const sameExpiredPage = (a: Qr, b: Qr) => a.state === 'expired' && b.state === 'expired'
  && !!a.pageId && a.pageId === b.pageId && !!a.version && !!b.version;
type Poll = { active: boolean; busy: boolean; controller: AbortController;
  timer?: ReturnType<typeof setTimeout>; read: () => Promise<void>; request: (kind: RequestedAction['kind']) => void };
const qrStates = new Set<QrState>(['starting', 'ready', 'expired', 'verification_required', 'unavailable', 'closed']);
const visible = () => document.visibilityState !== 'hidden';
const platformNames: Record<string, string> = {
  douyin: '抖音', xhs: '小红书', xiaohongshu: '小红书', kuaishou: '快手', 'weixin-channels': '微信视频号',
  zhihu: '知乎', juejin: '掘金', 'twitter-x': 'X', bilibili: 'B站', toutiao: '今日头条',
  'wechat-oa': '微信公众号', facebook: 'Facebook', instagram: 'Instagram', tiktok: 'TikTok', youtube: 'YouTube',
};
const scanApp = (platform: string, fallback: string) => ['wechat-oa', 'weixin-channels'].includes(platform) ? '微信' : fallback;

function readQr(value: unknown): Qr {
  if (!value || typeof value !== 'object') throw new Error('invalid-qr');
  const row = value as Record<string, unknown>;
  if (!qrStates.has(row.state as QrState)) throw new Error('invalid-qr');
  const validPage = typeof row.pageId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(row.pageId);
  const validVersion = typeof row.version === 'string' && /^[a-f0-9]{64}$/.test(row.version);
  const image = typeof row.image === 'string' && row.image.length <= 500 * 1024
    && /^data:image\/(png|jpeg);base64,[A-Za-z0-9+/]+={0,2}$/.test(row.image) ? row.image : undefined;
  if (row.state === 'ready' && (!validPage || !validVersion || !image)) throw new Error('invalid-qr');
  return { state: row.state as QrState,
    ...(row.state === 'starting' && row.message === '已扫码，请在手机上确认，随后核验账号身份' ? { awaitingPhone: true } : {}),
    ...(validPage && validVersion ? { pageId: row.pageId as string, version: row.version as string } : {}),
    ...(row.state === 'ready' ? { image } : {}) };
}

function useLoginOwnership(descriptor: RemoteLoginDescriptor | undefined, onSettled: Props['onSettled'], onEnded: (remote: RemoteLoginDescriptor) => void) {
  const owner = useRef<Owner | null>(null);
  const settled = useRef(onSettled), ended = useRef(onEnded);
  settled.current = onSettled; ended.current = onEnded;
  const id = descriptor?.id, platform = descriptor?.platform;
  useEffect(() => {
    if (!id || !platform) return;
    const target = { id, platform };
    let closure: Promise<boolean> | undefined;
    let failed = false;
    const current: Owner = { key: `${platform}:${id}`, active: true, close: (retry = false) => {
      if (!closure || (retry && failed)) {
        failed = false;
        closure = closeRemoteLogin(target).then(acknowledged => {
          failed = !acknowledged;
          settled.current?.(target, acknowledged);
          return acknowledged;
        });
      }
      return closure;
    } };
    owner.current = current;
    const pagehide = () => { current.active = false; current.close(); ended.current(target); };
    window.addEventListener('pagehide', pagehide);
    return () => {
      current.active = false; window.removeEventListener('pagehide', pagehide);
      queueMicrotask(() => {
        const replacement = owner.current;
        if (replacement !== current && replacement?.active && replacement.key === current.key) return;
        current.close();
      });
    };
  }, [id, platform]);
  const isActive = useCallback(() => owner.current?.active === true, []);
  const closeForRestart = useCallback(() => owner.current?.active ? owner.current.close(true) : Promise.resolve(false), []);
  return { isActive, closeForRestart };
}

function QrLoginView({ remote, onClose, onSettled, onRestart }: Props) {
  const { id, platform } = remote;
  const platformName = platformNames[platform] || '平台';
  const [interactive, setInteractive] = useState(false);
  const [needsHelp, setNeedsHelp] = useState(false);
  const [qr, setQr] = useState<Qr>({ state: 'starting' });
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [closed, setClosed] = useState(false);
  const [recovery, setRecovery] = useState<Recovery>('idle');
  const { isActive, closeForRestart } = useLoginOwnership(remote, onSettled, () => { setClosed(true); setQr({ state: 'closed' }); });
  const poll = useRef<Poll | null>(null);
  const restart = useRef(onRestart); restart.current = onRestart;
  useEffect(() => {
    if (interactive || closed) return;
    const base = `/api/accounts/${encodeURIComponent(platform)}/remote/${encodeURIComponent(id)}/qrcode`;
    const current: Poll = { active: true, busy: false, controller: new AbortController(), read: async () => {}, request: () => {} };
    poll.current = current;
    let latest: Qr = { state: 'starting' };
    let watch: { since: number; consecutive: number; last: Qr } | undefined;
    let pending: RequestedAction | undefined;
    let acting = false;
    let phase: Recovery = 'idle';
    const active = () => current.active && isActive();
    const show = (value: Recovery) => { phase = value; setRecovery(value); };
    const end = () => { current.active = false; setWorking(false); setQr({ state: 'closed' }); setClosed(true); };
    const schedule = () => {
      clearTimeout(current.timer);
      if (active() && visible()) current.timer = setTimeout(() => { void current.read(); }, pollDelay);
    };
    const getQr = async (): Promise<Qr> => {
      const response = await projectFetch(base, { cache: 'no-store', signal: current.controller.signal });
      if ([401, 403, 404, 410].includes(response.status)) return { state: 'closed' };
      if (!response.ok) throw new Error('qr-unavailable');
      return readQr(await response.json());
    };
    const observe = (next: Qr) => {
      latest = next; setQr(next); setError('');
      if (next.state === 'closed') { end(); return; }
      if (!watch || acting) return;
      if (next.state === 'expired') {
        watch.consecutive = sameExpiredPage(next, watch.last) ? watch.consecutive + 1 : 1;
        watch.last = next;
        if (watch.consecutive >= 3 && Date.now() - watch.since >= 2 * pollDelay) {
          if (phase !== 'close_failed') show('stalled');
        } else { show('checking'); }
      } else if (next.state === 'starting' && !next.awaitingPhone) {
        watch.consecutive = 0; show('checking');
      } else {
        watch = undefined; show('idle');
      }
    };
    const runAction = async ({ kind, source }: RequestedAction) => {
      if (!active()) return;
      if (!sameExpiredPage(source, latest) || (kind === 'refresh' && source.version !== latest.version)) {
        acting = false; watch = undefined; show('idle'); schedule(); return;
      }
      current.busy = true; setWorking(true); setError(''); clearTimeout(current.timer);
      let readAgain = false;
      try {
        if (kind === 'refresh') {
          show('refreshing'); setQr({ state: 'starting' });
          watch = { since: Date.now(), consecutive: 0, last: source };
          readAgain = true;
          const response = await projectFetch(`${base}/refresh`, { method: 'POST', cache: 'no-store',
            signal: current.controller.signal, headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pageId: source.pageId, version: source.version }) });
          if (!active()) return;
          if ([401, 403, 404, 410].includes(response.status)) { end(); return; }
          if (response.status === 409) { watch = undefined; show('idle'); }
          else { show('checking'); }
        } else {
          show('restarting');
          // The code can change after the button appears. Always recheck before closing the attempt.
          const fresh = await getQr();
          if (!active()) return;
          observe(fresh);
          const replace = restart.current;
          if (!sameExpiredPage(source, fresh) || !replace) { watch = undefined; show('idle'); return; }
          let closeCalled = false, acknowledged = false;
          const started = await replace({ id, platform }, async () => {
            if (!active() || !restart.current) return false;
            closeCalled = true;
            acknowledged = await closeForRestart();
            return acknowledged;
          });
          if (!active()) return;
          if (started || acknowledged) { end(); return; }
          if (closeCalled) show('close_failed');
          else { watch = undefined; show('idle'); }
        }
      } catch {
        if (active()) {
          show(kind === 'refresh' ? 'checking' : 'idle');
          setError(kind === 'refresh' ? '正在核对平台的刷新结果。' : '未能重新获取二维码，请核对当前登录状态。');
        }
      } finally {
        acting = false; current.busy = false;
        if (active()) {
          setWorking(false);
          if (readAgain && watch) watch.since = Date.now();
          if (readAgain) void current.read(); else schedule();
        }
      }
    };
    current.read = async () => {
      if (!active() || current.busy || !visible()) return;
      clearTimeout(current.timer); current.busy = true; setWorking(true);
      try {
        const next = await getQr();
        if (active()) observe(next);
      } catch {
        if (active()) {
          latest = { state: 'unavailable' }; setQr(latest);
          if (watch) watch.consecutive = 0;
          if (!acting) show('idle');
          setError('暂时无法读取二维码，请重试，或打开平台验证窗口。');
        }
      } finally {
        current.busy = false;
        if (active()) {
          setWorking(false);
          const queued = pending; pending = undefined;
          if (queued) void runAction(queued); else schedule();
        }
      }
    };
    current.request = kind => {
      if (!active() || acting || latest.state !== 'expired' || !latest.pageId || !latest.version) return;
      if (kind === 'restart' && (!restart.current || !['stalled', 'close_failed'].includes(phase))) return;
      acting = true;
      const action = { kind, source: latest };
      if (current.busy) { pending = action; show('queued'); }
      else void runAction(action);
    };
    const visibility = () => { clearTimeout(current.timer); if (visible()) void current.read(); };
    document.addEventListener('visibilitychange', visibility);
    setQr(latest); setError(''); show('idle'); void current.read();
    return () => {
      current.active = false; current.controller.abort(); clearTimeout(current.timer);
      document.removeEventListener('visibilitychange', visibility);
      if (poll.current === current) poll.current = null;
    };
  }, [id, platform, interactive, closed, isActive, closeForRestart]);

  const labels: Record<QrState, string> = {
    starting: '正在准备二维码…', ready: `使用${scanApp(platform, platformName)} App 扫码`, expired: '二维码已失效',
    verification_required: '需要补充平台验证', unavailable: '暂时无法读取二维码', closed: '本次登录窗口已结束',
  };
  const operationBusy = ['queued', 'refreshing', 'restarting'].includes(recovery);
  const canReplace = !!onRestart && qr.state === 'expired' && ['stalled', 'close_failed'].includes(recovery);
  let status = labels[closed ? 'closed' : qr.state];
  if (!closed) {
    if (recovery === 'queued') status = '正在核对二维码…';
    if (recovery === 'refreshing' || recovery === 'checking') status = '正在等待平台更新二维码…';
    if (canReplace) status = recovery === 'close_failed' ? '旧登录尚未确认结束' : '平台未更新二维码';
    if (recovery === 'restarting') status = '正在重新获取二维码…';
    if (qr.awaitingPhone) status = '已扫码，请在手机上确认';
  }
  return <section className={`platform-login platform-login--${interactive ? 'interactive' : 'qr'}`} aria-label={`${platformName}扫码登录`}>
    {interactive && !closed ? <>
      <button className="btn btn-sm" type="button" onClick={() => { setQr({ state: 'starting' }); setError(''); setNeedsHelp(false); setInteractive(false); }}>返回扫码</button>
      <RemoteLoginView remote={remote} keepSessionOnUnmount />
    </> : <>
      <div className="platform-login-qr-card" aria-busy={working}>
        {qr.state === 'ready' && !closed ? <img src={qr.image} alt={`${platformName}登录二维码`} />
          : <div className="platform-login-qr-placeholder" aria-hidden="true">{qr.state === 'starting' ? <span className="spinner" /> : qr.state === 'expired' ? '↻' : '◎'}</div>}
        <strong role="status">{status}</strong>
        <p>{closed ? '请查看账号状态；如未完成，可关闭后重新连接。' : canReplace ? '可重新获取二维码，再用手机扫码。' : qr.state === 'expired' ? '点击刷新后扫描新二维码。'
          : qr.state === 'verification_required' ? '打开平台窗口，由你本人完成短信或安全验证。'
            : qr.state === 'unavailable' ? '暂未获取到可用二维码，可重试或改用平台登录。'
            : '扫码后在手机上确认，账号身份核验完成后会自动更新。'}</p>
      </div>
      {error && <p className="remote-login-error" role="alert">{error}</p>}
      {!closed && <div className="platform-login-actions">
        {canReplace ? <button className="btn btn-primary" type="button" onClick={() => poll.current?.request('restart')}>重新获取二维码</button>
          : qr.state === 'expired' && qr.pageId && qr.version
          ? <button className="btn btn-primary" type="button" disabled={operationBusy || recovery === 'checking'} onClick={() => poll.current?.request('refresh')}>刷新二维码</button>
          : ['unavailable', 'starting'].includes(qr.state) && <button className="btn btn-sm" type="button" disabled={working} onClick={() => void poll.current?.read()}>重新读取</button>}
        {needsHelp || ['unavailable', 'verification_required'].includes(qr.state)
          ? <button className="btn btn-sm btn-ghost" type="button" disabled={operationBusy} onClick={() => setInteractive(true)}>打开平台验证窗口</button>
          : <button className="btn btn-sm btn-ghost" type="button" disabled={operationBusy} onClick={() => setNeedsHelp(true)}>扫码遇到问题</button>}
      </div>}
    </>}
    {onClose && <button className="btn btn-sm remote-login-cancel" type="button" onClick={onClose}>结束本次登录</button>}
  </section>;
}

export default function PlatformLoginView(props: Props) {
  return <QrLoginView key={`${props.remote.platform}:${props.remote.id}`} {...props} />;
}

/** Existing platform QR APIs stay browser-free; their parent owns polling/restart. */
export function LegacyPlatformLoginView({ platform, name, state, image, onRefresh, attempt, onSettled }: {
  platform: string; name?: string; state: string; image?: string; onRefresh?: () => Promise<void>;
  attempt?: RemoteLoginDescriptor; onSettled?: Props['onSettled'];
}) {
  const [retrying, setRetrying] = useState(false), [error, setError] = useState('');
  const [help, setHelp] = useState(false), [endedKey, setEndedKey] = useState('');
  useLoginOwnership(attempt, onSettled, remote => setEndedKey(`${remote.platform}:${remote.id}`));
  const ended = !!attempt && endedKey === `${attempt.platform}:${attempt.id}`;
  const inFlight = useRef(false), alive = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const label = name || platformNames[platform] || '平台';
  const ready = state === 'qr_ready' && !!image && !retrying && !ended;
  const status = ended ? '本次登录窗口已结束' : retrying ? '正在更新二维码…' : state === 'qr_ready' && image ? `使用${scanApp(platform, label)} App 扫码`
    : state === 'scanned' ? '已扫码，请在手机上确认'
      : ['verifying', 'success'].includes(state) ? '正在核对账号身份…'
        : state === 'expired' ? '二维码已失效' : state === 'sms_required' ? '需要短信验证'
          : ['error', 'window_login'].includes(state) ? '暂未获取到可用二维码' : '正在准备二维码…';
  const refresh = async () => {
    if (inFlight.current || !onRefresh) return;
    inFlight.current = true; setRetrying(true); setError('');
    try { await onRefresh(); }
    catch { if (alive.current) setError('二维码更新未完成，请重试。'); }
    finally { inFlight.current = false; if (alive.current) setRetrying(false); }
  };
  return <section className="platform-login platform-login--qr" aria-label={`${label}扫码登录`}>
    <div className="platform-login-qr-card" aria-busy={retrying}>
      {ready ? <img src={image} alt={`${label}登录二维码`} />
        : <div className="platform-login-qr-placeholder" aria-hidden="true">{['starting', 'unknown', 'scanned', 'verifying'].includes(state) || retrying ? <span className="spinner" /> : '↻'}</div>}
      <strong role="status">{status}</strong>
      <p>{state === 'expired' ? '刷新后扫描新二维码，旧二维码已停止显示。'
        : state === 'window_login' ? '此平台暂未提供可用二维码，可改用平台登录。'
          : state === 'sms_required' ? '请在下方输入平台发送的验证码。'
            : '扫码后在手机上确认，账号身份仍以平台验证结果为准。'}</p>
    </div>
    {error && <p className="remote-login-error" role="alert">{error}</p>}
    <div className="platform-login-actions">
      {onRefresh && ['expired', 'error'].includes(state) && <button className="btn btn-primary" type="button" disabled={retrying} onClick={() => void refresh()}>{state === 'expired' ? '刷新二维码' : '重新获取二维码'}</button>}
      {state === 'window_login' && <button className="btn btn-sm" type="button" onClick={() => setHelp(true)}>查看平台登录说明</button>}
    </div>
    {help && <p className="platform-login-manual-hint" role="status">请在运行响芽的设备上，打开本次平台登录窗口完成验证。若无法看到窗口，请关闭后重试或联系管理员。</p>}
  </section>;
}
