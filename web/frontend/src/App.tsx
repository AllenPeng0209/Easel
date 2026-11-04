import { projectStorageKey, projectUrl, projectFetch, ACTIVE_PROJECT_ID, ACTIVE_USER_ID } from './lib/projectScope';
import { ProjectBreadcrumb, useIpPermissions } from './components/ProjectSpace';
import { lazy, Suspense, useState, useEffect, useCallback, useRef } from 'react';
import Sidebar from './components/Sidebar';
import type { Page } from './components/Sidebar';
import ChatPage from './components/ChatPage';
import ArchiveRunningChatDialog from './components/ArchiveRunningChatDialog';
import DeleteChatDialog from './components/DeleteChatDialog';
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
import { useChatSuperagent } from './components/SuperagentPage';
import { fetchStatus, fetchPersonas, streamChat, fetchLastTurn, stopChat, steerChat, fetchChatSessions, syncChatSessions, deleteChatSession, forkChatSession, forkPrefixHash, fetchEarlierChatTurns, fetchCompleteChatSession, fetchSessionModels, type SessionModelCapability } from './lib/api';
import type { PersonaItem, UploadedFile, ChatQuestion, ChatQuestionClosed, ChatTurnOutcome } from './lib/api';
import { reduceAgentEvents } from './lib/agentEvents';
import { observeAgentTimeline, recoverAgentTimeline, type AgentTimelineObservation } from './lib/agentTimeline';
import { fetchBusinessObject, parseBusinessHash } from './lib/businessObjectApi';
import type { BusinessTarget } from './lib/businessObjectApi';
import type { WorkshopReference } from './lib/workshop';
import type { AgentEvent } from './lib/agentEvents';
import { validateChatCommand, type ChatCommand } from './lib/chatCommands';
import { trimChatReferenceText, toWireSessionReferences, type ChatReference, type ChatSessionReference } from './lib/chatReferences';
import { mergeWindowedCatalog, nextSessionRevision } from './lib/sessionSync';
import { sessionAttentionState } from './lib/sessionNavigation';
import { confirmChatStop, enqueueMessage, nextQueuedMessage } from './lib/chatControl';
import { chatDraftOwner, loadChatDraft, saveChatDraft, clearChatDraft } from './lib/chatDrafts';
import { applyPromptAdmission, forgetPromptAttempt, loadPromptAttempt, sameComposerDraft,
  samePromptAttempt, savePromptAttempt, type PromptAttempt } from './lib/promptAdmission';
import { questionStatus } from './lib/api';
import {
  loadSessions,
  saveSessions,
  createSession,
  updateSessionTitle,
  loadActiveId,
  saveActiveId,
} from './lib/store';
import type { ChatSession, ChatMessage, QueuedMessage, StreamState, ReasoningSummary, AnswerCompletion, ChatFailure, ChatPlanRef } from './lib/store';

const TAB_SESSION_KEY = projectStorageKey('easel_tab_session');
const ONBOARDING_SEEN_KEY = projectStorageKey('easel_onboarding_seen');
const VideoWorkshopPage = lazy(() => import('./components/VideoWorkshopPage'));
const WorkshopJobsDrawer = lazy(() => import('./components/WorkshopJobsDrawer'));
const InteractionCenterPage = lazy(() => import('./components/InteractionCenterPage'));

interface ArchiveIntent {
  id: string;
  title: string;
  generation: number;
  turnId?: string;
  remote: boolean;
}

interface LiveTurnAccumulator {
  content: string; reasoningSummary?: ReasoningSummary; answerCompletion?: AnswerCompletion; steps: string[]; questions: ChatQuestion[]; events: AgentEvent[]; timeline: AgentTimelineObservation[];
  turnId?: string;
  queuedId?: string;
  queueAdmitted?: boolean;
  queueRetry?: boolean;
  prompt?: PromptAttempt;
  promptAdmitted?: boolean;
  admissionComposerId?: string;
  admissionPromise?: Promise<boolean>;
  resolveAdmission?: (accepted: boolean) => void;
  steering?: boolean;
  steerAttempt?: { turnId: string; requestId: string; message: string; accepted?: boolean };
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
  const [workshopJobsOpen, setWorkshopJobsOpen] = useState(false);
  const [archiveRequest, setArchiveRequest] = useState<ArchiveIntent | null>(null);
  const archiveGeneration = useRef(0);
  const forkRequests = useRef(new Map<string, string>());
  const [deleteRequest, setDeleteRequest] = useState<{ id: string; title: string } | null>(null);
  const [interactionEntry, setInteractionEntry] = useState(0);
  const [businessEntry, setBusinessEntry] = useState<BusinessTarget | null>(null);
  const [businessNotice, setBusinessNotice] = useState('');
  const businessNavigation = useRef(0);
  const [, setPersonas] = useState<PersonaItem[]>([]);
  const [selectedPersona, setSelectedPersona] = useState('');
  const [sessions, setSessions] = useState<ChatSession[]>(() => loadSessions());
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [workshopPanel, setWorkshopPanel] = useState<{ id: string; sessionId: string; artifactId?: string } | null>(null);
  const chatOrigin = useRef<string | null>(null);
  chatOrigin.current = currentPage === 'chat' ? activeSessionId : null;
  const [gatewayStatus, setGatewayStatus] = useState('connecting');
  const [apiDirect, setApiDirect] = useState(false);
  const [agentRuntime, setAgentRuntime] = useState('openclaw');
  const [showRecommend, setShowRecommend] = useState(false);
  const [showWizard, setShowWizard] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyReady, setHistoryReady] = useState(false);
  const [navigationReady, setNavigationReady] = useState(false);
  const [historyNotice, setHistoryNotice] = useState('');
  const [admissionFeedback, setAdmissionFeedback] = useState<Record<string, string>>({});
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
      const origin = chatOrigin.current;
      try {
        const record = await fetchBusinessObject(target.kind, target.id, controller.signal);
        if (!mounted || generation !== businessNavigation.current || window.location.hash !== hash) return;
        const expected = target.kind === 'idea' ? 'ideas' : 'workshop';
        if (!record.actions.some(action => action.kind === 'open' && action.target === expected && action.entityId === target.id)) {
          throw new Error('No current navigation action');
        }
        if (target.kind === 'workshop' && origin && origin === chatOrigin.current) {
          setWorkshopPanel({ id: target.id, sessionId: origin, artifactId: target.artifactId });
        } else {
          setBusinessEntry(target);
          if (target.kind === 'workshop') setWorkshopEntry(value => value + 1);
          setCurrentPage(expected);
        }
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
  const recordSuperagentPrompt = useCallback((sessionId: string, taskId: string, text: string) => {
    const clientTurnId = `superagent-${taskId}`;
    setSessions(previous => {
      const session = previous.find(item => item.id === sessionId);
      if (!session || session.messages.some(message => message.clientTurnId === clientTurnId)) return previous;
      const message: ChatMessage = { role: 'user', clientTurnId, content: text, created: Date.now() };
      const updated = { ...session, messages: [...session.messages, message], updated: nextSessionRevision(session) };
      updateSessionTitle(updated);
      const next = previous.map(item => item.id === sessionId ? updated : item);
      saveSessions(next);
      return next;
    });
  }, []);
  const chatSuperagent = useChatSuperagent(
    activeSession?.id, currentPage === 'chat' && canOperate, recordSuperagentPrompt,
  );

  // 最新 sessions 的 ref，供回调里读取而不必进依赖数组（避免闭包过期/频繁重建）
  const sessionsRef = useRef(sessions);
  useEffect(() => { sessionsRef.current = sessions; }, [sessions]);

  const [modelCatalog, setModelCatalog] = useState<{ sessionId: string; capability?: SessionModelCapability; loading: boolean }>();
  const [modelReload, setModelReload] = useState(0);
  useEffect(() => {
    if (!canOperate || currentPage !== 'chat' || !activeSessionId) return;
    const controller = new AbortController();
    const sessionId = activeSessionId;
    setModelCatalog(previous => ({
      sessionId, capability: previous?.sessionId === sessionId ? previous.capability : undefined, loading: true,
    }));
    void fetchSessionModels(sessionId, controller.signal).then(capability => {
      if (!controller.signal.aborted) setModelCatalog({ sessionId, capability, loading: false });
    }).catch(() => {
      if (!controller.signal.aborted) setModelCatalog(previous => ({ sessionId,
        capability: previous?.sessionId === sessionId ? previous.capability : undefined, loading: false,
      }));
    });
    return () => controller.abort();
  }, [canOperate, currentPage, activeSessionId, activeSession?.pendingTurnId, agentRuntime, modelReload]);

  const selectNextModel = (sessionId: string, model: string | null) => {
    if (!canOperate || modelCatalog?.sessionId !== sessionId
        || (model !== null && (!modelCatalog.capability?.canSelect
            || !modelCatalog.capability.options.some(item => item.id === model)))) return;
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId && session.nextModel !== (model ?? undefined)
        ? { ...session, nextModel: model ?? undefined, updated: nextSessionRevision(session) } : session);
      saveSessions(next);
      return next;
    });
  };

  // 当前活跃会话 id 的 ref：供跨标签「谁在用会话 X」查询时即时应答（见挂载 effect）
  const activeIdRef = useRef<string | null>(activeSessionId);
  useEffect(() => { activeIdRef.current = activeSessionId; }, [activeSessionId]);

  useEffect(() => {
    if (!historyReady || !canOperate || currentPage !== 'chat' || !activeSessionId || !activeSession?.unreadCompletion) return;
    const markSeen = () => {
      if (chatOrigin.current !== activeSessionId || document.visibilityState === 'hidden') return;
      setSessions(previous => {
        const next = previous.map(session => session.id === activeSessionId && session.unreadCompletion
          ? { ...session, unreadCompletion: false, updated: nextSessionRevision(session) } : session);
        if (next.every((item, index) => item === previous[index])) return previous;
        saveSessions(next);
        return next;
      });
    };
    markSeen();
    document.addEventListener('visibilitychange', markSeen);
    return () => document.removeEventListener('visibilitychange', markSeen);
  }, [activeSessionId, activeSession?.unreadCompletion, canOperate, currentPage, historyReady]);

  useEffect(() => {
    if (!canOperate || !navigationReady) return;
    let cancelled = false;
    const controller = new AbortController();
    void fetchChatSessions(controller.signal, true).then(result => {
      if (cancelled) return;
      const remote = Array.isArray(result.sessions) ? result.sessions : [];
      setSessions(previous => {
        const merged = mergeWindowedCatalog(previous, remote, result.deletedIds);
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
    return () => { cancelled = true; controller.abort(); };
  }, [canOperate, navigationReady]);

  const catalogBusy = useRef(false);
  const catalogMounted = useRef(false);
  useEffect(() => {
    catalogMounted.current = true;
    return () => { catalogMounted.current = false; };
  }, []);

  useEffect(() => {
    if (!historyReady || !canOperate) return;
    let scheduled: ReturnType<typeof setTimeout>;
    let current = true;
    const sync = () => {
      if (catalogBusy.current) {
        scheduled = setTimeout(sync, 150);
        return;
      }
      catalogBusy.current = true;
      const submitted = sessionsRef.current;
      void syncChatSessions(submitted).then(result => {
        if (!catalogMounted.current) return;
        setHistoryNotice('');
        setSessions(previous => {
          const merged = mergeWindowedCatalog(previous, result.sessions || [], result.deletedIds, submitted);
          if (merged.length === previous.length && merged.every((item, index) => item === previous[index])) return previous;
          saveSessions(merged);
          return merged;
        });
      }).catch(error => {
        if (!catalogMounted.current) return;
        setHistoryNotice(error?.status === 409
          ? '服务器历史已在其他页面更新，本地改动已保留。'
          : '历史尚未同步到服务器，请保留此浏览器；稍后会重新尝试。');
        if (current && error?.status !== 409) scheduled = setTimeout(() => setHistoryRetry(value => value + 1), 5000);
      }).finally(() => { catalogBusy.current = false; });
    };
    scheduled = setTimeout(sync, 500);
    return () => { current = false; clearTimeout(scheduled); };
  }, [sessions, historyReady, canOperate, historyRetry]);

  const handleLoadEarlier = useCallback(async (sessionId: string, before: string) => {
    const source = sessionsRef.current.find(session => session.id === sessionId);
    const current = source?.historyWindow;
    if (!canOperate || !current || current.before !== before) return;
    if (catalogBusy.current) throw new Error('正在同步对话，请稍后重试加载。');
    catalogBusy.current = true;
    try {
      const page = await fetchEarlierChatTurns(sessionId, before);
      const next = page.historyWindow;
      if (!next || !Array.isArray(page.messages) || !Number.isSafeInteger(next.start) || next.start < 0
          || !page.messages.length || next.start + page.messages.length !== current.start
          || !next.base || (next.start > 0 ? !next.before || next.before === before : next.before !== null)) {
        throw new Error('历史页回执不完整，请重试。');
      }
      if (!catalogMounted.current) return;
      setSessions(previous => {
        const updated = previous.map(session => session.id === sessionId && session.historyWindow?.before === before
          ? { ...session, historyWindow: next, messages: [...page.messages, ...session.messages] } : session);
        saveSessions(updated);
        return updated;
      });
    } finally {
      catalogBusy.current = false;
    }
  }, [canOperate]);

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
  const queueEdits = useRef<Record<string, string>>({});
  const [queueEditRevision, setQueueEditRevision] = useState(0);
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
    const unreadCompletion = msg.status === 'completed'
      && (chatOrigin.current !== sessionId || document.visibilityState === 'hidden');
    setSessions((prev) => {
      const next = prev.map((s) =>
        s.id === sessionId && (!msg.clientTurnId || !s.messages.some(message =>
          message.role === 'assistant' && message.clientTurnId === msg.clientTurnId))
          ? { ...s, updated: nextSessionRevision(s), messages: [...s.messages, { ...msg, created: Date.now() }], sessionKey: sessionKey || s.sessionKey, pendingTurnId: undefined, unreadCompletion }
          : s);
      saveSessions(next);
      return next;
    });
  }, []);

  const clearStream = useCallback((sessionId: string) => {
    const pending = finalizers.current.get(sessionId);
    if (pending?.timer) clearTimeout(pending.timer);
    finalizers.current.delete(sessionId);
    // 打字机收尾：剩余队列立刻吐出，避免结束瞬间内容被截断
    flushTyping(sessionId);
    streamAcc.current[sessionId]?.resolveAdmission?.(false);
    delete streamCtl.current[sessionId];
    delete streamAcc.current[sessionId];
    setStreams((prev) => {
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });
  }, []);

  const confirmPromptAdmission = useCallback((sessionId: string, owner: LiveTurnAccumulator) => {
    const attempt = owner.prompt;
    if (!attempt || owner.promptAdmitted || streamAcc.current[sessionId] !== owner) return;
    owner.promptAdmitted = true;
    const stored = loadPromptAttempt(sessionId);
    const ownsAttempt = stored?.turnId === attempt.turnId;
    const draft = loadChatDraft(sessionId);
    const needsReview = ownsAttempt && sameComposerDraft(attempt, draft.text,
      draft.attachments.flatMap(item => item.reference ? [item.reference] : []), draft.references, draft.sessionReferences, draft.command)
      && (!owner.resolveAdmission || chatDraftOwner(sessionId) !== owner.admissionComposerId);
    if (ownsAttempt) savePromptAttempt(sessionId, { ...attempt, state: 'accepted' });
    setSessions(previous => {
      const next = previous.map(session => {
        if (session.id !== sessionId || session.pendingTurnId !== attempt.turnId) return session;
        const updated = applyPromptAdmission(session, attempt);
        if (updated === session) return session;
        updated.updated = nextSessionRevision(session);
        updateSessionTitle(updated);
        return updated;
      });
      if (saveSessions(next) && !needsReview && next.some(session => session.id === sessionId
          && session.pendingTurnId === attempt.turnId)) forgetPromptAttempt(sessionId, attempt.turnId);
      return next;
    });
    setAdmissionFeedback(previous => ({ ...previous, [sessionId]: '' }));
    owner.resolveAdmission?.(true);
    owner.resolveAdmission = undefined;
  }, []);

  const failPromptAdmission = useCallback((sessionId: string, owner: LiveTurnAccumulator, error?: Error): boolean => {
    const attempt = owner.prompt;
    if (!attempt || owner.promptAdmitted || streamAcc.current[sessionId] !== owner) return false;
    const status = (error as { status?: number } | undefined)?.status;
    const rejected = attempt.state === 'sending' && [400, 401, 403, 404, 422].includes(status ?? 0);
    if (rejected) {
      forgetPromptAttempt(sessionId, attempt.turnId);
    } else if (loadPromptAttempt(sessionId)?.turnId === attempt.turnId) {
      savePromptAttempt(sessionId, { ...attempt, state: 'unconfirmed' });
    }
    setAdmissionFeedback(previous => ({ ...previous, [sessionId]: rejected
      ? error?.message || '服务端未接收这条消息，草稿已保留。' : '' }));
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId && session.pendingTurnId === attempt.turnId
        ? { ...session, pendingTurnId: undefined, updated: nextSessionRevision(session) } : session);
      saveSessions(next);
      return next;
    });
    owner.resolveAdmission?.(false);
    owner.resolveAdmission = undefined;
    clearStream(sessionId);
    try { sessionStorage.removeItem(projectStorageKey(`easel_pending_turn:${sessionId}`)); } catch { /* Optional browser storage. */ }
    return true;
  }, [clearStream]);

  const confirmQueuedMessage = useCallback((sessionId: string, owner: LiveTurnAccumulator) => {
    if (!owner.queuedId || owner.queueAdmitted || streamAcc.current[sessionId] !== owner) return;
    owner.queueAdmitted = true;
    setSessions(previous => {
      const next = previous.map(session => {
        if (session.id !== sessionId || session.pendingTurnId !== owner.turnId) return session;
        const item = session.queued?.find(row => row.id === owner.queuedId);
        if (!item) return session;
        const trimmed = trimChatReferenceText(item.text, item.references, item.sessionReferences);
        const message: ChatMessage = { role: 'user', clientTurnId: owner.turnId, content: trimmed.text, attachments: item.attachments,
          references: trimmed.references, sessionReferences: toWireSessionReferences(trimmed.sessionReferences), command: item.command, created: Date.now() };
        const updated = { ...session, updated: nextSessionRevision(session),
          queued: session.queued?.filter(row => row.id !== item.id), messages: [...session.messages, message] };
        updateSessionTitle(updated);
        return updated;
      });
      saveSessions(next);
      return next;
    });
  }, []);

  const failQueuedAdmission = useCallback((sessionId: string, owner: LiveTurnAccumulator, error?: Error): boolean => {
    if (!owner.queuedId || owner.queueAdmitted || streamAcc.current[sessionId] !== owner) return false;
    const status = (error as { status?: number } | undefined)?.status;
    const delivery: QueuedMessage['delivery'] = !owner.queueRetry && (status === 400 || status === 422)
      ? 'rejected' : 'unconfirmed';
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId && session.pendingTurnId === owner.turnId
        ? { ...session, pendingTurnId: undefined, queuePaused: true, updated: nextSessionRevision(session),
          queued: session.queued?.map(row => row.id === owner.queuedId ? { ...row, delivery } : row) }
        : session);
      saveSessions(next);
      return next;
    });
    clearStream(sessionId);
    try { sessionStorage.removeItem(projectStorageKey(`easel_pending_turn:${sessionId}`)); } catch { /* Storage is optional. */ }
    return true;
  }, [clearStream]);

  const finalizeStream = useCallback((sessionId: string, owner: typeof streamAcc.current[string],
    sessionKey?: string, error?: Error, outcome?: ChatTurnOutcome) => {
    if (streamAcc.current[sessionId] !== owner || finalizers.current.has(sessionId)) return;
    if (owner.queuedId) {
      if (error && failQueuedAdmission(sessionId, owner, error)) return;
      if (!error) confirmQueuedMessage(sessionId, owner);
    }
    const pending: { owner: object; finish: () => void; timer?: ReturnType<typeof setTimeout> } = {
      owner,
      finish: () => {
        if (finalizers.current.get(sessionId) !== pending || streamAcc.current[sessionId] !== owner) return;
        if (owner.stopRequested && !owner.stopConfirmed) return;
        finalizers.current.delete(sessionId);
        if (pending.timer) clearTimeout(pending.timer);
        flushTyping(sessionId);
        const reportedError = error as (Error & { failure?: ChatFailure; unconfirmed?: boolean }) | undefined;
        const failure = !owner.stopConfirmed && (error || outcome === 'failed')
          ? reportedError?.failure
            ?? { code: 'unknown', retryable: false, phase: 'execution' } as const
          : undefined;
        appendAssistant(sessionId, {
          role: 'assistant', clientTurnId: owner.turnId, content: owner.content + (owner.stopConfirmed ? '\n\n_（已停止）_' : ''),
          failure,
          reasoningSummary: owner.reasoningSummary, answerCompletion: owner.answerCompletion,
          activity: owner.steps.join('\n') || undefined,
          questions: owner.questions.length ? [...owner.questions] : undefined,
          events: owner.events.length ? [...owner.events] : undefined,
          timeline: owner.timeline?.length ? owner.timeline : undefined,
          status: owner.stopConfirmed ? 'stopped' : reportedError?.unconfirmed ? 'unconfirmed'
            : error ? 'failed' : outcome ?? 'completed',
        }, sessionKey);
        if (error) setSessions(previous => previous.map(session => session.id === sessionId
          ? { ...session, queuePaused: true, updated: nextSessionRevision(session) } : session));
        clearStream(sessionId);
        try { sessionStorage.removeItem(projectStorageKey(`easel_pending_turn:${sessionId}`)); } catch {}
        try { sessionStorage.removeItem(projectStorageKey(`easel_steer_attempt:${sessionId}`)); } catch {}
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
  }, [appendAssistant, clearStream, confirmQueuedMessage, failQueuedAdmission]);

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
    if (!closed.status || ['pending', 'unknown', 'not_found'].includes(closed.status)) return;
    const merge = (question: ChatQuestion): ChatQuestion => {
      if (question.id !== closed.id) return question;
      if (question.status && question.status !== 'pending' && question.status !== closed.status
          && !(question.status === 'answered' && closed.status === 'resolved')) return question;
      const sameTerminal = question.status === closed.status;
      return { ...question, ...closed,
        answers: sameTerminal ? question.answers ?? closed.answers : closed.answers ?? question.answers,
        resolvedBy: sameTerminal ? question.resolvedBy ?? closed.resolvedBy : closed.resolvedBy ?? question.resolvedBy };
    };
    const owner = streamAcc.current[sessionId];
    if (owner && owner.questions.some(question => question.id === closed.id)) {
      owner.questions = owner.questions.map(merge);
      setStreams(previous => previous[sessionId] ? { ...previous,
        [sessionId]: { ...previous[sessionId], questions: [...owner.questions],
          lifecycle: previous[sessionId].lifecycle === 'stopping' || previous[sessionId].lifecycle === 'stop_failed'
            ? previous[sessionId].lifecycle : owner.questions.some(question => !question.status || question.status === 'pending') ? 'waiting_input' : 'running' } } : previous);
    }
    setSessions(previous => {
      let changed = false;
      const updated = previous.map(session => {
        if (session.id !== sessionId) return session;
        let changedMessages = false;
        const messages = session.messages.map(message => {
          const questions = message.questions?.map(merge);
          if (!questions?.some((question, index) => question !== message.questions?.[index])) return message;
          changedMessages = true;
          return { ...message, questions };
        });
        if (!changedMessages) return session;
        changed = true;
        return { ...session, messages, updated: nextSessionRevision(session) };
      });
      if (changed) saveSessions(updated);
      return changed ? updated : previous;
    });
  }, []);

  const receiveQuestion = useCallback((sessionId: string, owner: typeof streamAcc.current[string], q: ChatQuestion) => {
    if (streamAcc.current[sessionId] !== owner || answeredRef.current.has(q.id) || owner.questions.some(question => question.id === q.id)) return;
    owner.questions.push({ ...q, status: q.status || 'pending' });
    setStreams(previous => previous[sessionId] ? { ...previous,
      [sessionId]: { ...previous[sessionId], questions: [...owner.questions],
        lifecycle: previous[sessionId].lifecycle === 'stopping' || previous[sessionId].lifecycle === 'stop_failed'
          ? previous[sessionId].lifecycle : 'waiting_input' } } : previous);
    void questionStatus([q.id], sessionId).then(states => {
      if (streamAcc.current[sessionId] !== owner) return;
      const status = states[q.id]?.status;
      if (status && status !== 'pending' && status !== 'unknown') closeQuestion(sessionId, { id: q.id, ...states[q.id] });
    });
  }, [closeQuestion]);

  const receiveAgentEvent = useCallback((sessionId: string, owner: typeof streamAcc.current[string], event: AgentEvent) => {
    if (streamAcc.current[sessionId] !== owner) return;
    if (event.kind === 'steer_capability') {
      if (owner.turnId && event.turnId === owner.turnId && (event.status === 'ready' || event.status === 'closed')) {
        setStreams(previous => previous[sessionId] ? { ...previous,
          [sessionId]: { ...previous[sessionId], steerTurnId: event.status === 'ready' ? owner.turnId : undefined },
        } : previous);
      }
      return;
    }
    owner.timeline = observeAgentTimeline(owner.timeline, event);
    const pendingDecision = event.kind === 'decision' && event.status === 'pending';
    if (!pendingDecision) owner.events = reduceAgentEvents(owner.events, event);
    setStreams(previous => previous[sessionId] ? { ...previous,
      [sessionId]: { ...previous[sessionId], events: [...owner.events], timeline: owner.timeline, stillWorking: undefined } } : previous);
    if (event.kind === 'decision' && !pendingDecision && event.questionId) closeQuestion(sessionId, {
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
    workshopRef?: WorkshopReference,
    queuedId?: string,
    queueRetry?: boolean,
    prompt?: PromptAttempt,
    waitForAdmission = false,
    references = prompt?.message.references,
    sessionReferences = prompt?.message.sessionReferences,
    command = prompt?.message.command,
    modelOverride = prompt?.modelOverride,
  ) => {
    const turnId = requestedTurnId || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    try { sessionStorage.setItem(projectStorageKey(`easel_pending_turn:${sessionId}`), turnId); } catch { /* ignore */ }
    setSessions((prev) => {
      const next = prev.map(s => s.id === sessionId ? { ...s, pendingTurnId: turnId, updated: nextSessionRevision(s),
        ...(queuedId ? { queued: s.queued?.map(row => row.id === queuedId ? { ...row, delivery: 'sending' as const } : row) } : {}),
      } : s);
      saveSessions(next); return next;
    });
    const owner: LiveTurnAccumulator = { turnId, queuedId, queueRetry, prompt, content: '', steps: [], questions: [], events: [], timeline: [] };
    if (prompt && waitForAdmission) {
      owner.admissionComposerId = chatDraftOwner(sessionId);
      owner.admissionPromise = new Promise(resolve => { owner.resolveAdmission = resolve; });
    }
    streamAcc.current[sessionId] = owner;
    setStreams((prev) => ({ ...prev, [sessionId]: { content: '', activity: '', questions: [], events: [], lifecycle: 'recovering' } }));
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
      (sessionKey, outcome) => {
        // done 不能立即 flush——token 和 done 几乎同时到达（OpenClaw 攒批），
        // 立即 flush 会把整包瞬间冲出，打字机白做。等队列吐完再落盘。
        if (!failPromptAdmission(sessionId, owner)) finalizeStream(sessionId, owner, sessionKey, undefined, outcome);
      },
      (err) => {
        if (!failPromptAdmission(sessionId, owner, err)) finalizeStream(sessionId, owner, undefined, err);
      },
      (reasoningSummary) => {
        if (streamAcc.current[sessionId] !== owner) return;
        owner.reasoningSummary = reasoningSummary;
        setStreams(p => p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], reasoningSummary, stillWorking: undefined } } : p);
      },
      (status) => {
        const a = streamAcc.current[sessionId]; if (a !== owner) return;
        if (a.steps[a.steps.length - 1] !== status) a.steps.push(status);
        // 有真实活动状态 → 清掉防呆提示，让真实状态占据活动行
        setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], activity: status, stillWorking: undefined,
          lifecycle: p[sessionId].lifecycle === 'recovering' ? 'running' : p[sessionId].lifecycle } } : p));
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
      (closed) => { if (streamAcc.current[sessionId] === owner) closeQuestion(sessionId, closed); },
      (event) => receiveAgentEvent(sessionId, owner, event),
      topicRef,
      workshopRef,
      () => {
        confirmQueuedMessage(sessionId, owner);
        confirmPromptAdmission(sessionId, owner);
        if (modelOverride && streamAcc.current[sessionId] === owner) {
          setStreams(previous => previous[sessionId]
            ? { ...previous, [sessionId]: { ...previous[sessionId], requestedModel: modelOverride } } : previous);
        }
      },
      !!prompt || !!modelOverride,
      references,
      sessionReferences,
      command,
      (answerCompletion) => {
        if (streamAcc.current[sessionId] !== owner || owner.answerCompletion?.itemId === answerCompletion.itemId) return;
        owner.answerCompletion = answerCompletion;
        setStreams(p => p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], answerCompletion } } : p);
      },
      prompt?.message.planRef,
      modelOverride,
    );
    return owner;
  }, [finalizeStream, closeQuestion, receiveQuestion, receiveAgentEvent, confirmQueuedMessage, confirmPromptAdmission, failPromptAdmission]);

  // 刷新/重开页面后按 eventId=0 重放当前 job，再继续实时 tail；旧任务无事件日志时退回最终快照。
  const resumePendingTurn = useCallback((sessionId: string, observedTurnId?: string) => {
    if (streamCtl.current[sessionId] || streamAcc.current[sessionId]) return;  // 本标签正在跑，不插手
    const s = sessionsRef.current.find((x) => x.id === sessionId);
    if (!s) return;
    const last = s.messages.at(-1);
    let turnId = s.pendingTurnId;
    try { turnId = sessionStorage.getItem(projectStorageKey(`easel_pending_turn:${sessionId}`)) || turnId; } catch { /* use persisted id */ }
    if (observedTurnId) turnId = observedTurnId;
    if (turnId && s.messages.some(message => message.role === 'assistant' && message.clientTurnId === turnId)) return;
    const attempt = loadPromptAttempt(sessionId);
    if (!turnId && attempt?.state === 'unconfirmed') return;
    const prompt = attempt?.turnId === turnId ? attempt : undefined;
    const queuedId = s.queued?.find(row => turnId === `queue-${row.id}`)?.id;
    if (!observedTurnId && !queuedId && !prompt && last?.role !== 'user') return;
    if (observedTurnId) {
      try { sessionStorage.setItem(projectStorageKey(`easel_pending_turn:${sessionId}`), observedTurnId); } catch {}
      setSessions(previous => previous.map(session => session.id === sessionId
        ? { ...session, pendingTurnId: observedTurnId } : session));
    }
    const owner: LiveTurnAccumulator = { turnId, queuedId, queueRetry: !!queuedId, prompt, content: '', steps: [], questions: [], events: [], timeline: [] };
    streamAcc.current[sessionId] = owner;
    if (prompt?.state === 'accepted') confirmPromptAdmission(sessionId, owner);
    setStreams((p) => ({ ...p, [sessionId]: { content: '', activity: '⏳ 正在接回上一轮结果…', questions: [], lifecycle: 'recovering' } }));
    typingBuf.current[sessionId] = '';
    startTypingPump(sessionId);
    const appendRecovered = (result: Awaited<ReturnType<typeof fetchLastTurn>>) => {
      if (prompt && !owner.promptAdmitted) {
        if (prompt.state !== 'accepted') {
          failPromptAdmission(sessionId, owner);
          return;
        }
        confirmPromptAdmission(sessionId, owner);
      }
      confirmQueuedMessage(sessionId, owner);
      const status = result.outcome ?? (result.stop_reason === 'user_stopped' ? 'stopped'
        : result.error ? 'failed' : result.clean_end === true ? 'completed' : 'unconfirmed');
      appendAssistant(sessionId, { role: 'assistant', clientTurnId: result.turn_id ?? turnId, content: result.text || '（无输出）',
        events: result.events?.filter(event => event.kind !== 'decision' || event.status !== 'pending'),
        timeline: recoverAgentTimeline(result.events, owner.timeline),
        reasoningSummary: result.reasoning_summary ?? owner.reasoningSummary,
        answerCompletion: result.answer_completed ?? owner.answerCompletion,
        questions: result.questions, status, failure: status === 'failed' ? result.error : undefined });
    };
    if (!turnId) {
      void fetchLastTurn(sessionId).then((r) => {
        if (streamAcc.current[sessionId] !== owner) return;
        if (r.status === 'done') appendRecovered(r);
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
      (sessionKey, outcome) => {
        if (!failPromptAdmission(sessionId, owner)) finalizeStream(sessionId, owner, sessionKey, undefined, outcome);
      },
      (err) => {
        if (!failPromptAdmission(sessionId, owner, err)) finalizeStream(sessionId, owner, undefined, err);
      },
      (reasoningSummary) => {
        if (streamAcc.current[sessionId] !== owner) return;
        owner.reasoningSummary = reasoningSummary;
        setStreams(p => p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], reasoningSummary, stillWorking: undefined } } : p);
      },
      (status) => {
        const a = streamAcc.current[sessionId]; if (a !== owner) return;
        if (a.steps[a.steps.length - 1] !== status) a.steps.push(status);
        setStreams((p) => (p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], activity: status,
          lifecycle: p[sessionId].lifecycle === 'recovering' ? 'running' : p[sessionId].lifecycle } } : p));
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
            appendRecovered(r);
          } else {
            if (failPromptAdmission(sessionId, owner) || failQueuedAdmission(sessionId, owner)) return;
            appendAssistant(sessionId, {
              role: 'assistant', status: 'unconfirmed',
              content: '上一轮任务记录已失效，执行结果待核对。',
            });
          }
          clearStream(sessionId);
          try { sessionStorage.removeItem(projectStorageKey(`easel_pending_turn:${sessionId}`)); } catch { /* ignore */ }
        }).catch(() => {
          if (streamAcc.current[sessionId] !== owner) return;
          if (failPromptAdmission(sessionId, owner) || failQueuedAdmission(sessionId, owner)) return;
          appendAssistant(sessionId, {
            role: 'assistant', status: 'unconfirmed', content: '上一轮任务记录暂时不可用，执行结果待核对。',
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
      (closed) => { if (streamAcc.current[sessionId] === owner) closeQuestion(sessionId, closed); },
      (event) => receiveAgentEvent(sessionId, owner, event),
      undefined, undefined, () => {
        confirmQueuedMessage(sessionId, owner);
        confirmPromptAdmission(sessionId, owner);
      }, !!prompt,
      undefined, undefined, undefined,
      (answerCompletion) => {
        if (streamAcc.current[sessionId] !== owner || owner.answerCompletion?.itemId === answerCompletion.itemId) return;
        owner.answerCompletion = answerCompletion;
        setStreams(p => p[sessionId] ? { ...p, [sessionId]: { ...p[sessionId], answerCompletion } } : p);
      },
    );
  }, [appendAssistant, clearStream, finalizeStream, closeQuestion, receiveQuestion, receiveAgentEvent, confirmQueuedMessage,
    confirmPromptAdmission, failQueuedAdmission, failPromptAdmission]);

  // 活跃会话确定后（含挂载首刷）尝试恢复它悬空的一轮
  useEffect(() => {
    if (canOperate && activeSessionId) resumePendingTurn(activeSessionId);
  }, [activeSessionId, activeSession?.pendingTurnId, activeSession?.updated, canOperate, historyReady, resumePendingTurn]);

  useEffect(() => {
    if (!historyReady || !canOperate) return;
    for (const session of sessions) {
      if (!session.pendingTurnId || session.id === activeSessionId) continue;
      // Only this tab's own background turns are resumed automatically.
      let ownedTurn: string | null = null;
      try { ownedTurn = sessionStorage.getItem(projectStorageKey(`easel_pending_turn:${session.id}`)); } catch { /* Wait for explicit open. */ }
      if (ownedTurn === session.pendingTurnId) resumePendingTurn(session.id);
    }
  }, [activeSessionId, canOperate, historyReady, resumePendingTurn, sessions]);

  // 等待服务端接收，再追加用户消息。
  const sendUserAndStream = useCallback((
    sessionId: string,
    displayText: string,
    attachments: UploadedFile[] = [],
    legacyAgentText?: string,
    requestedTurnId?: string,
    topicRef?: ChatMessage['topicRef'],
    workshopRef?: WorkshopReference,
    composer = false,
    references?: ChatReference[],
    sessionReferences?: ChatSessionReference[],
    command?: ChatCommand,
    planRef?: ChatPlanRef,
  ) => {
    const trimmed = trimChatReferenceText(displayText, references, sessionReferences);
    const visible = trimmed.text;
    const claim = command ? validateChatCommand(displayText, command) : null;
    if (command && !claim) return false;
    const agentMessage = planRef ? displayText : legacyAgentText ? legacyAgentText.trim() : visible;
    if (!agentMessage && attachments.length === 0) return false;
    const cur = sessionsRef.current.find((s) => s.id === sessionId);
    const persona = cur ? cur.persona : selectedPersona || undefined;
    const queuedItem = requestedTurnId && cur?.queued?.find(row => requestedTurnId === `queue-${row.id}`);
    if (queuedItem) {
      if (streamCtl.current[sessionId] || streamAcc.current[sessionId]) return false;
      startStream(sessionId, agentMessage, persona, attachments, requestedTurnId, topicRef, workshopRef,
        queuedItem.id, queuedItem.delivery === 'unconfirmed', undefined, false, trimmed.references, toWireSessionReferences(trimmed.sessionReferences), claim ?? undefined, queuedItem.modelOverride);
      return true;
    }
    const previous = loadPromptAttempt(sessionId);
    let attempt: PromptAttempt = {
      turnId: requestedTurnId || `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      state: 'sending', wireText: agentMessage, persona, composer, modelOverride: cur?.nextModel,
      message: {
        role: 'user', content: planRef ? displayText : visible, created: Date.now(),
        ...(attachments.length ? { attachments: attachments.map(item => ({ ...item })) } : {}),
        ...(trimmed.references?.length ? { references: trimmed.references } : {}),
        ...(trimmed.sessionReferences?.length ? { sessionReferences: toWireSessionReferences(trimmed.sessionReferences) } : {}),
        ...(claim ? { command: claim } : {}),
        ...(planRef ? { planRef: { ...planRef } } : {}),
        ...(topicRef ? { topicRef: { ...topicRef } } : {}),
        ...(workshopRef ? { workshopRef: { ...workshopRef } } : {}),
        ...(legacyAgentText && legacyAgentText !== visible ? { agentContent: legacyAgentText } : {}),
      },
    };
    if (previous && previous.state !== 'accepted') {
      if (!samePromptAttempt(previous, attempt)) {
        setAdmissionFeedback(value => ({ ...value,
          [sessionId]: '上一条发送仍待核对。可先核对原请求，当前新草稿已保留。' }));
        return false;
      }
      const active = streamAcc.current[sessionId];
      if (active?.prompt?.turnId === previous.turnId) {
        if (!composer || !active.admissionPromise) return false;
        active.admissionComposerId = chatDraftOwner(sessionId);
        return active.admissionPromise;
      }
      attempt = { ...previous, state: 'unconfirmed' };
    } else if (cur?.pendingTurnId) {
      return false;
    }
    if (streamCtl.current[sessionId] || streamAcc.current[sessionId]) return false;
    savePromptAttempt(sessionId, attempt);
    setAdmissionFeedback(value => ({ ...value, [sessionId]: '' }));
    const waitForAdmission = composer || !!planRef;
    const owner = startStream(sessionId, attempt.wireText, attempt.persona, attempt.message.attachments,
      attempt.turnId, attempt.message.topicRef, attempt.message.workshopRef, undefined, undefined, attempt, waitForAdmission);
    return waitForAdmission ? owner.admissionPromise! : true;
  }, [selectedPersona, startStream]);

  const handleSendMessage = useCallback((sessionId: string, displayText: string, attachments?: UploadedFile[], references?: ChatReference[], sessionReferences?: ChatSessionReference[], command?: ChatCommand) => {
    const session = sessionsRef.current.find(item => item.id === sessionId);
    if (!session || (!displayText.trim() && !attachments?.length) || (command && !validateChatCommand(displayText, command))) return false;
    const prior = loadPromptAttempt(sessionId);
    if (prior && prior.state !== 'accepted') {
      return sendUserAndStream(sessionId, displayText, attachments, undefined, undefined, undefined, undefined, true, references, sessionReferences, command);
    }
    if (prior && sameComposerDraft(prior, displayText, attachments || [], references, sessionReferences, command)) {
      // A cold or remounted composer may still hold the already admitted draft.
      // An intentional send of that exact draft acknowledges the old echo.
      setSessions(previous => {
        const next = previous.map(item => {
          if (item.id !== sessionId) return item;
          const updated = applyPromptAdmission(item, prior);
          if (updated === item) return item;
          updated.updated = nextSessionRevision(item);
          updateSessionTitle(updated);
          return updated;
        });
        if (saveSessions(next)) forgetPromptAttempt(sessionId, prior.turnId);
        return next;
      });
      return true;
    }
    if (prior) forgetPromptAttempt(sessionId, prior.turnId);
    if (streamCtl.current[sessionId] || streamAcc.current[sessionId] || session.queued?.length) {
      const trimmed = trimChatReferenceText(displayText, references, sessionReferences);
      const queued = { id: crypto.randomUUID(), text: trimmed.text, attachments, references: trimmed.references,
        sessionReferences: toWireSessionReferences(trimmed.sessionReferences), command, modelOverride: session.nextModel, created: Date.now() };
      setSessions(previous => {
        const next = previous.map(item => item.id === sessionId ? enqueueMessage(item, queued) : item);
        saveSessions(next);
        return next;
      });
      return true;
    }
    return sendUserAndStream(sessionId, displayText, attachments, undefined, undefined, undefined, undefined, true, references, sessionReferences, command);
  }, [sendUserAndStream]);

  const reconcilePromptAttempt = useCallback((sessionId: string) => {
    const pending = loadPromptAttempt(sessionId);
    if (!pending || pending.state === 'accepted' || streamAcc.current[sessionId] || streamCtl.current[sessionId]) return;
    const retry = { ...pending, state: 'unconfirmed' as const };
    savePromptAttempt(sessionId, retry);
    setAdmissionFeedback(value => ({ ...value, [sessionId]: '' }));
    startStream(sessionId, pending.wireText, pending.persona, pending.message.attachments,
      pending.turnId, pending.message.topicRef, pending.message.workshopRef, undefined, undefined, retry);
  }, [startStream]);

  const handleSteer = useCallback(async (sessionId: string, text: string): Promise<boolean> => {
    const owner = streamAcc.current[sessionId];
    const turnId = owner?.turnId;
    const stream = streams[sessionId];
    if (!canOperate || !turnId || stream?.steerTurnId !== turnId || stream.lifecycle !== 'running'
        || owner.stopRequested || owner.questions.some(question => !question.status || question.status === 'pending')) {
      throw new Error('当前任务暂时无法接收指导，草稿已保留。');
    }
    const message = text.trim();
    if (!message || owner.steering) return false;
    if (Array.from(message).length > 8000) throw new Error('本轮指导最多 8000 个字符，草稿已保留。');
    const key = projectStorageKey(`easel_steer_attempt:${sessionId}`);
    if (!owner.steerAttempt) {
      try {
        const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
        if (saved?.turnId === turnId && typeof saved.requestId === 'string' && typeof saved.message === 'string') {
          owner.steerAttempt = saved;
        }
      } catch { /* Session storage is optional. */ }
    }
    if (owner.steerAttempt && owner.steerAttempt.message !== message) {
      if (!owner.steerAttempt.accepted) {
        throw new Error('上一条指导仍待核实。请恢复原文查询回执，或等本轮结束。');
      }
      owner.steerAttempt = undefined;
    }
    const attempt = owner.steerAttempt ?? { turnId, requestId: crypto.randomUUID(), message };
    owner.steerAttempt = attempt;
    owner.steering = true;
    try { sessionStorage.setItem(key, JSON.stringify(attempt)); } catch { /* Keep the in-memory receipt key. */ }
    const forgetAttempt = () => {
      owner.steerAttempt = undefined;
      if (streamAcc.current[sessionId] === owner) {
        try { sessionStorage.removeItem(key); } catch { /* No persisted receipt key. */ }
      }
    };
    try {
      const receipt = await steerChat(sessionId, turnId, attempt.requestId, message);
      if (receipt.status !== 'accepted') throw new Error('指导回执待核实，草稿已保留。再次点击只会查询同一条指导。');
      // A remounted composer may still contain this text. Keep the key so it
      // can acknowledge the same send rather than deliver a duplicate.
      attempt.accepted = true;
      if (streamAcc.current[sessionId] === owner) {
        try { sessionStorage.setItem(key, JSON.stringify(attempt)); } catch { /* Keep the in-memory receipt key. */ }
      }
      return true;
    } catch (error) {
      const status = (error as { status?: number } | null)?.status;
      if (status && status >= 400 && status < 500) {
        if (status === 409 || status === 422) forgetAttempt();
        throw error;
      }
      throw new Error('指导回执待核实，草稿已保留。再次点击只会查询同一条指导。');
    } finally {
      owner.steering = false;
    }
  }, [canOperate, streams]);

  // Queue entries survive navigation/reload. A stopped or failed queue needs an explicit resume.
  useEffect(() => {
    if (!historyReady || !canOperate) return;
    for (const session of sessions) {
      if (session.pendingTurnId && session.queued?.length) resumePendingTurn(session.id);
      if (loadPromptAttempt(session.id)?.state === 'unconfirmed') continue;
      const next = nextQueuedMessage(session, Boolean(streamAcc.current[session.id] || streamCtl.current[session.id] || session.pendingTurnId));
      if (!next || queueEdits.current[session.id]) continue;
      sendUserAndStream(session.id, next.text, next.attachments || [], undefined, `queue-${next.id}`, undefined, undefined, false, next.references, next.sessionReferences, next.command);
    }
  }, [sessions, streams, queueEditRevision, historyReady, canOperate, resumePendingTurn, sendUserAndStream]);

  const removeQueued = useCallback((sessionId: string, messageId: string) => {
    if (streamAcc.current[sessionId]?.queuedId === messageId) return;
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId
        ? { ...session, queued: session.queued?.filter(message => message.id !== messageId ||
          message.delivery === 'sending' || message.delivery === 'unconfirmed'), updated: nextSessionRevision(session) } : session);
      saveSessions(next); return next;
    });
  }, []);

  const editQueued = useCallback((sessionId: string, messageId: string, text: string): boolean => {
    const row = sessionsRef.current.find(session => session.id === sessionId)?.queued?.find(item => item.id === messageId);
    if (!row || row.delivery === 'sending' || row.delivery === 'unconfirmed' ||
        streamAcc.current[sessionId]?.queuedId === messageId || (!text.trim() && !row.attachments?.length)) return false;
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId
        ? { ...session, updated: nextSessionRevision(session), queued: session.queued?.map(item =>
          item.id === messageId && item.delivery !== 'sending' && item.delivery !== 'unconfirmed'
            ? { ...item, text: text === item.text ? text : text.trim(),
                references: text === item.text ? item.references : undefined,
                sessionReferences: text === item.text ? item.sessionReferences : undefined,
                command: validateChatCommand(text, item.command) ?? undefined, delivery: undefined } : item) } : session);
      saveSessions(next);
      return next;
    });
    return true;
  }, []);

  const changeQueueEdit = useCallback((sessionId: string, id: string | null) => {
    if (queueEdits.current[sessionId] === (id ?? undefined)) return;
    if (id) queueEdits.current[sessionId] = id;
    else delete queueEdits.current[sessionId];
    setQueueEditRevision(value => value + 1);
  }, []);

  const resumeQueue = useCallback((sessionId: string) => {
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId ? { ...session, queuePaused: false, updated: nextSessionRevision(session) } : session);
      saveSessions(next); return next;
    });
  }, []);

  // 重试追加新轮次，保留原回答和服务端回执。
  const handleResend = useCallback((sessionId: string, userIndex: number) => {
    const source = sessionsRef.current.find(session => session.id === sessionId);
    if (!canOperate || !source || source.pendingTurnId || streamAcc.current[sessionId]
        || userIndex + (source.historyWindow?.start ?? 0) <= (source.fork?.cutoffIndex ?? -1)) return;
    const original = source.messages[userIndex];
    const last = source.messages.at(-1);
    const latestUserIndex = source.messages.length - (last?.role === 'assistant' ? 2 : 1);
    if (original?.role !== 'user' || original.planRef || userIndex !== latestUserIndex || last?.status === 'unconfirmed'
        || last?.events?.some(event => event.kind === 'publication' && event.publication)) return;
    const failed = last?.status === 'failed' || !!last?.failure;
    if (failed && (last?.failure?.retryable !== true || last.failure.phase !== 'startup'
      || !['network', 'timeout', 'rate_limit'].includes(last.failure.code))) return;
    sendUserAndStream(sessionId, original.content, original.attachments, original.agentContent, undefined,
      original.topicRef, original.workshopRef, false, original.references, original.sessionReferences, original.command);
  }, [canOperate, sendUserAndStream]);

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
        undefined, { id: idea.id, snapshotHash: idea.snapshotHash });
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

  const handleUseWorkshop = useCallback((reference: WorkshopReference, origin?: string) => {
    if (!canOperate) return;
    const prompt = reference.artifactId ? '请根据本轮引用的工坊成片版本，核对交付状态并给出下一步建议。'
      : '请根据本轮引用的工坊项目和已保存画布状态，给出下一步创作建议。';
    if (origin) {
      const target = sessionsRef.current.find(session => session.id === origin);
      if (!target || target.archived || target.pendingTurnId || target.queued?.length ||
          !sendUserAndStream(origin, prompt, [], undefined, undefined, undefined, reference)) {
        setBusinessNotice('原对话不可用或还有任务，请完成后重试。');
        return;
      }
    } else {
      const session = createSession(selectedPersona || undefined);
      setSessions(previous => {
        const next = [session, ...previous];
        saveSessions(next);
        return next;
      });
      setActiveSessionId(session.id);
      sendUserAndStream(session.id, prompt, [], undefined, undefined, undefined, reference);
    }
    setWorkshopPanel(null);
    setCurrentPage('chat');
    setBusinessNotice('');
  }, [canOperate, selectedPersona, sendUserAndStream]);

  const handleStopStream = useCallback(async (sessionId: string, owner: LiveTurnAccumulator | undefined) => {
    if (!owner || streamAcc.current[sessionId] !== owner || owner.stopRequested) return;
    setSessions(previous => {
      const next = previous.map(session => session.id === sessionId ? { ...session, queuePaused: true, updated: nextSessionRevision(session) } : session);
      saveSessions(next); return next;
    });
    const pending = finalizers.current.get(sessionId);
    if (pending?.owner === owner) { pending.finish(); return; }

    const expectedTurnId = owner.turnId;
    const finishWithoutStop = (kind: 'failed' | 'unconfirmed', message: string) => {
      owner.stopRequested = false;
      if (streamAcc.current[sessionId] !== owner) return;
      const completed = finalizers.current.get(sessionId);
      if (completed?.owner === owner) { completed.finish(); return; }
      setStreams(previous => previous[sessionId] ? { ...previous,
        [sessionId]: { ...previous[sessionId], lifecycle: 'stop_failed', stopFeedback: { kind, message } } } : previous);
    };
    if (!expectedTurnId) {
      finishWithoutStop('unconfirmed', '尚未确认当前回合，正在等待恢复。');
      return;
    }

    owner.stopRequested = true;
    setStreams(previous => previous[sessionId] ? { ...previous,
      [sessionId]: { ...previous[sessionId], lifecycle: 'stopping', stopFeedback: undefined } } : previous);
    let receipt: Awaited<ReturnType<typeof stopChat>>;
    try {
      receipt = await stopChat(sessionId, expectedTurnId);
    } catch {
      finishWithoutStop('failed', '停止请求失败，可对同一回合重试。');
      return;
    }
    if (!receipt.stopped || (receipt.status && receipt.status !== 'stopped')) {
      const message = receipt.status === 'turn_mismatch' ? '当前回合已变更，正在等待原回合结果。'
        : receipt.status === 'already_finished' ? '引擎已结束，正在等待本回合结果。'
        : '停止尚未确认，任务可能仍在运行。';
      finishWithoutStop('unconfirmed', message);
      return;
    }

    if (streamAcc.current[sessionId] !== owner) return;
    if (owner.prompt && !owner.promptAdmitted) {
      // Keep the receipt stream alive until this submission can be matched.
      finishWithoutStop('unconfirmed', '停止回执已收到，仍在核对原发送回合。');
      return;
    }
    if (finalizers.current.get(sessionId)?.owner !== owner) {
      // The HTTP receipt can arrive before the last events on the SSE stream.
      const snapshot = await fetchLastTurn(sessionId, expectedTurnId, AbortSignal.timeout(2000)).catch(() => undefined);
      if (streamAcc.current[sessionId] !== owner) return;
      if (snapshot?.status === 'done' && snapshot.turn_id === expectedTurnId && snapshot.outcome === 'stopped') {
        owner.content = snapshot.text;
        owner.reasoningSummary = snapshot.reasoning_summary ?? owner.reasoningSummary;
        owner.answerCompletion = snapshot.answer_completed ?? owner.answerCompletion;
        typingBuf.current[sessionId] = '';
        if (snapshot.events) {
          owner.timeline = recoverAgentTimeline(snapshot.events, owner.timeline);
          owner.events = snapshot.events.filter(event => event.kind !== 'decision' || event.status !== 'pending')
            .reduce(reduceAgentEvents, owner.events);
        }
        if (snapshot.questions) owner.questions = snapshot.questions;
      }
    }
    owner.stopConfirmed = true;
    owner.stopRequested = false;
    const completed = finalizers.current.get(sessionId);
    if (completed?.owner === owner) { completed.finish(); return; }
    streamCtl.current[sessionId]?.abort();
    flushTyping(sessionId);
    appendAssistant(sessionId, {
      role: 'assistant',
      clientTurnId: expectedTurnId,
      content: owner.content + '\n\n_（已停止）_',
      reasoningSummary: owner.reasoningSummary,
      answerCompletion: owner.answerCompletion,
      activity: owner.steps.join('\n') || undefined,
      questions: owner.questions.length ? [...owner.questions] : undefined,
      events: owner.events.length ? [...owner.events] : undefined,
      timeline: owner.timeline?.length ? owner.timeline : undefined,
      status: 'stopped',
    });
    clearStream(sessionId);
    setSessions((prev) => {
      const next = prev.map((s) => s.id === sessionId && s.pendingTurnId === expectedTurnId
        ? { ...s, pendingTurnId: undefined, updated: nextSessionRevision(s) } : s);
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

  const applySessionArchive = useCallback((id: string, archived: boolean) => {
    setSessions((prev) => {
      const next = prev.map((s) => (s.id === id ? { ...s, archived, updated: nextSessionRevision(s) } : s));
      saveSessions(next);
      return next;
    });
    // 归档当前激活会话 → 切到另一个未归档会话或新建
    if (archived && id === activeIdRef.current) {
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
  }, [selectedPersona]);

  const closeArchiveRequest = () => {
    archiveGeneration.current += 1;
    setArchiveRequest(null);
  };

  const handleSessionArchive = useCallback(async (id: string, archived: boolean) => {
    const generation = ++archiveGeneration.current;
    setArchiveRequest(null);
    if (!archived) { applySessionArchive(id, false); return true; }
    const target = sessionsRef.current.find(session => session.id === id);
    if (!target || target.archived) return false;
    const local = streamAcc.current[id];
    if (local || streamCtl.current[id] || target.pendingTurnId) {
      setArchiveRequest({ id, title: target.title, generation,
        turnId: local?.turnId ?? target.pendingTurnId, remote: !local });
      return true;
    }
    const latest = await fetchLastTurn(id, undefined, AbortSignal.timeout(5000)).catch(() => undefined);
    if (archiveGeneration.current !== generation) return false;
    const current = sessionsRef.current.find(session => session.id === id);
    if (!current || current.archived) return false;
    const owner = streamAcc.current[id];
    if (owner || streamCtl.current[id] || current.pendingTurnId || latest?.status === 'running') {
      setArchiveRequest({ id, title: current.title, generation, remote: !owner,
        turnId: owner?.turnId ?? current.pendingTurnId ?? latest?.turn_id });
    } else if (latest?.status === 'done' || latest?.status === 'none') {
      applySessionArchive(id, true);
    } else {
      setHistoryNotice('暂时无法确认会话是否运行，请稍后再归档。');
      return false;
    }
    return true;
  }, [applySessionArchive]);

  const handleArchiveChoice = useCallback(async (intent: ArchiveIntent, action: 'continue' | 'stop') => {
    const { id, turnId, generation, remote } = intent;
    const matches = () => {
      const session = sessionsRef.current.find(row => row.id === id);
      const owner = streamAcc.current[id];
      return archiveGeneration.current === generation && !!session && !session.archived
        && (!owner || owner.turnId === turnId) && (!session.pendingTurnId || session.pendingTurnId === turnId);
    };
    if (!matches()) return false;
    if (action === 'stop') {
      if (!turnId) return false;
      const owner = streamAcc.current[id];
      try {
        if (owner) await handleStopStream(id, owner);
        else await confirmChatStop(() => stopChat(id, turnId));
      } catch { /* A final snapshot can still confirm the original turn. */ }
      const latest = await fetchLastTurn(id, undefined, AbortSignal.timeout(5000)).catch(() => undefined);
      if (!matches() || latest?.status !== 'done' || latest.turn_id !== turnId
          || (latest.outcome !== 'stopped' && latest.outcome !== 'completed'
            && !(latest.outcome === undefined && latest.clean_end === true))) return false;
    } else if (remote && turnId) {
      const latest = await fetchLastTurn(id, undefined, AbortSignal.timeout(5000)).catch(() => undefined);
      if (!matches() || latest?.turn_id !== turnId
          || (latest.status !== 'running' && latest.status !== 'done')) return false;
    }
    applySessionArchive(id, true);
    if (remote && turnId && !streamAcc.current[id]) resumePendingTurn(id, turnId);
    return true;
  }, [applySessionArchive, handleStopStream, resumePendingTurn]);

  const handleForkAnswer = useCallback(async (sessionId: string, cutoffIndex: number) => {
    const source = sessionsRef.current.find(session => session.id === sessionId);
    const message = source?.messages[cutoffIndex];
    if (!canOperate || !source || message?.role !== 'assistant' || message.status !== 'completed'
        || !message.clientTurnId) return null;
    try {
      let sourceMessages = source.messages;
      const start = source.historyWindow?.start ?? 0;
      if (start > 0) {
        const full = await fetchCompleteChatSession(sessionId);
        cutoffIndex += start;
        const confirmed = full.messages[cutoffIndex];
        if (full.id !== sessionId || confirmed?.clientTurnId !== message.clientTurnId || confirmed.content !== message.content) return null;
        sourceMessages = full.messages;
      }
      const prefixHash = await forkPrefixHash(sourceMessages, cutoffIndex);
      const key = JSON.stringify([sessionId, message.clientTurnId, cutoffIndex, prefixHash]);
      const requestId = forkRequests.current.get(key) ?? crypto.randomUUID();
      forkRequests.current.set(key, requestId);
      const { session: child } = await forkChatSession(sessionId, {
        cutoffIndex, cutoffTurnId: message.clientTurnId, prefixHash, requestId,
      });
      if (!child?.id || child.id === sessionId || child.fork?.sourceSessionId !== sessionId
          || child.fork.cutoffIndex !== cutoffIndex || child.fork.cutoffTurnId !== message.clientTurnId
          || child.fork.prefixHash !== prefixHash) return null;
      forkRequests.current.delete(key);
      setSessions(previous => {
        if (previous.some(session => session.id === child.id)) return previous;
        const updated = [child, ...previous];
        saveSessions(updated);
        return updated;
      });
      return child;
    } catch {
      return null;
    }
  }, [canOperate]);

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
    const target = sessions.find(s => s.id === id);
    if (target) {
      setSelectedPersona(target.persona || '');
    }
    setCurrentPage('chat');
  }, [sessions]);

  const handleEditHistory = useCallback(async (sessionId: string, userIndex: number) => {
    const source = sessionsRef.current.find(session => session.id === sessionId);
    const original = source?.messages[userIndex];
    if (!canOperate || !source || original?.role !== 'user' || userIndex + (source.historyWindow?.start ?? 0) <= (source.fork?.cutoffIndex ?? -1)
        || !source.messages.slice(userIndex + 1).some(message => message.role === 'user')) {
      throw new Error('这条消息无法从历史记录中修订。');
    }
    if (userIndex === 0 && source.historyWindow?.before) {
      throw new Error('请先加载更早的对话，再从这条消息创建修订分支。');
    }
    const cutoff = source.messages[userIndex - 1];
    if (cutoff?.role !== 'assistant' || cutoff.status !== 'completed' || !cutoff.clientTurnId) {
      throw new Error('这条消息之前没有可核验的已完成回答，无法在此处分支。可复制原文到新对话。');
    }
    const child = await handleForkAnswer(sessionId, userIndex - 1);
    if (!child) throw new Error('分支创建未获确认，请刷新历史后重试。');
    if (!saveChatDraft(child.id, { text: original.content, attachments: [], interruptedFiles: [] })) {
      throw new Error('分支已创建，但无法保存修订草稿。可在新分支粘贴原文。');
    }
    if (chatOrigin.current === sessionId) {
      setActiveSessionId(child.id);
      setSelectedPersona(child.persona || '');
    }
  }, [canOperate, handleForkAnswer]);

  const handleSessionDelete = useCallback((id: string) => {
    const target = sessionsRef.current.find(session => session.id === id);
    if (target) setDeleteRequest({ id, title: target.title });
  }, []);

  const executeSessionDelete = useCallback(async (id: string): Promise<boolean> => {
    const target = sessionsRef.current.find(session => session.id === id);
    if (!target) return false;
    const owner = streamAcc.current[id];
    let expectedTurnId = owner ? owner.turnId : target.pendingTurnId;
    if (!owner && !streamCtl.current[id] && !expectedTurnId) {
      const latest = await fetchLastTurn(id, undefined, AbortSignal.timeout(5000)).catch(() => undefined);
      if (latest?.status === 'running') {
        if (!latest.turn_id) return false;
        expectedTurnId = latest.turn_id;
      } else if (latest?.status !== 'done' && latest?.status !== 'none') return false;
    }
    if (owner || streamCtl.current[id] || expectedTurnId) {
      if (!expectedTurnId) return false;
      try {
        await confirmChatStop(() => stopChat(id, expectedTurnId!));
      } catch {
        setHistoryNotice('停止尚未确认，原对话仍可查看。');
        return false;
      }
      const latest = await fetchLastTurn(id, undefined, AbortSignal.timeout(5000)).catch(() => undefined);
      const currentOwner = streamAcc.current[id];
      const currentPending = sessionsRef.current.find(session => session.id === id)?.pendingTurnId;
      if ((currentOwner && currentOwner !== owner) || (currentPending && currentPending !== expectedTurnId)
          || latest?.status !== 'done' || latest.turn_id !== expectedTurnId) {
        setHistoryNotice('原回合状态已变化，请刷新后再删除。');
        return false;
      }
    }
    try {
      await deleteChatSession(id);
    } catch {
      return false;
    }
    streamCtl.current[id]?.abort();
    clearStream(id);
    clearChatDraft(id);
    const pending = loadPromptAttempt(id);
    if (pending) forgetPromptAttempt(id, pending.turnId);
    try {
      sessionStorage.removeItem(projectStorageKey(`easel_pending_turn:${id}`));
      sessionStorage.removeItem(projectStorageKey(`easel_chat_reading:${id}`));
    } catch { /* Browser storage is optional. */ }

    setSessions(previous => {
      const updated = previous.filter(session => session.id !== id);
      if (id === activeIdRef.current) {
        const fallback = updated.find(session => !session.archived);
        if (fallback) setActiveSessionId(fallback.id);
        else {
          const newSession = createSession();
          updated.unshift(newSession);
          setActiveSessionId(newSession.id);
        }
      }
      saveSessions(updated);
      return updated;
    });
    setHistoryNotice('对话已从 Easel 目录删除。原生历史、交付物和操作回执已保留。');
    return true;
  }, [clearStream]);

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
      case 'chat': {
        const stopOwner = activeSession ? streamAcc.current[activeSession.id] : undefined;
        const pending = activeSession ? loadPromptAttempt(activeSession.id) : undefined;
        const canReconcile = pending && pending.state !== 'accepted' && !stopOwner;
        const admissionMessage = activeSession && (admissionFeedback[activeSession.id]
          || (canReconcile && '上一条发送尚未确认。可核对原请求，当前草稿不会被发送。')
          || (pending?.state === 'accepted' && pending.composer && '上一条发送已恢复。若仍显示同一草稿，再次点击发送只清理该草稿。'));
        return activeSession ? (
          <ChatPage
            apiDirect={apiDirect}
            key={activeSession.id}
            session={activeSession}
            onSendSuperagent={canOperate ? chatSuperagent.send : undefined}
            superagentTasks={chatSuperagent.tasks}
            stream={streams[activeSession.id]}
            modelCapability={modelCatalog?.sessionId === activeSession.id ? modelCatalog.capability : undefined}
            modelLoading={modelCatalog?.sessionId !== activeSession.id || modelCatalog.loading}
            nextModel={activeSession.nextModel}
            turnModel={streams[activeSession.id]?.requestedModel
              ?? (!streams[activeSession.id] && modelCatalog?.sessionId === activeSession.id
                ? modelCatalog.capability?.lastRequestedModel : undefined)}
            onChangeModel={model => selectNextModel(activeSession.id, model)}
            onReloadModels={() => setModelReload(value => value + 1)}
            onNewContextChat={canOperate ? () => {
              businessNavigation.current += 1;
              setBusinessNotice('');
              setWorkshopPanel(null);
              handleNewChat();
            } : undefined}
            queue={activeSession.queued || []}
            queuePaused={activeSession.queuePaused}
            onRemoveQueued={id => removeQueued(activeSession.id, id)}
            onEditQueued={(id, text) => editQueued(activeSession.id, id, text)}
            onQueueEditChange={id => changeQueueEdit(activeSession.id, id)}
            onResumeQueue={() => resumeQueue(activeSession.id)}
            historyNotice={historyNotice}
            admissionNotice={admissionMessage ? { message: admissionMessage,
              ...(canReconcile
                ? { action: { label: '核对上一条发送', run: () => reconcilePromptAttempt(activeSession.id) } } : {}),
            } : undefined}
            onForkAnswer={canOperate ? index => handleForkAnswer(activeSession.id, index) : undefined}
            onOpenFork={handleSessionSelect}
            onLoadEarlier={canOperate ? handleLoadEarlier : undefined}
            onEditHistory={canOperate ? handleEditHistory : undefined}
            onSend={(displayText, attachments, references, sessionReferences, command) =>
              handleSendMessage(activeSession.id, displayText, attachments, references, sessionReferences, command)}
            onSteer={streams[activeSession.id]?.steerTurnId && streams[activeSession.id]?.lifecycle === 'running'
              ? text => handleSteer(activeSession.id, text) : undefined}
            onStop={() => handleStopStream(activeSession.id, stopOwner)}
            onOpenFailureSettings={isPlatformAdmin ? () => setSettingsOpen(true) : undefined}
            onResend={userIndex => handleResend(activeSession.id, userIndex)}
            onQuestionAnswered={receipt => {
              answeredRef.current.add(receipt.id);
              closeQuestion(activeSession.id, receipt);
            }}
          />
        ) : null;
      }
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
          initialProjectId={businessEntry?.kind === 'workshop' ? businessEntry.id : undefined}
          initialArtifactId={businessEntry?.artifactId}
          onUseWorkshop={handleUseWorkshop} /></Suspense>;
      case 'interactions':
        return <Suspense fallback={<div className="dash-empty">正在打开互动中心…</div>}><InteractionCenterPage key={interactionEntry} onManageAccounts={() => setCurrentPage('accounts')} /></Suspense>;
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

  const closeWorkshopJobs = () => setWorkshopJobsOpen(false);
  const openWorkshopJob = (id: string, artifactId?: string) => {
    if (!/^[a-f0-9]{32}$/.test(id)) return;
    businessNavigation.current += 1;
    setBusinessNotice('');
    setWorkshopPanel(null);
    setBusinessEntry({ kind: 'workshop', id,
      ...(/^[a-f0-9]{32}$/.test(artifactId ?? '') ? { artifactId } : {}) });
    setWorkshopEntry(value => value + 1);
    setCurrentPage('workshop');
    closeWorkshopJobs();
  };

  const visibleWorkshop = canOperate && currentPage === 'chat' && workshopPanel?.sessionId === activeSessionId
    ? workshopPanel : null;
  return (
    <div className="app-layout">
      <Sidebar
        currentPage={currentPage}
        onPageChange={page => {
          businessNavigation.current += 1; setBusinessEntry(null); setBusinessNotice(''); setWorkshopPanel(null);
          if (page === 'workshop') setWorkshopEntry(entry => entry + 1);
          if (page === 'interactions') setInteractionEntry(entry => entry + 1);
          setCurrentPage(page);
        }}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSessionSelect={id => { businessNavigation.current += 1; setBusinessNotice(''); setWorkshopPanel(null); handleSessionSelect(id); }}
        onSessionDelete={handleSessionDelete}
        onSessionRename={handleSessionRename}
        onSessionArchive={handleSessionArchive}
        onSessionPin={handleSessionPin}
        sessionStates={Object.fromEntries(sessions.map(session => [session.id, sessionAttentionState(session, streams[session.id])]))}
        onNewChat={() => { businessNavigation.current += 1; setBusinessNotice(''); setWorkshopPanel(null); handleNewChat(); }}
        gatewayStatus={gatewayStatus}
        apiDirect={apiDirect}
        agentRuntime={agentRuntime}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <main className="main-content">
        <ProjectBreadcrumb>
          <button type="button" className="btn btn-sm space-breadcrumb-action"
            aria-haspopup="dialog" aria-expanded={workshopJobsOpen} onClick={() => setWorkshopJobsOpen(true)}>
            后台任务
          </button>
        </ProjectBreadcrumb>
        {historyNotice && currentPage !== 'chat' && <p className="agent-event__note" role="status">{historyNotice}</p>}
        {businessNotice && <p className="agent-event__note" role="status">{businessNotice}</p>}
        {(['trends', 'ideas', 'breakdown'] as Page[]).includes(currentPage) && (
          <SubNav current={currentPage} onNavigate={page => { businessNavigation.current += 1;
            setBusinessEntry(null); setBusinessNotice(''); setCurrentPage(page); }} />
        )}
        <div className={visibleWorkshop ? 'page-host workshop-chat-layout' : 'page-host'}>
          {renderPage()}
          {visibleWorkshop && <aside className="workshop-chat-panel" aria-label="对话关联的视频工坊">
            <div className="workshop-chat-panel-header">
              <strong>视频工坊</strong>
              <button className="btn btn-sm" type="button" onClick={() => setWorkshopPanel(null)}>关闭工坊</button>
            </div>
            <Suspense fallback={<div className="dash-empty">正在打开视频工坊…</div>}>
              <VideoWorkshopPage key={visibleWorkshop.id + ':' + (visibleWorkshop.artifactId || '')}
                initialProjectId={visibleWorkshop.id} initialArtifactId={visibleWorkshop.artifactId}
                initialView={isPlatformAdmin && !visibleWorkshop.artifactId ? 'canvas' : 'review'}
                onUseWorkshop={reference => handleUseWorkshop(reference, visibleWorkshop.sessionId)} />
            </Suspense>
          </aside>}
        </div>
      </main>

      {deleteRequest && <DeleteChatDialog key={deleteRequest.id} sessionTitle={deleteRequest.title}
        onClose={() => setDeleteRequest(null)}
        onDelete={() => executeSessionDelete(deleteRequest.id)}
        onArchive={() => handleSessionArchive(deleteRequest.id, true)} />}

      {archiveRequest && <ArchiveRunningChatDialog key={archiveRequest.generation}
        sessionTitle={archiveRequest.title} onClose={closeArchiveRequest}
        onContinue={() => handleArchiveChoice(archiveRequest, 'continue')}
        onStopAndArchive={() => handleArchiveChoice(archiveRequest, 'stop')} />}

      {workshopJobsOpen && <Suspense fallback={null}>
        <WorkshopJobsDrawer open canOperate={canOperate} onClose={closeWorkshopJobs} onOpenWorkshop={openWorkshopJob} />
      </Suspense>}

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
            setModelReload(value => value + 1);
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
