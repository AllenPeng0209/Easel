import { projectStorageKey, projectUrl, projectFetch, ACTIVE_PROJECT_ID, ACTIVE_USER_ID } from './lib/projectScope';
import { ProjectBreadcrumb, useIpPermissions } from './components/ProjectSpace';
import { lazy, Suspense, useState, useEffect, useCallback, useRef } from 'react';
import Sidebar from './components/Sidebar';
import type { Page } from './components/Sidebar';
import ChatPage from './components/ChatPage';
import SkillPage from './components/SkillPage';
import OutputsPage from './components/OutputsPage';
import AccountsPage from './components/AccountsPage';
import ProfilePage from './components/ProfilePage';
import DashboardPage from './components/DashboardPage';
import TrendsPage from './components/TrendsPage';
import CalendarPage from './components/CalendarPage';
import IdeasPage from './components/IdeasPage';
import PublishPage from './components/PublishingPage';
import BreakdownPage from './components/BreakdownPage';
import SubNav from './components/SubNav';
import OnboardingWizard from './components/OnboardingWizard';
import SettingsPanel from './components/SettingsPanel';
import { fetchStatus, fetchPersonas, streamChat, fetchLastTurn, stopChat, fetchChatSessions, syncChatSessions, deleteChatSession } from './lib/api';
import type { PersonaItem, UploadedFile, ChatQuestion, ChatQuestionClosed } from './lib/api';
import { reduceAgentEvents } from './lib/agentEvents';
import { fetchBusinessObject, parseBusinessHash } from './lib/businessObjectApi';
import type { BusinessTarget } from './lib/businessObjectApi';
import type { AgentEvent } from './lib/agentEvents';
import { mergeSessionCatalog, nextSessionRevision } from './lib/sessionSync';
import { confirmChatStop, enqueueMessage, nextQueuedMessage } from './lib/chatControl';
import { questionStatus } from './lib/api';
import { deleteSession as deleteRemoteSession } from './lib/api';
import {
  loadSessions,
  saveSessions,
  createSession,
  updateSessionTitle,
  loadActiveId,
  saveActiveId,
} from './lib/store';
import type { ChatSession, ChatMessage, StreamState } from './lib/store';

const TAB_SESSION_KEY = projectStorageKey('easel_tab_session');
const ONBOARDING_SEEN_KEY = projectStorageKey('easel_onboarding_seen');
const VideoWorkshopPage = lazy(() => import('./components/VideoWorkshopPage'));
const InteractionCenterPage = lazy(() => import('./components/InteractionCenterPage'));

interface LiveTurnAccumulator {
  content: string; thinking: string; steps: string[]; questions: ChatQuestion[]; events: AgentEvent[];
  stopRequested?: boolean;
  stopConfirmed?: boolean;
}

function onboardingSeen(): boolean {
  const current = localStorage.getItem(ONBOARDING_SEEN_KEY);
  if (current) return true;
  if (ACTIVE_PROJECT_ID !== 'default' || ACTIVE_USER_ID) return false;
  const previousKey = `${['post', 'craft'].join('')}_onboarding_seen`;
  const previous = localStorage.getItem(previousKey);
  if (previous) {
    localStorage.setItem(ONBOARDING_SEEN_KEY, previous);
    localStorage.removeItem(previousKey);
    return true;
  }
  return false;
}

export default function App() {
  const { canOperate, isPlatformAdmin } = useIpPermissions();
  const [currentPage, setCurrentPage] = useState<Page>(() => canOperate ? 'chat' : 'dashboard');
  const [workshopEntry, setWorkshopEntry] = useState(0);
  const [interactionEntry, setInteractionEntry] = useState(0);
  const [businessEntry, setBusinessEntry] = useState<BusinessTarget | null>(null);
  const [businessNotice, setBusinessNotice] = useState('');
  const businessNavigation = useRef(0);
  const [personas, setPersonas] = useState<PersonaItem[]>([]);
  const [selectedPersona, setSelectedPersona] = useState('');
  const [sessions, setSessions] = useState<ChatSession[]>(() => loadSessions());
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [gatewayStatus, setGatewayStatus] = useState('connecting');
  const [apiDirect, setApiDirect] = useState(false);
  const [agentRuntime, setAgentRuntime] = useState('openclaw');
  const [showRecommend, setShowRecommend] = useState(false);
  const [showWizard, setShowWizard] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyReady, setHistoryReady] = useState(false);
  const [navigationReady, setNavigationReady] = useState(false);
  const [historyNotice, setHistoryNotice] = useState('');
  const [unreadSessions, setUnreadSessions] = useState<Record<string, boolean>>({});
  const [historyRetry, setHistoryRetry] = useState(0);
  // 装完还没配模型时，首开要把用户直接带到模型设置页（安装脚本不再在终端问 Key）
  const [needModelSetup, setNeedModelSetup] = useState(false);

  // 对话里的「产物路径 → 内容库」跳转：linkifyOutputs 把目录路径生成为
  // `#/outputs/<路径>` 锚点，这里监听 hashchange 切页并带上下文，随后清掉 hash
  // （不污染地址栏与前进/后退历史）。
  const [outputsJump, setOutputsJump] = useState('');
  useEffect(() => {
    const onHash = () => {
      const m = window.location.hash.match(/^#\/outputs\/(.+)$/);
      if (!m) return;
      let path = m[1];
      try { path = decodeURIComponent(path); } catch { /* 保留原样 */ }
      setOutputsJump(path);
      setCurrentPage('outputs');
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    };
    window.addEventListener('hashchange', onHash);
    onHash();
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const clearOutputsJump = useCallback(() => setOutputsJump(''), []);

  useEffect(() => {
    let mounted = true;
    let controller: AbortController | undefined;
    const onHash = async () => {
      const hash = window.location.hash;
      if (!hash.startsWith('#/business/')) return;
      const generation = ++businessNavigation.current;
      controller?.abort();
      controller = new AbortController();
      const target = parseBusinessHash(hash);
      if (!target || !canOperate) {
        setBusinessNotice('业务链接不属于当前 IP，或当前账号无权访问。');
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
        return;
      }
      setBusinessNotice('正在核对业务对象…');
      try {
        const record = await fetchBusinessObject(target.kind, target.id, controller.signal);
        if (!mounted || generation !== businessNavigation.current || window.location.hash !== hash) return;
        const expected = target.kind === 'idea' ? 'ideas' : 'workshop';
        if (!record.actions.some(action => action.kind === 'open' && action.target === expected && action.entityId === target.id)) {
          throw new Error('No current navigation action');
        }
        setBusinessEntry(target);
        if (target.kind === 'workshop') setWorkshopEntry(value => value + 1);
        setCurrentPage(expected);
        setBusinessNotice('');
      } catch {
        if (!mounted || generation !== businessNavigation.current) return;
        setBusinessNotice('业务对象不可用或当前账号无权访问，请在当前 IP 核对。');
      }
      if (mounted && generation === businessNavigation.current && window.location.hash === hash) {
        window.history.replaceState(null, '', window.location.pathname + window.location.search);
      }
    };
    window.addEventListener('hashchange', onHash);
    void onHash();
    return () => { mounted = false; controller?.abort(); window.removeEventListener('hashchange', onHash); };
  }, [canOperate]);

  // 挂载时决定进哪个会话。规则：
  //  - 同一标签刷新（sessionStorage 记着本标签的会话）→ 直接续上（同标签不算冲突）。
  //  - 新开标签/窗口 → 若「上次活跃会话」正被另一个存活标签占用（跨标签 BroadcastChannel 探测），
  //    则开一个新会话，避免两个窗口撞同一会话 → openclaw 并发 takeover 崩溃（后端还有 flock 兜底）。
  //  - 否则续上上次会话（保留「关页重开续接」的体验）。
  useEffect(() => {
    const existing = loadSessions();
    let ch: BroadcastChannel | null = null;
    try { ch = new BroadcastChannel(projectStorageKey('easel-session')); } catch { ch = null; }

    const settle = (id: string, sess: ChatSession[]) => {
      setSessions(sess);
      setActiveSessionId(id);
      const s = sess.find((x) => x.id === id);
      if (s) setSelectedPersona(s.persona || '');
      try { sessionStorage.setItem(TAB_SESSION_KEY, id); } catch { /* ignore */ }
      ch?.postMessage({ type: 'claim', sessionId: id });
      setNavigationReady(true);
    };
    const openNew = (sess: ChatSession[]) => {
      const ns = createSession();
      const updated = [ns, ...sess];
      saveSessions(updated);
      settle(ns.id, updated);
    };

    // 持久监听：别的标签问「谁在用会话 X」时，若正是本标签当前会话就应答 owned
    const onMsg = (e: MessageEvent) => {
      const d = e.data as { type?: string; sessionId?: string } | null;
      if (d?.type === 'query' && d.sessionId && d.sessionId === activeIdRef.current) {
        ch?.postMessage({ type: 'owned', sessionId: d.sessionId });
      }
    };
    ch?.addEventListener('message', onMsg);

    // 1) 本标签刷新：续本标签原会话
    let tabOwn: string | null = null;
    try { tabOwn = sessionStorage.getItem(TAB_SESSION_KEY); } catch { tabOwn = null; }
    if (tabOwn && existing.find((s) => s.id === tabOwn)) {
      settle(tabOwn, existing);
      return () => { ch?.removeEventListener('message', onMsg); ch?.close(); };
    }

    // 2) 新标签：候选=上次活跃会话；先跨标签问有没有别的活标签占着它
    const lastId = loadActiveId();
    const candidate = lastId && existing.find((s) => s.id === lastId) ? lastId : null;
    if (candidate && ch) {
      let taken = false;
      const probe = (e: MessageEvent) => {
        const d = e.data as { type?: string; sessionId?: string } | null;
        if (d?.type === 'owned' && d.sessionId === candidate) taken = true;
      };
      ch.addEventListener('message', probe);
      ch.postMessage({ type: 'query', sessionId: candidate });
      const t = setTimeout(() => {
        ch?.removeEventListener('message', probe);
        if (taken) openNew(existing);      // 另一个窗口在用 → 开新会话
        else settle(candidate, existing);  // 没人占 → 续上
      }, 250);
      return () => { clearTimeout(t); ch?.removeEventListener('message', probe); ch?.removeEventListener('message', onMsg); ch?.close(); };
    }

    // 3) 无候选 / 不支持 BroadcastChannel：退化为原逻辑（复用空会话或新建；后端 flock 兜底防崩）
    if (candidate) {
      settle(candidate, existing);
    } else {
      const empty = existing.find((s) => s.messages.length === 0);
      if (empty) settle(empty.id, existing);
      else openNew(existing);
    }
    return () => { ch?.removeEventListener('message', onMsg); ch?.close(); };
  }, []);

  // 持久化当前活跃会话 id，重开网页据此续接上次对话（修复"今天再问就忘了"）。
  // 仅在非空时写：避免挂载首刷 activeSessionId 尚为 null 时误清掉已存的 id。
  // 同时更新本标签的 sessionStorage 标记：手动切会话/新建后刷新本标签仍续在正确会话上。
  useEffect(() => {
    if (activeSessionId) {
      saveActiveId(activeSessionId);
      try { sessionStorage.setItem(TAB_SESSION_KEY, activeSessionId); } catch { /* ignore */ }
    }
  }, [activeSessionId]);

  // Fetch status on mount — 真实反映 gateway 状态 + 首次引导检测
  useEffect(() => {
    // 先问 bootstrap：模型没配好时，画像推荐弹窗要让位 —— 一次只问用户一件事，
    // 而且画像配得再好，没有模型也对话不了。
    const bootstrap = isPlatformAdmin ? projectFetch(projectUrl('./api/settings/bootstrap'))
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null) : Promise.resolve(null);

    Promise.all([fetchStatus(), bootstrap])
      .then(([data, boot]) => {
        setPersonas(data.personas || []);
        setGatewayStatus(data.gateway ? 'connected' : 'disconnected');
        setApiDirect(data.transport === 'api');
        setAgentRuntime(data.runtime || 'openclaw');
        const unconfigured = !!boot && boot.modelConfigured === false;
        if (unconfigured && isPlatformAdmin) {
          // 直接打开设置面板（它默认就停在「模型配置 · 对话」页）
          setNeedModelSetup(true);
          setSettingsOpen(true);
          return;
        }
        // 首次使用：没有任何个性化画像 且 未看过引导 → 推荐配置
        if (canOperate && (data.personas || []).length === 0 && !onboardingSeen()) {
          setShowRecommend(true);
        }
      })
      .catch(() => {
        setGatewayStatus('disconnected');
      });
  }, [canOperate, isPlatformAdmin]);

  const activeSession = sessions.find((s) => s.id === activeSessionId) || null;

  // 最新 sessions 的 ref，供回调里读取而不必进依赖数组（避免闭包过期/频繁重建）
  const sessionsRef = useRef(sessions);
  useEffect(() => { sessionsRef.current = sessions; }, [sessions]);

  // 当前活跃会话 id 的 ref：供跨标签「谁在用会话 X」查询时即时应答（见挂载 effect）
  const activeIdRef = useRef<string | null>(activeSessionId);
  useEffect(() => { activeIdRef.current = activeSessionId; }, [activeSessionId]);

  useEffect(() => {
    if (!canOperate || !navigationReady) return;
    let cancelled = false;
    void fetchChatSessions().then(result => {
      if (cancelled) return;
      const remote = Array.isArray(result.sessions) ? result.sessions : [];
      setSessions(previous => {
        const merged = mergeSessionCatalog(previous, remote, result.deletedIds);
        saveSessions(merged);
        return merged;
      });
      if (!sessionsRef.current.some(session => session.messages.length) && remote.some(session => session.messages.length)) {
        const restored = remote.find(session => session.messages.length && !session.archived);
        if (restored) { setActiveSessionId(restored.id); setSelectedPersona(restored.persona || ''); }
      }
      setHistoryNotice('');
    }).catch(() => {
      if (!cancelled) setHistoryNotice('服务器历史暂不可用，当前内容仍保存在此浏览器。');
    }).finally(() => { if (!cancelled) setHistoryReady(true); });
    return () => { cancelled = true; };
  }, [canOperate, navigationReady]);

  useEffect(() => {
    if (!historyReady || !canOperate) return;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    const timer = setTimeout(() => {
      void syncChatSessions(sessions).then(result => {
        if (cancelled) return;
        setHistoryNotice('');
        setSessions(previous => {
          const merged = mergeSessionCatalog(previous, result.sessions || [], result.deletedIds);
          if (merged.length === previous.length && merged.every((item, index) => item === previous[index])) return previous;
          saveSessions(merged);
          return merged;
        });
      }).catch(() => {
        if (cancelled) return;
        setHistoryNotice('历史尚未同步到服务器，请保留此浏览器；稍后会重新尝试。');
        retryTimer = setTimeout(() => setHistoryRetry(value => value + 1), 5000);
      });
    }, 500);
    return () => { cancelled = true; clearTimeout(timer); if (retryTimer) clearTimeout(retryTimer); };
  }, [sessions, historyReady, canOperate, historyRetry]);

  useEffect(() => {
    const storageFailure = () => setHistoryNotice('浏览器存储已满，内容暂保留在本页内存，正在尝试服务器同步；请勿关闭页面。');
    const retry = () => setHistoryRetry(value => value + 1);
    window.addEventListener('easel:history-storage-error', storageFailure);
    window.addEventListener('online', retry);
    return () => { window.removeEventListener('easel:history-storage-error', storageFailure); window.removeEventListener('online', retry); };
  }, []);

  // ---- 流式对话：状态与生命周期都放在 App（永不卸载），切页/切 ChatPage 都不中断/丢失 ----
  const [streams, setStreams] = useState<Record<string, StreamState>>({});
  const streamCtl = useRef<Record<string, AbortController>>({});
  const streamAcc = useRef<Record<string, LiveTurnAccumulator>>({});
  const finalizers = useRef(new Map<string, { owner: object; finish: () => void; timer?: ReturnType<typeof setTimeout> }>());
  const answeredRef = useRef<Set<string>>(new Set());   // 已提交答案的 question id：重放/恢复不再重现
  // ---- 打字机：分批到达的 token 按节奏吐给界面 ----
  const typingBuf = useRef<Record<string, string>>({});
  const typingTimer = useRef<Record<string, ReturnType<typeof setInterval>>>({});
  const TYPING_INTERVAL = 12;          // 每 tick 间隔 ms（短回复约 83 字/秒：快且有逐字感）

  const pumpTyping = (sessionId: string) => {
    const buf = typingBuf.current[sessionId] || '';
    if (!buf) { clearTyping(sessionId); return; }
    // 动态步长：<80 字逐字吐（83字/秒），每满 80 字每 tick 多吐 1 字，长文更快
    const step = Math.max(1, Math.floor(buf.length / 80));
    const take = buf.slice(0, step);
    typingBuf.current[sessionId] = buf.slice(step);
    const a = streamAcc.current[sessionId]; if (!a) { clearTyping(sessionId); return; }
    a.content += take;
    setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], content: a.content } } : p));
  };
  const startTypingPump = (sessionId: string) => {
    if (typingTimer.current[sessionId]) return;
    typingTimer.current[sessionId] = setInterval(() => pumpTyping(sessionId), TYPING_INTERVAL);
  };
  const ensureTypingPump = (sessionId: string) => {
    if (!typingTimer.current[sessionId]) startTypingPump(sessionId);
  };
  const clearTyping = (sessionId: string) => {
    const t = typingTimer.current[sessionId];
    if (t) { clearInterval(t); delete typingTimer.current[sessionId]; }
  };
  /** 收尾冲刷：把剩余队列立刻吐完（避免结束瞬间内容被截断）。 */
  const flushTyping = (sessionId: string) => {
    clearTyping(sessionId);
    const buf = typingBuf.current[sessionId] || '';
    if (!buf) return;
    typingBuf.current[sessionId] = '';
    const a = streamAcc.current[sessionId]; if (!a) return;
    a.content += buf;
    setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], content: a.content } } : p));
  };

  const appendAssistant = useCallback((sessionId: string, msg: ChatMessage, sessionKey?: string) => {
    setSessions((prev) => {
      const next = prev.map((s) =>
        s.id === sessionId
          ? { ...s, updated: nextSessionRevision(s), messages: [...s.messages, { ...msg, created: Date.now() }], sessionKey: sessionKey || s.sessionKey, pendingTurnId: undefined }
          : s);
      saveSessions(next);
      return next;
    });
    if (activeIdRef.current !== sessionId) setUnreadSessions(previous => ({ ...previous, [sessionId]: true }));
  }, []);

  const clearStream = useCallback((sessionId: string) => {
    const pending = finalizers.current.get(sessionId);
    if (pending?.timer) clearTimeout(pending.timer);
    finalizers.current.delete(sessionId);
    // 打字机收尾：剩余队列立刻吐出，避免结束瞬间内容被截断
    flushTyping(sessionId);
    delete streamCtl.current[sessionId];
    delete streamAcc.current[sessionId];
    setStreams((prev) => {
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });
  }, []);

  const finalizeStream = useCallback((sessionId: string, owner: typeof streamAcc.current[string],
    sessionKey?: string, error?: Error) => {
    if (streamAcc.current[sessionId] !== owner || finalizers.current.has(sessionId)) return;
    const pending: { owner: object; finish: () => void; timer?: ReturnType<typeof setTimeout> } = {
      owner,
      finish: () => {
        if (finalizers.current.get(sessionId) !== pending || streamAcc.current[sessionId] !== owner) return;
        if (owner.stopRequested && !owner.stopConfirmed) return;
        finalizers.current.delete(sessionId);
        if (pending.timer) clearTimeout(pending.timer);
        flushTyping(sessionId);
        appendAssistant(sessionId, {
          role: 'assistant', content: owner.content + (owner.stopConfirmed ? '\n\n_（已停止）_' : error ? `\n\nError: ${error.message}` : ''),
          thinking: owner.thinking || undefined, activity: owner.steps.join('\n') || undefined,
          questions: owner.questions.length ? [...owner.questions] : undefined,
          events: owner.events.length ? [...owner.events] : undefined,
          status: owner.stopConfirmed ? 'stopped' : error ? 'failed' : 'completed',
        }, sessionKey);
        if (error) setSessions(previous => previous.map(session => session.id === sessionId
          ? { ...session, queuePaused: true, updated: nextSessionRevision(session) } : session));
        clearStream(sessionId);
        try { sessionStorage.removeItem(projectStorageKey(`easel_pending_turn:${sessionId}`)); } catch {}
      },
    };
    finalizers.current.set(sessionId, pending);
    const poll = () => {
      if (finalizers.current.get(sessionId) !== pending || streamAcc.current[sessionId] !== owner) return;
      if ((owner.stopRequested && !owner.stopConfirmed) || (typingBuf.current[sessionId] && document.visibilityState !== 'hidden')) {
        pending.timer = setTimeout(poll, 60);
      } else pending.finish();
    };
    poll();
  }, [appendAssistant, clearStream]);

  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState !== 'hidden') return;
      for (const sessionId of Object.keys(streamAcc.current)) flushTyping(sessionId);
      for (const pending of [...finalizers.current.values()]) pending.finish();
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
  }, []);

  const closeQuestion = useCallback((sessionId: string, closed: ChatQuestionClosed) => {
    const owner = streamAcc.current[sessionId];
    if (owner && owner.questions.some(question => question.id === closed.id)) {
      owner.questions = owner.questions.map(question => question.id === closed.id
        ? { ...question, ...closed } : question);
      setStreams(previous => previous[sessionId] ? { ...previous,
        [sessionId]: { ...previous[sessionId], questions: [...owner.questions],
          lifecycle: previous[sessionId].lifecycle === 'stopping' || previous[sessionId].lifecycle === 'stop_failed'
            ? previous[sessionId].lifecycle : owner.questions.some(question => !question.status || question.status === 'pending') ? 'waiting_input' : 'running' } } : previous);
    }
    setSessions(previous => {
      const updated = previous.map(session => session.id === sessionId ? { ...session, updated: nextSessionRevision(session),
        messages: session.messages.map(message => ({ ...message, questions: message.questions?.map(question =>
          question.id === closed.id ? { ...question, ...closed } : question) })) } : session);
      saveSessions(updated);
      return updated;
    });
  }, []);

  const receiveQuestion = useCallback((sessionId: string, owner: typeof streamAcc.current[string], q: ChatQuestion) => {
    if (streamAcc.current[sessionId] !== owner || answeredRef.current.has(q.id) || owner.questions.some(question => question.id === q.id)) return;
    owner.questions.push({ ...q, status: q.status || 'pending' });
    setStreams(previous => previous[sessionId] ? { ...previous,
      [sessionId]: { ...previous[sessionId], questions: [...owner.questions], lifecycle: 'waiting_input' } } : previous);
    void questionStatus([q.id]).then(states => {
      const status = states[q.id]?.status;
      if (status && status !== 'pending' && status !== 'unknown') closeQuestion(sessionId, { id: q.id, ...states[q.id] });
    });
  }, [closeQuestion]);

  const receiveAgentEvent = useCallback((sessionId: string, owner: typeof streamAcc.current[string], event: AgentEvent) => {
    if (streamAcc.current[sessionId] !== owner) return;
    owner.events = reduceAgentEvents(owner.events, event);
    setStreams(previous => previous[sessionId] ? { ...previous,
      [sessionId]: { ...previous[sessionId], events: [...owner.events], stillWorking: undefined } } : previous);
    if (event.kind === 'decision' && event.questionId) closeQuestion(sessionId, {
      id: event.questionId, status: event.status || 'answered', answers: event.answers,
      resolvedBy: event.source || 'human',
    });
  }, [closeQuestion]);

  // 启动一次流式（fetch + 累积 + 回调）——只管流，不动消息列表
  const startStream = useCallback((
    sessionId: string,
    text: string,
    persona: string | undefined,
    attachments: UploadedFile[] = [],
    requestedTurnId?: string,
    topicRef?: ChatMessage['topicRef'],
  ) => {
    const turnId = requestedTurnId || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    try { sessionStorage.setItem(projectStorageKey(`easel_pending_turn:${sessionId}`), turnId); } catch { /* ignore */ }
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === sessionId ? { ...s, pendingTurnId: turnId, updated: nextSessionRevision(s) } : s));
      saveSessions(next); return next;
    });
    const owner = { content: '', thinking: '', steps: [] as string[], questions: [] as ChatQuestion[], events: [] as AgentEvent[] };
    streamAcc.current[sessionId] = owner;
    setStreams((prev) => ({ ...prev, [sessionId]: { content: '', thinking: '', activity: '', questions: [], events: [], lifecycle: 'running' } }));
    // 打字机队列：流式事件按批到达（OpenClaw 攒批），前端按字符节奏显示，体验逐字浮现。
    typingBuf.current[sessionId] = '';
    startTypingPump(sessionId);
    streamCtl.current[sessionId] = streamChat(
      text, persona, sessionId,
      (chunk) => {
        const a = streamAcc.current[sessionId]; if (a !== owner) return;
        // 不直接追加 content——进打字机队列，pump 按节奏吐出（切会话不中断，队列归属 sessionId）
        typingBuf.current[sessionId] = (typingBuf.current[sessionId] || '') + chunk;
        ensureTypingPump(sessionId);
      },
      (sessionKey) => {
        // done 不能立即 flush——token 和 done 几乎同时到达（OpenClaw 攒批），
        // 立即 flush 会把整包瞬间冲出，打字机白做。等队列吐完再落盘。
        finalizeStream(sessionId, owner, sessionKey);
      },
      (err) => {
        finalizeStream(sessionId, owner, undefined, err);
      },
      (thinkChunk) => {
        const a = streamAcc.current[sessionId]; if (a !== owner) return;
        a.thinking = (a.thinking + thinkChunk).slice(-4000);
        // 有真实思考流 → 清掉防呆提示（不再显示「未卡住」）
        setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], thinking: a.thinking, stillWorking: undefined } } : p));
      },
      (status) => {
        const a = streamAcc.current[sessionId]; if (a !== owner) return;
        if (a.steps[a.steps.length - 1] !== status) a.steps.push(status);
        // 有真实活动状态 → 清掉防呆提示，让真实状态占据活动行
        setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], activity: status, stillWorking: undefined } } : p));
      },
      // onInterrupted：SSE 被中断（长任务时代理掐断），但后端仍在跑并会落盘完整结果。
      // streamChat 会按 eventId 自动重连并补发遗漏事件；这里只更新用户可见状态。
      () => {
        if (streamAcc.current[sessionId] !== owner) return;
        setStreams((p) => (p[sessionId]
          ? { ...p, [sessionId]: { ...p[sessionId], activity: '⏳ 连接中断，正在自动续接…' } } : p));
      },
      turnId,
      false,
      undefined,
      attachments,
      (q) => {
        // ask_user 问答题：追加进流式状态（去重），ChatPage 渲染为选项卡片
        // 重放可能带已解决/已过期的旧问题，先查状态只留 pending（失败则保留）
        receiveQuestion(sessionId, owner, q);
      },
      // onHeartbeat：防呆心跳（30s 静默）。只设独立的「未卡住」提示，绝不写 activity/thinking → 不顶掉真实状态。
      (note) => { if (streamAcc.current[sessionId] === owner) setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], stillWorking: note } } : p)); },
      (closed) => closeQuestion(sessionId, closed),
      (event) => receiveAgentEvent(sessionId, owner, event),
      topicRef,
    );
  }, [finalizeStream, closeQuestion, receiveQuestion, receiveAgentEvent]);

  // 刷新/重开页面后按 eventId=0 重放当前 job，再继续实时 tail；旧任务无事件日志时退回最终快照。
  const resumePendingTurn = useCallback((sessionId: string) => {
    if (streamCtl.current[sessionId] || streamAcc.current[sessionId]) return;  // 本标签正在跑，不插手
    const s = sessionsRef.current.find((x) => x.id === sessionId);
    const last = s?.messages[s.messages.length - 1];
    if (!last || last.role !== 'user') return;   // 没有悬空的用户消息 = 无需恢复
    let turnId = s.pendingTurnId;
    try { turnId = sessionStorage.getItem(projectStorageKey(`easel_pending_turn:${sessionId}`)) || turnId; } catch { /* use persisted id */ }
    const owner = { content: '', thinking: '', steps: [] as string[], questions: [] as ChatQuestion[], events: [] as AgentEvent[] };
    streamAcc.current[sessionId] = owner;
    setStreams((p) => ({ ...p, [sessionId]: { content: '', thinking: '', activity: '⏳ 正在接回上一轮结果…', questions: [] } }));
    typingBuf.current[sessionId] = '';
    startTypingPump(sessionId);
    if (!turnId) {
      void fetchLastTurn(sessionId).then((r) => {
        if (streamAcc.current[sessionId] !== owner) return;
        if (r.status === 'done') appendAssistant(sessionId, { role: 'assistant', content: r.text || '（无输出）', events: r.events, questions: r.questions });
        clearStream(sessionId);
      }).catch(() => { if (streamAcc.current[sessionId] === owner) clearStream(sessionId); });
      return;
    }
    streamCtl.current[sessionId] = streamChat(
      '', undefined, sessionId,
      (chunk) => {
        const a = streamAcc.current[sessionId]; if (a !== owner) return;
        typingBuf.current[sessionId] = (typingBuf.current[sessionId] || '') + chunk;
        ensureTypingPump(sessionId);
      },
      (sessionKey) => {
        finalizeStream(sessionId, owner, sessionKey);
      },
      (err) => {
        finalizeStream(sessionId, owner, undefined, err);
      },
      (chunk) => { const a = streamAcc.current[sessionId]; if (a === owner) a.thinking = (a.thinking + chunk).slice(-4000); },
      (status) => {
        const a = streamAcc.current[sessionId]; if (a !== owner) return;
        if (a.steps[a.steps.length - 1] !== status) a.steps.push(status);
        setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], activity: status } } : p));
      },
      () => { if (streamAcc.current[sessionId] === owner) setStreams((p) => (p[sessionId]
        ? { ...p, [sessionId]: { ...p[sessionId], activity: '⏳ 正在自动续接…' } } : p)); },
      turnId,
      true,
      () => {
        // The event log may disappear after a backend restart. Prefer the
        // completed per-session snapshot; otherwise terminate stale recovery.
        void fetchLastTurn(sessionId, turnId).then((r) => {
          if (streamAcc.current[sessionId] !== owner) return;
          if (r.status === 'done') {
            appendAssistant(sessionId, { role: 'assistant', content: r.text || '（无输出）', events: r.events, questions: r.questions });
          } else {
            appendAssistant(sessionId, {
              role: 'assistant',
              content: '上一轮任务记录已失效，无法继续恢复。请重新发送上一条消息。',
            });
          }
          clearStream(sessionId);
          try { sessionStorage.removeItem(projectStorageKey(`easel_pending_turn:${sessionId}`)); } catch { /* ignore */ }
        }).catch(() => {
          if (streamAcc.current[sessionId] !== owner) return;
          appendAssistant(sessionId, {
            role: 'assistant', content: '上一轮任务记录已失效，请重新发送上一条消息。',
          });
          clearStream(sessionId);
        });
      },
      undefined,   // attachments: 恢复轮次无新附件
      (q) => {
        receiveQuestion(sessionId, owner, q);
      },
      // onHeartbeat：同上，独立的「未卡住」提示，不覆盖 activity/thinking。
      (note) => { if (streamAcc.current[sessionId] === owner) setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], stillWorking: note } } : p)); },
      (closed) => closeQuestion(sessionId, closed),
      (event) => receiveAgentEvent(sessionId, owner, event),
    );
  }, [appendAssistant, clearStream, finalizeStream, closeQuestion, receiveQuestion, receiveAgentEvent]);

  // 活跃会话确定后（含挂载首刷）尝试恢复它悬空的一轮
  useEffect(() => {
    if (activeSessionId) resumePendingTurn(activeSessionId);
  }, [activeSessionId, activeSession?.pendingTurnId, activeSession?.updated, historyReady, resumePendingTurn]);

  // 落用户消息（可选先把 messages 截断到 truncateAt）→ 启动流。retry/edit 都走这里。
  const sendUserAndStream = useCallback((
    sessionId: string,
    displayText: string,
    attachments: UploadedFile[] = [],
    legacyAgentText?: string,
    truncateAt?: number,
    requestedTurnId?: string,
    topicRef?: ChatMessage['topicRef'],
  ) => {
    const visible = displayText.trim();
    const agentMessage = (legacyAgentText || displayText).trim();
    if ((!agentMessage && attachments.length === 0) || streamCtl.current[sessionId] || streamAcc.current[sessionId]) return false;
    const cur = sessionsRef.current.find((s) => s.id === sessionId);
    const persona = cur ? cur.persona : selectedPersona || undefined;
    setSessions((prev) => {
      const next = prev.map((s) => {
        if (s.id !== sessionId) return s;
        const base = truncateAt != null ? s.messages.slice(0, truncateAt) : s.messages;
        const updated = {
          ...s,
          updated: nextSessionRevision(s),
          messages: [...base, {
            role: 'user',
            content: visible,
            created: Date.now(),
            ...(attachments.length ? { attachments } : {}),
            ...(topicRef ? { topicRef } : {}),
            ...(legacyAgentText && legacyAgentText !== visible ? { agentContent: legacyAgentText } : {}),
          } as ChatMessage],
        };
        updateSessionTitle(updated);
        return updated;
      });
      saveSessions(next);
      return next;
    });
    startStream(sessionId, agentMessage, persona, attachments, requestedTurnId, topicRef);
    return true;
  }, [selectedPersona, startStream]);

  const handleSendMessage = useCallback((sessionId: string, displayText: string, attachments?: UploadedFile[]) => {
    const session = sessionsRef.current.find(item => item.id === sessionId);
    if (!session || (!displayText.trim() && !attachments?.length)) return false;
    if (streamCtl.current[sessionId] || streamAcc.current[sessionId] || session.queued?.length) {
      const queued = { id: crypto.randomUUID(), text: displayText, attachments, created: Date.now() };
      setSessions(previous => {
        const next = previous.map(item => item.id === sessionId ? { ...enqueueMessage(item, queued), queuePaused: false } : item);
        saveSessions(next);
        return next;
      });
      return true;
    }
    return sendUserAndStream(sessionId, displayText, attachments);
  }, [sendUserAndStream]);

  // Queue entries survive navigation/reload. A stopped or failed queue needs an explicit resume.
  useEffect(() => {
    if (!historyReady || !canOperate) return;
    for (const session of sessions) {
      if (session.pendingTurnId && session.queued?.length) resumePendingTurn(session.id);
      const next = nextQueuedMessage(session, Boolean(streamAcc.current[session.id] || streamCtl.current[session.id] || session.pendingTurnId));
      if (!next) continue;
      setSessions(previous => {
        const updated = previous.map(item => item.id === session.id
          ? { ...item, queued: item.queued?.filter(entry => entry.id !== next.id), updated: nextSessionRevision(item) } : item);
        saveSessions(updated);
        return updated;
      });
      sendUserAndStream(session.id, next.text, next.attachments || [], undefined, undefined, `queue-${next.id}`);
    }
  }, [sessions, streams, historyReady, canOperate, resumePendingTurn, sendUserAndStream]);

  const removeQueued = useCallback((sessionId: string, messageId: string) => {
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId
        ? { ...session, queued: session.queued?.filter(message => message.id !== messageId), updated: nextSessionRevision(session) } : session);
      saveSessions(next); return next;
    });
  }, []);

  const resumeQueue = useCallback((sessionId: string) => {
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId ? { ...session, queuePaused: false, updated: nextSessionRevision(session) } : session);
      saveSessions(next); return next;
    });
  }, []);

  // 重试/编辑重发：从该用户消息处截断（丢弃它及其之后），用 text 重新发起。
  const handleResend = useCallback((
    sessionId: string,
    userIndex: number,
    displayText: string,
    attachments?: UploadedFile[],
    legacyAgentText?: string,
  ) => {
    const topicRef = sessionsRef.current.find(session => session.id === sessionId)?.messages[userIndex]?.topicRef;
    sendUserAndStream(sessionId, displayText, attachments, legacyAgentText, userIndex, undefined, topicRef);
  }, [sendUserAndStream]);

  // 热点「一键做成内容」：新开会话，把选题作为指令发出去，跳到对话页。
  const handleUseTopic = useCallback((title: string) => {
    const prompt = `围绕当前热点「${title}」：先判断它适不适合我的账号赛道；若合适，给 2-3 个差异化的二创角度，并把你最推荐的那条写成可直接发布的文案初稿。`;
    const ns = createSession(selectedPersona || undefined);
    setSessions((prev) => { const u = [ns, ...prev]; saveSessions(u); return u; });
    setActiveSessionId(ns.id);
    setCurrentPage('chat');
    sendUserAndStream(ns.id, prompt);
  }, [selectedPersona, sendUserAndStream]);

  const handleUseSavedIdea = useCallback(async (id: string) => {
    if (!canOperate) return;
    const generation = ++businessNavigation.current;
    setBusinessNotice('正在读取选题…');
    try {
      const idea = await fetchBusinessObject('idea', id);
      if (generation !== businessNavigation.current) return;
      const session = createSession(selectedPersona || undefined);
      if (idea.title.trim()) session.title = Array.from(idea.title).slice(0, 60).join('');
      setSessions(previous => {
        const next = [session, ...previous];
        saveSessions(next);
        return next;
      });
      setActiveSessionId(session.id);
      setCurrentPage('chat');
      setBusinessNotice('');
      sendUserAndStream(session.id, '请根据所选选题及来源拟定创作方向，并起草一版内容。', [], undefined,
        undefined, undefined, { id: idea.id, snapshotHash: idea.snapshotHash });
    } catch {
      if (generation === businessNavigation.current) setBusinessNotice('无法读取该选题，请在当前 IP 核对后重试。');
    }
  }, [canOperate, selectedPersona, sendUserAndStream]);

  useEffect(() => {
    const useIdea = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail?.actorId === ACTIVE_USER_ID && detail?.projectId === ACTIVE_PROJECT_ID
          && typeof detail.id === 'string' && /^[A-Za-z0-9_-]{1,160}$/.test(detail.id)) {
        void handleUseSavedIdea(detail.id);
      }
    };
    window.addEventListener('easel:use-idea', useIdea);
    return () => {
      window.removeEventListener('easel:use-idea', useIdea);
      businessNavigation.current += 1;
    };
  }, [handleUseSavedIdea]);

  const handleStopStream = useCallback(async (sessionId: string) => {
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId ? { ...session, queuePaused: true, updated: nextSessionRevision(session) } : session);
      saveSessions(next); return next;
    });
    const pending = finalizers.current.get(sessionId);
    if (pending) { pending.finish(); return; }
    const owner = streamAcc.current[sessionId];
    if (!owner) return;
    owner.stopRequested = true;
    setStreams(previous => previous[sessionId] ? { ...previous,
      [sessionId]: { ...previous[sessionId], lifecycle: 'stopping', activity: '正在停止，等待执行引擎确认…' } } : previous);
    let receipt: 'stopped' | 'already_finished';
    try { receipt = await confirmChatStop(() => stopChat(sessionId)); }
    catch (error) {
      owner.stopRequested = false;
      finalizers.current.get(sessionId)?.finish();
      if (streamAcc.current[sessionId] === owner) setStreams(previous => previous[sessionId] ? { ...previous,
        [sessionId]: { ...previous[sessionId], lifecycle: 'stop_failed', activity: error instanceof Error ? error.message : '停止失败，可再次尝试。' } } : previous);
      return;
    }
    if (streamAcc.current[sessionId] !== owner) return;
    owner.stopConfirmed = receipt === 'stopped';
    owner.stopRequested = false;
    const completed = finalizers.current.get(sessionId);
    if (completed) { completed.finish(); return; }
    streamCtl.current[sessionId]?.abort();
    flushTyping(sessionId);   // 停止时立刻把队列余字吐完，保证已到内容不丢
    const a = streamAcc.current[sessionId];
    if (a) {
      appendAssistant(sessionId, {
        role: 'assistant',
        content: (a.content || '') + (receipt === 'stopped' ? '\n\n_（已停止）_' : ''),
        thinking: a.thinking || undefined,
        activity: a.steps.join('\n') || undefined,
        questions: a.questions.length ? [...a.questions] : undefined,
        events: a.events.length ? [...a.events] : undefined,
        status: receipt === 'stopped' ? 'stopped' : 'completed',
      });
    }
    clearStream(sessionId);
    // 关键：清掉「本轮进行中」标记，否则下一句被判为「上一条还没跑完」拦下
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === sessionId ? { ...s, pendingTurnId: undefined, updated: nextSessionRevision(s) } : s));
      saveSessions(next);
      return next;
    });
    try { sessionStorage.removeItem(projectStorageKey(`easel_pending_turn:${sessionId}`)); } catch { /* ignore */ }
  }, [appendAssistant, clearStream]);

  const handleSessionRename = useCallback((id: string, title: string) => {
    const t = title.trim();
    if (!t) return;
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === id ? { ...s, title: t, updated: nextSessionRevision(s) } : s));
      saveSessions(next);
      return next;
    });
  }, []);

  const handleSessionArchive = useCallback((id: string, archived: boolean) => {
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === id ? { ...s, archived, updated: nextSessionRevision(s) } : s));
      saveSessions(next);
      return next;
    });
    // 归档当前激活会话 → 切到另一个未归档会话或新建
    if (archived && id === activeSessionId) {
      const rest = sessionsRef.current.filter((s) => s.id !== id && !s.archived);
      if (rest.length) {
        setActiveSessionId(rest[0].id);
      } else {
        const ns = createSession(selectedPersona || undefined);
        setSessions((prev) => { const u = [ns, ...prev]; saveSessions(u); return u; });
        setActiveSessionId(ns.id);
      }
      setCurrentPage('chat');
    }
  }, [activeSessionId, selectedPersona]);

  const handleNewChat = useCallback(() => {
    const newSession = createSession(selectedPersona || undefined);
    setSessions((prev) => {
      const updated = [newSession, ...prev];
      saveSessions(updated);
      return updated;
    });
    setActiveSessionId(newSession.id);
    setCurrentPage('chat');
  }, [selectedPersona]);

  const handleSessionSelect = useCallback((id: string) => {
    setActiveSessionId(id);
    setUnreadSessions(previous => ({ ...previous, [id]: false }));
    const target = sessions.find(s => s.id === id);
    if (target) {
      setSelectedPersona(target.persona || '');
    }
    setCurrentPage('chat');
  }, [sessions]);

  const handleSessionDelete = useCallback((id: string) => {
    if (!window.confirm('删除 Easel 中的这条对话？引擎原生历史可能仍保留。')) return;
    void deleteChatSession(id).catch(() => setHistoryNotice('服务器删除尚未确认，请稍后重试。'));

    const target = sessionsRef.current.find((s) => s.id === id);
    const wasRunning = Boolean(streamCtl.current[id]);
    const stopped = wasRunning
      ? stopChat(id).catch(() => ({ stopped: false }))
      : Promise.resolve({ stopped: false });
    streamCtl.current[id]?.abort();   // 删除正在流式的会话时中止其流
    clearStream(id);
    try { sessionStorage.removeItem(projectStorageKey(`easel_pending_turn:${id}`)); } catch { /* ignore */ }

    if (target?.sessionKey) {
      // Do not delete OpenClaw's session record while its agent is still
      // writing to it; the stop endpoint waits for backend cleanup first.
      void stopped.then(() => deleteRemoteSession(target.sessionKey as string)).catch(() => {});
    }

    setSessions((prev) => {
      const updated = prev.filter((s) => s.id !== id);
      saveSessions(updated);

      if (id === activeSessionId) {
        if (updated.length > 0) {
          setActiveSessionId(updated[0].id);
        } else {
          const newSession = createSession();
          updated.unshift(newSession);
          saveSessions(updated);
          setActiveSessionId(newSession.id);
        }
      }
      return updated;
    });
  }, [activeSessionId, clearStream]);

  const handleSessionPin = useCallback((id: string, pinned: boolean) => {
    setSessions(previous => {
      const next = previous.map(session => session.id === id ? { ...session, pinned, updated: nextSessionRevision(session) } : session);
      saveSessions(next); return next;
    });
  }, []);

  // 首次引导：跳过（用通用模式）
  const dismissRecommend = useCallback(() => {
    localStorage.setItem(ONBOARDING_SEEN_KEY, '1');
    setShowRecommend(false);
  }, []);

  // 打开引导向导
  const openWizard = useCallback(() => {
    setShowRecommend(false);
    setShowWizard(true);
  }, []);

  // 画像创建完成
  const handleProfileCreated = useCallback((name: string) => {
    localStorage.setItem(ONBOARDING_SEEN_KEY, '1');
    setShowWizard(false);
    fetchPersonas().then((list) => {
      setPersonas(list);
      setSelectedPersona(name);
      // 用新画像开一个新会话
      const newSession = createSession(name);
      setSessions((prev) => {
        const updated = [newSession, ...prev];
        saveSessions(updated);
        return updated;
      });
      setActiveSessionId(newSession.id);
      setCurrentPage('profile');
    }).catch(() => {});
  }, []);

  // 画像删除完成：刷新列表 + 若删的是当前选中的则清空选择
  const handleProfileDeleted = useCallback((name: string) => {
    fetchPersonas().then((list) => {
      setPersonas(list);
      setSelectedPersona((cur) => (cur === name ? '' : cur));
    }).catch(() => {});
  }, []);

  // 流式生命周期在 App，页面切换随意——ChatPage 可自由卸载/重挂，回来从 props 读流式态即可。
  const renderPage = () => {
    if ((!canOperate && ['chat', 'publish', 'breakdown', 'skills'].includes(currentPage))
        || (!isPlatformAdmin && currentPage === 'skills')) {
      return <div className="dash-empty">{canOperate ? '此工具需要平台管理员权限。' : '当前 IP 的查看者可浏览内容，运营操作需要 operator 或 owner 角色。'}<button className="btn" onClick={() => setCurrentPage('dashboard')}>返回工作台</button></div>;
    }
    switch (currentPage) {
      case 'dashboard':
        return (
          <DashboardPage
            persona={selectedPersona}
            gatewayStatus={gatewayStatus}
            apiDirect={apiDirect}
            agentRuntime={agentRuntime}
            onNavigate={setCurrentPage}
            onUseTopic={handleUseTopic}
            onUseSavedIdea={handleUseSavedIdea}
          />
        );
      case 'chat':
        return activeSession ? (
          <ChatPage
            apiDirect={apiDirect}
            key={activeSession.id}
            session={activeSession}
            stream={streams[activeSession.id]}
            queue={activeSession.queued || []}
            queuePaused={activeSession.queuePaused}
            onRemoveQueued={id => removeQueued(activeSession.id, id)}
            onResumeQueue={() => resumeQueue(activeSession.id)}
            historyNotice={historyNotice}
            onSend={(displayText, attachments) => handleSendMessage(activeSession.id, displayText, attachments)}
            onStop={() => handleStopStream(activeSession.id)}
            onResend={(userIndex, displayText, attachments, legacyAgentText) => handleResend(
              activeSession.id, userIndex, displayText, attachments, legacyAgentText,
            )}
            onQuestionAnswered={(qid) => {
              answeredRef.current.add(qid);
              closeQuestion(activeSession.id, { id: qid, status: 'answered' });
              void questionStatus([qid]).then(states => {
                if (states[qid]) closeQuestion(activeSession.id, { id: qid, ...states[qid] });
              });
            }}
            onQuestionClosed={(id, status) => closeQuestion(activeSession.id, { id, status })}
          />
        ) : null;
      case 'trends':
        return <TrendsPage onUseSavedIdea={handleUseSavedIdea} />;
      case 'ideas':
        return <IdeasPage onUseTopic={handleUseSavedIdea} initialIdeaId={businessEntry?.kind === 'idea' ? businessEntry.id : undefined} />;
      case 'calendar':
        return <CalendarPage />;
      case 'publish':
        return <PublishPage persona={selectedPersona} />;
      case 'breakdown':
        return <BreakdownPage persona={selectedPersona} />;
      case 'skills':
        return <SkillPage persona={selectedPersona} />;
      case 'workshop':
        return <Suspense fallback={<div className="dash-empty">正在打开视频工坊…</div>}><VideoWorkshopPage key={workshopEntry}
          initialProjectId={businessEntry?.kind === 'workshop' ? businessEntry.id : undefined} /></Suspense>;
      case 'interactions':
        return <Suspense fallback={<div className="dash-empty">正在打开互动中心…</div>}><InteractionCenterPage key={interactionEntry} /></Suspense>;
      case 'outputs':
        return <OutputsPage jumpPath={outputsJump} onJumpHandled={clearOutputsJump} />;
      case 'accounts':
        return <AccountsPage />;
      case 'profile':
        return <ProfilePage persona={selectedPersona} onUsePersona={handlePersonaChange} onNewProfile={() => setShowWizard(true)} onDeleted={handleProfileDeleted} />;
      default:
        return null;
    }
  };

  const handlePersonaChange = useCallback((persona: string) => {
    setSelectedPersona(persona);
    setCurrentPage('chat');
    const cur = sessionsRef.current.find((s) => s.id === activeSessionId);
    if (cur && cur.messages.length === 0) {
      setSessions((prev) => {
        const updated = prev.map((s) =>
          s.id === activeSessionId ? { ...s, persona: persona || undefined, updated: nextSessionRevision(s) } : s);
        saveSessions(updated);
        return updated;
      });
      return;
    }
    const newSession = createSession(persona || undefined);
    setSessions((prev) => {
      const updated = [newSession, ...prev];
      saveSessions(updated);
      return updated;
    });
    setActiveSessionId(newSession.id);
  }, [activeSessionId]);

  return (
    <div className="app-layout">
      <Sidebar
        currentPage={currentPage}
        onPageChange={page => {
          businessNavigation.current += 1; setBusinessEntry(null); setBusinessNotice('');
          if (page === 'workshop') setWorkshopEntry(entry => entry + 1);
          if (page === 'interactions') setInteractionEntry(entry => entry + 1);
          setCurrentPage(page);
        }}
        personas={personas}
        selectedPersona={selectedPersona}
        onPersonaChange={handlePersonaChange}
        onNewProfile={() => setShowWizard(true)}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSessionSelect={id => { businessNavigation.current += 1; setBusinessNotice(''); handleSessionSelect(id); }}
        onSessionDelete={handleSessionDelete}
        onSessionRename={handleSessionRename}
        onSessionArchive={handleSessionArchive}
        onSessionPin={handleSessionPin}
        sessionStates={Object.fromEntries(sessions.map(session => {
          const stream = streams[session.id];
          const state = stream ? stream.lifecycle === 'waiting_input' ? 'waiting' : stream.lifecycle === 'stopping' ? 'stopping' : 'running'
            : unreadSessions[session.id] ? 'completed-unread' : session.messages.at(-1)?.status === 'failed' ? 'failed' : '';
          return [session.id, state];
        }))}
        onNewChat={() => { businessNavigation.current += 1; setBusinessNotice(''); handleNewChat(); }}
        gatewayStatus={gatewayStatus}
        apiDirect={apiDirect}
        agentRuntime={agentRuntime}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <main className="main-content">
        <ProjectBreadcrumb />
        {businessNotice && <p className="agent-event__note" role="status">{businessNotice}</p>}
        {(['trends', 'ideas', 'breakdown'] as Page[]).includes(currentPage) && (
          <SubNav current={currentPage} onNavigate={page => { businessNavigation.current += 1;
            setBusinessEntry(null); setBusinessNotice(''); setCurrentPage(page); }} />
        )}
        <div className="page-host">
          {renderPage()}
        </div>
      </main>

      {/* 首次使用：推荐配置画像 */}
      {showRecommend && (
        <div className="overlay">
          <div className="modal" style={{ width: 420, maxWidth: '100%', textAlign: 'center' }}>
            <div style={{ fontSize: 40 }}>👋</div>
            <h2 style={{ margin: '12px 0 8px', fontSize: 20 }}>欢迎使用 Easel</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.6 }}>
              配置你的账号画像，生成的内容会更贴合你的风格、受众和平台调性。<br />
              大约 2 分钟，也可以随时在侧栏「+ 新建画像」补配。
            </p>
            <div style={{ display: 'flex', gap: 10, marginTop: 20, justifyContent: 'center' }}>
              <button className="btn" onClick={dismissRecommend}>先用通用模式</button>
              <button className="btn btn-primary" onClick={openWizard}>开始配置</button>
            </div>
          </div>
        </div>
      )}

      {/* 画像配置向导 */}
      {showWizard && (
        <OnboardingWizard onClose={() => setShowWizard(false)} onCreated={handleProfileCreated} />
      )}

      {/* 设置（统一入口：模型配置 · 环境安装 · 更多设置） */}
      {settingsOpen && isPlatformAdmin && (
        <SettingsPanel
          banner={needModelSetup
            ? '还没有配置模型 —— 填一个 API Key 才能开始对话。保存后会自动同步配置并重启网关。'
            : ''}
          onClose={() => {
            setSettingsOpen(false);
            setNeedModelSetup(false);
            void fetchStatus().then((data) => {
              setApiDirect(data.transport === 'api');
              setAgentRuntime(data.runtime || 'openclaw');
              setGatewayStatus(data.gateway ? 'connected' : 'disconnected');
            }).catch(() => setGatewayStatus('disconnected'));
          }}
        />
      )}
    </div>
  );
}
