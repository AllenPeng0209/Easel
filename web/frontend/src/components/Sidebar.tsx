import { AuthAccountMenu, useAuth } from './AuthGate';
import { ProjectSwitcher, useIpPermissions } from './ProjectSpace';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ChatSession } from '../lib/store';
import type { PersonaItem } from '../lib/api';
import type { ComponentType } from 'react';
import {
  IconChat, IconSkills, IconOutputs, IconAccounts, IconProfile,
  IconNewChat, IconEdit, IconArchive, IconUnarchive, IconTrash, IconChevron,
  IconDashboard, IconVideo, IconInteractions, IconIdea, IconCalendar, IconPublish,
} from './icons';
import { IconGear } from './settingsIcons';
import ThemeToggle from './ThemeToggle';
import SessionLogExportDialog from './SessionLogExportDialog';
import { fetchSessionLogZip } from '../lib/api';
import { downloadSessionMarkdown, navigationSessions, visibleHistoryCount } from '../lib/sessionNavigation';
import type { ArchiveFilter } from '../lib/sessionNavigation';
import { useModalLayer } from '../vendor/deepseek-harness/navigation/useModalLayer';
import '../styles/sidebar.css';
import '../styles/session-navigation.css';

export type Page = 'dashboard' | 'chat' | 'trends' | 'ideas' | 'calendar' | 'publish' | 'breakdown' | 'skills' | 'outputs' | 'accounts' | 'profile' | 'workshop' | 'interactions';

interface SidebarProps {
  currentPage: Page;
  onPageChange: (page: Page) => void;
  personas: PersonaItem[];
  selectedPersona: string;
  onPersonaChange: (persona: string) => void;
  onNewProfile: () => void;
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSessionSelect: (id: string) => void;
  onSessionDelete: (id: string) => void;
  onSessionRename: (id: string, title: string) => void;
  onSessionArchive: (id: string, archived: boolean) => void;
  onSessionPin?: (id: string, pinned: boolean) => void;
  sessionStates?: Record<string, string>;
  onNewChat: () => void;
  gatewayStatus: string;
  apiDirect: boolean;
  agentRuntime: string;
  onOpenSettings: () => void;
}

interface NavigationItem { page: Page; Icon: ComponentType<{ size?: number }>; label: string }

const PRODUCTION_NAV: NavigationItem[] = [
  { page: 'profile', Icon: IconProfile, label: '创作画像' },
  { page: 'ideas', Icon: IconIdea, label: '选题策划' },
  { page: 'workshop', Icon: IconVideo, label: '视频工坊' },
  { page: 'outputs', Icon: IconOutputs, label: '内容库' },
  { page: 'skills', Icon: IconSkills, label: '技能库' },
];

const OPERATIONS_NAV: NavigationItem[] = [
  { page: 'dashboard', Icon: IconDashboard, label: '运营总览' },
  { page: 'accounts', Icon: IconAccounts, label: '账号管理' },
  { page: 'publish', Icon: IconPublish, label: '发布中心' },
  { page: 'calendar', Icon: IconCalendar, label: '内容日历' },
  { page: 'interactions', Icon: IconInteractions, label: '互动中心' },
];

// DeepSeek workspace rows put requests for human input ahead of live work
// and unread completion. Keep these categories distinct in the compact view.
const SESSION_ATTENTION = [
  { key: 'approval', label: '待审批' },
  { key: 'question', label: '待回答' },
  { key: 'stop_failed', label: '停止失败' },
  { key: 'failed', label: '执行失败' },
  { key: 'recovering', label: '待核对任务' },
  { key: 'stopping', label: '正在停止' },
  { key: 'running', label: '运行中' },
  { key: 'completed-unread', label: '完成未读' },
] as const;

function sessionAttention(state: string | undefined) {
  const key = state === 'waiting' ? 'question' : state === 'streaming' ? 'running'
    : state === 'unread' ? 'completed-unread' : state;
  return SESSION_ATTENTION.find(item => item.key === key);
}

export default function Sidebar({
  currentPage,
  onPageChange,
  personas,
  selectedPersona,
  onPersonaChange,
  onNewProfile,
  sessions,
  activeSessionId,
  onSessionSelect,
  onSessionDelete,
  onSessionRename,
  onSessionArchive,
  onSessionPin,
  sessionStates = {},
  onNewChat,
  gatewayStatus,
  apiDirect,
  agentRuntime,
  onOpenSettings,
}: SidebarProps) {
  const { canOperate, isPlatformAdmin } = useIpPermissions();
  const auth = useAuth();
  const canExportLogs = canOperate && auth?.user.is_admin === true;
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyQuery, setHistoryQuery] = useState('');
  const [archiveFilter, setArchiveFilter] = useState<ArchiveFilter>('default');
  const [historyLimit, setHistoryLimit] = useState(5);
  const [archiveLimit, setArchiveLimit] = useState(5);
  const [exportError, setExportError] = useState('');
  const [logSessionId, setLogSessionId] = useState<string>();
  const logSession = canExportLogs ? sessions.find(session => session.id === logSessionId) : undefined;
  // Upstream ui-layout uses a 768px drawer boundary and a 56px desktop rail.
  const [drawer, setDrawer] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const sidebarColumn = useRef<HTMLDivElement>(null);
  const restoreRailToggle = useRef(false);
  useModalLayer(sidebarColumn, drawer && drawerOpen, () => setDrawerOpen(false));
  useEffect(() => {
    const media = window.matchMedia('(max-width: 767px)');
    const change = () => { setDrawer(media.matches); setDrawerOpen(false); };
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, []);
  useLayoutEffect(() => {
    if (!drawer || !drawerOpen) return;
    const main = sidebarColumn.current?.parentElement?.querySelector<HTMLElement>('main.main-content');
    if (!main) return;
    const previous = main.inert;
    main.inert = true;
    return () => { main.inert = previous; };
  }, [drawer, drawerOpen]);
  useLayoutEffect(() => {
    if (!restoreRailToggle.current || drawer) return;
    restoreRailToggle.current = false;
    sidebarColumn.current?.querySelector<HTMLButtonElement>('[data-sidebar-toggle]')?.focus({ preventScroll: true });
  }, [drawer, railCollapsed]);
  const closeDrawer = () => setDrawerOpen(false);
  const toggleRail = () => { restoreRailToggle.current = true; setRailCollapsed(value => !value); };

  const startRename = (s: ChatSession) => { setRenamingId(s.id); setRenameValue(s.title); };
  const commitRename = () => {
    if (renamingId) onSessionRename(renamingId, renameValue);
    setRenamingId(null);
  };

  const filtered = navigationSessions(sessions, historyQuery, archiveFilter, activeSessionId);
  const active = archiveFilter === 'default' ? filtered : filtered.filter(session => !session.archived);
  const archived = navigationSessions(sessions, historyQuery, 'only', activeSessionId);
  const historyWindow = visibleHistoryCount(active, historyLimit, activeSessionId, sessionStates);
  const visibleArchives = archived.slice(0, archiveLimit);
  const historyCount = sessions.filter((s) => s.messages.length > 0).length;
  const archivedOpen = showArchived || archiveFilter !== 'default';
  const attentionCounts = new Map<string, number>();
  if (canOperate) {
    for (const session of sessions) {
      if (session.archived) continue;
      const status = sessionAttention(sessionStates[session.id]);
      if (status) attentionCounts.set(status.key, (attentionCounts.get(status.key) || 0) + 1);
    }
  }
  const attention = SESSION_ATTENTION.filter(item => attentionCounts.has(item.key));
  const attentionLabel = attention.map(item => `${item.label} ${attentionCounts.get(item.key)}`).join('，');
  const attentionTotal = [...attentionCounts.values()].reduce((total, count) => total + count, 0);
  const attentionBadge = attentionTotal > 0 && <span aria-hidden="true"
    className={`session-attention-count state-${attention[0].key}`}>{attentionTotal}</span>;
  const exportSession = (session: ChatSession) => {
    try { downloadSessionMarkdown(session); setExportError(''); }
    catch { setExportError('导出未成功，请稍后重试。'); }
  };

  const downloadLogs = async (id: string, signal: AbortSignal) => {
    const { blob, filename } = await fetchSessionLogZip(id, signal);
    if (signal.aborted) return;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    try {
      document.body.appendChild(anchor);
      anchor.click();
    } finally {
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  };

  const renderNavigation = ({ page, Icon, label }: NavigationItem) => {
    const selected = currentPage === page || (page === 'ideas' && ['trends', 'breakdown'].includes(currentPage));
    return <button key={page} className={`nav-item ${selected ? 'active' : ''}`}
      aria-current={selected ? 'page' : undefined}
      onClick={() => {
        if ((page === 'publish' && !canOperate) || (page === 'skills' && !isPlatformAdmin)) return;
        onPageChange(page);
        closeDrawer();
      }}>
      <span className="nav-icon"><Icon size={17} /></span>{label}
    </button>;
  };

  const renderItem = (s: ChatSession, isArchived: boolean) => {
    const attentionStatus = sessionAttention(sessionStates[s.id]);
    if (renamingId === s.id) {
      return (
        <div key={s.id} className="session-item">
          <input
            className="session-rename-input"
            aria-label="重命名对话"
            value={renameValue}
            autoFocus
            onChange={(e) => setRenameValue(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename();
              else if (e.key === 'Escape') setRenamingId(null);
            }}
            onBlur={commitRename}
          />
        </div>
      );
    }
    return (
      <div
        key={s.id}
        className={`session-item ${s.id === activeSessionId ? 'active' : ''}`}
        role="button"
        tabIndex={0}
        onClick={() => { if (!isArchived) { onSessionSelect(s.id); closeDrawer(); } }}
        onKeyDown={(event) => {
          if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
            event.preventDefault();
            if (!isArchived) { onSessionSelect(s.id); closeDrawer(); }
          }
        }}
      >
        {(s as ChatSession & { pinned?: boolean }).pinned && !isArchived && <span className="session-pin-mark" aria-label="已置顶">⌖</span>}
        <span className="session-item-title" title={s.title}>{s.title}</span>
        {attentionStatus && <span className={`session-attention-label state-${attentionStatus.key}`}>{attentionStatus.label}</span>}
        <div className="session-actions">
          {!isArchived && onSessionPin && <button className="session-act" title={(s as ChatSession & { pinned?: boolean }).pinned ? '取消置顶' : '置顶'}
            onClick={(e) => { e.stopPropagation(); onSessionPin(s.id, !(s as ChatSession & { pinned?: boolean }).pinned); }}>⌖</button>}
          <button className="session-act" title="重命名"
            onClick={(e) => { e.stopPropagation(); startRename(s); }}><IconEdit size={14} /></button>
          <button className="session-act" title={isArchived ? '取消归档' : '归档'}
            onClick={(e) => { e.stopPropagation(); onSessionArchive(s.id, !isArchived); }}>
            {isArchived ? <IconUnarchive size={14} /> : <IconArchive size={14} />}
          </button>
          <button className="session-act" type="button" aria-label="导出 Markdown"
            title="导出 Markdown" aria-description="可见对话，不含工具日志"
            onClick={(e) => { e.stopPropagation(); exportSession(s); }}>↓</button>
          {canExportLogs && <button className="session-act" type="button" aria-label="导出技术日志 ZIP"
            title="导出技术日志 ZIP（平台管理员）" data-session-log-export
            onClick={event => { event.stopPropagation(); setLogSessionId(s.id); }}>ZIP</button>}
          <button className="session-act danger" title="删除"
            onClick={(e) => { e.stopPropagation(); onSessionDelete(s.id); }}><IconTrash size={14} /></button>
        </div>
      </div>
    );
  };

  return (<>
    {drawer && !drawerOpen && <button className="sidebar-mobile-opener" aria-label="打开导航" aria-expanded={false}
      aria-controls="easel-sidebar" data-sidebar-opener onClick={() => setDrawerOpen(true)}>☰</button>}
    {drawer && drawerOpen && <div className="sidebar-drawer-mask" aria-hidden="true" onClick={closeDrawer} />}
    <div id="easel-sidebar" ref={sidebarColumn}
      className={`sidebar${drawer ? ' sidebar-drawer' : ''}${drawer && !drawerOpen ? ' drawer-closed' : ''}${!drawer && railCollapsed ? ' sidebar-collapsed' : ''}`}
      role={drawer ? 'dialog' : undefined} aria-label={drawer ? '主导航' : undefined} aria-modal={drawer && drawerOpen ? true : undefined}
      tabIndex={drawer ? -1 : undefined} inert={drawer && !drawerOpen ? true : undefined}>
      {!drawer && railCollapsed ? <div className="sidebar-rail">
        <button className="sidebar-shell-toggle" aria-label="展开侧栏" title="展开侧栏" data-sidebar-toggle onClick={toggleRail}>☰</button>
        <button className="sidebar-rail-action" aria-label="AI 对话" aria-description={attentionLabel || undefined}
          title={attentionLabel || 'AI 对话'} disabled={!canOperate}
          onClick={() => { if (canOperate) onPageChange('chat'); }}><IconChat size={20} />{attentionBadge}</button>
        <button className="sidebar-rail-action" aria-label="新建对话" title="新建对话" disabled={!canOperate}
          onClick={() => { if (canOperate) onNewChat(); }}><IconNewChat size={19} /></button>
        {isPlatformAdmin && <button className="sidebar-rail-action sidebar-rail-settings" aria-label="设置" title="设置" onClick={onOpenSettings}><IconGear size={18} /></button>}
      </div> : <>
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <img className="sidebar-logo-icon" src="./static/easel-icon-transparent.png" alt="" />
          <h1>Easel</h1>
          <ThemeToggle />
          <button className="sidebar-shell-toggle" aria-label={drawer ? '关闭导航' : '收起侧栏'} title={drawer ? '关闭导航' : '收起侧栏'}
            data-sidebar-toggle data-modal-autofocus={drawer || undefined} onClick={() => drawer ? closeDrawer() : toggleRail()}>{drawer ? '×' : '‹'}</button>
        </div>
        <ProjectSwitcher />
      </div>

      <div className="sidebar-body">
        <nav className="sidebar-navigation" aria-label="主导航">
          <section className="sidebar-ai-section" aria-label="AI 工作入口">
            <div className={`sidebar-ai-entry ${currentPage === 'chat' ? 'active' : ''}`}>
              <button className="sidebar-ai-link" aria-label="AI 对话" disabled={!canOperate}
                aria-current={currentPage === 'chat' ? 'page' : undefined}
                title={canOperate ? '从 AI 对话开始创作与运营' : '当前为查看者，AI 对话需要运营权限'}
                onClick={() => { if (canOperate) { onPageChange('chat'); closeDrawer(); } }}>
                <span className="sidebar-ai-icon"><IconChat size={20} /></span>
                <span><strong>AI 对话</strong><small>{canOperate ? '所有工作的入口' : '当前角色只读'}</small></span>
              </button>
              <button className="sidebar-new-chat" aria-label="新建对话" title="新建对话" disabled={!canOperate}
                onClick={() => { if (canOperate) { onNewChat(); closeDrawer(); } }}><IconNewChat size={17} /></button>
            </div>

            {currentPage === 'chat' && canOperate && <div className="sidebar-chat-context">
              <label htmlFor="sidebar-chat-persona">创作画像</label>
              <select id="sidebar-chat-persona" className="persona-select" value={selectedPersona}
                title="选择用户画像（一个对话对应一个画像：切换会新建一个对话）"
                onChange={(event) => {
                  if (event.target.value === '__new__') { onNewProfile(); return; }
                  onPersonaChange(event.target.value);
                }}>
                <option value="">通用模式</option>
                {personas.map(persona => <option key={persona.name} value={persona.name}>{persona.name}</option>)}
                <option value="__new__">+ 新建画像…</option>
              </select>
            </div>}

            {canOperate && <>
              <button className="sidebar-history-toggle" aria-label="历史对话"
                aria-description={!historyOpen ? attentionLabel || undefined : undefined}
                title={!historyOpen ? attentionLabel || undefined : undefined} aria-expanded={historyOpen}
                aria-controls="sidebar-chat-history" onClick={() => setHistoryOpen(value => !value)}>
                <span className={`sidebar-history-chevron ${historyOpen ? 'open' : ''}`}><IconChevron size={11} /></span>
                <span>历史对话</span>{!historyOpen && attentionTotal > 0 && <span aria-hidden="true"
                  className={`session-attention-count state-${attention[0].key}`}>动态 {attentionTotal}</span>}
                <span className="sidebar-history-count">{historyCount || '暂无'}</span>
              </button>
              {historyOpen && <div id="sidebar-chat-history" className="sidebar-chat-history">
                <div className="session-history-controls">
                  <input type="search" aria-label="搜索历史对话" placeholder="搜索标题或会话 ID"
                    value={historyQuery} onChange={(event) => { setHistoryQuery(event.target.value); setHistoryLimit(5); setArchiveLimit(5); }} />
                  <select aria-label="归档筛选" value={archiveFilter}
                    onChange={(event) => { setArchiveFilter(event.target.value as ArchiveFilter); setHistoryLimit(5); setArchiveLimit(5); }}>
                    <option value="default">未归档</option><option value="show">全部对话</option><option value="only">仅归档</option>
                  </select>
                </div>
                {active.slice(0, historyWindow).map(session => renderItem(session, false))}
                {active.length > historyWindow && <button className="session-show-more" aria-label="显示更多历史对话"
                  onClick={() => setHistoryLimit(value => Math.max(value, historyWindow) + 5)}>显示更多 · 还有 {active.length - historyWindow} 条</button>}
                {!active.length && !archived.length && <p className="sidebar-history-empty">{historyQuery ? '没有匹配的对话。' : '还没有对话，从 AI 对话开始吧。'}</p>}
                {!active.length && archived.length > 0 && archiveFilter === 'default' && <p className="sidebar-history-empty">{historyQuery ? '未归档中无匹配，可展开已归档。' : '暂无未归档对话。'}</p>}
                {archived.length > 0 && <>
                  <button className="sidebar-archive-toggle" aria-expanded={archivedOpen} aria-controls="sidebar-archived-history"
                    onClick={() => { if (archiveFilter !== 'default') { setArchiveFilter('default'); setShowArchived(false); } else setShowArchived(value => !value); }}>
                    <span className={`archived-chevron ${archivedOpen ? 'open' : ''}`}><IconChevron size={10} /></span>
                    已归档<span>{archived.length}</span>
                  </button>
                  {archivedOpen && <div id="sidebar-archived-history">
                    <p className="session-archive-hint">取消归档后可继续对话</p>
                    {visibleArchives.map(session => renderItem(session, true))}
                    {archived.length > archiveLimit && <button className="session-show-more" aria-label="显示更多归档对话"
                      onClick={() => setArchiveLimit(value => value + 5)}>显示更多归档 · 还有 {archived.length - archiveLimit} 条</button>}
                  </div>}
                </>}
                {exportError && <p className="session-export-error" role="alert">{exportError}</p>}
              </div>}
            </>}
          </section>

          <section className="sidebar-nav-group" aria-label="内容生产">
            <h2 className="sidebar-group-title">内容生产</h2>
            {PRODUCTION_NAV.filter(item => item.page !== 'skills' || isPlatformAdmin).map(renderNavigation)}
          </section>
          <section className="sidebar-nav-group" aria-label="运营管理">
            <h2 className="sidebar-group-title">运营管理</h2>
            {OPERATIONS_NAV.filter(item => item.page !== 'publish' || canOperate).map(renderNavigation)}
          </section>
        </nav>
      </div>

      <div className="sidebar-footer">
        <AuthAccountMenu />
        <div className="sidebar-status">
          <span className={`status-dot ${gatewayStatus === 'connected' ? '' : 'offline'}`} />
          <span className="sidebar-status-text">{gatewayStatus === 'connected'
            ? (apiDirect ? '仅聊天模式已配置' : agentRuntime !== 'openclaw' ? `${agentRuntime} CLI 已安装` : '网关已连接')
            : gatewayStatus === 'disconnected'
              ? (apiDirect ? 'API 配置不完整' : agentRuntime !== 'openclaw' ? `${agentRuntime} CLI 未配置` : '网关离线')
              : '连接中…'}</span>
          {isPlatformAdmin && <button className="settings-gear" onClick={onOpenSettings} title="设置（模型 · 环境 · 更多）">
            <IconGear size={13} /> 设置
          </button>}
        </div>
      </div>
      </>}
    </div>
    {logSession && <SessionLogExportDialog key={logSession.id} sessionId={logSession.id} sessionTitle={logSession.title}
      onDownload={downloadLogs} onClose={() => setLogSessionId(undefined)} />}
  </>);
}
