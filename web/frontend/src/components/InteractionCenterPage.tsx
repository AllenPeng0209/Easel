import { useIpPermissions } from './ProjectSpace';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { IconChat, IconChevron, IconRefresh } from './icons';
import {
  approveInteractionDraft, buildInteractionDraft, createInteractionChannel, createInteractionClient,
  fetchInteractionBootstrap, filterInteractionConversations, getInteractionConversation,
  interactionStatus, kindName, performInteractionAction, platformName, runInteractionAutomation,
  saveInteractionDraft, saveInteractionKnowledge, seedInteractionSimulation, sendInteractionDraft,
  syncInteractionChannel, updateInteractionKnowledge, updateInteractionRules, fetchNativeInteractionOptions, refreshNativeInteractionSources,
} from '../lib/interactions';
import type {
  InteractionBootstrap, InteractionDetail, InteractionKnowledge, InteractionMode,
  InteractionPlatform, InteractionKind, InteractionRules, NativeInteractionOptions,
} from '../lib/interactions';
import '../styles/interactions.css';

const EMPTY: InteractionBootstrap = { client_id: '', clients: [], channels: [], conversations: [], knowledge: [],
  rules: { mode: 'assist', enabled: false, blocked_keywords: [] }, counts: { pending: 0, review: 0, handled: 0, failed: 0 }, capabilities: [] };
const dateLabel = (value?: string) => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '';
const message = (cause: unknown) => cause instanceof Error ? cause.message : '操作未完成，请稍后再试。';
const blankKnowledge = { question: '', keywords: '', answer: '', auto_send: false, enabled: true };
const blankChannel = { name: '', platform: 'douyin' as InteractionPlatform, kind: 'comment' as InteractionKind,
  delivery_mode: 'native' as InteractionMode, account_label: '', source_url: '', env_prefix: '', account_id: '', video_id: '', profile_name: '', conversation_id: '' };

export default function InteractionCenterPage({ onManageAccounts }: { onManageAccounts?: () => void } = {}) {
  const { canOperate, canManageAccounts } = useIpPermissions();
  const [data, setData] = useState<InteractionBootstrap>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [tab, setTab] = useState<'inbox' | 'knowledge' | 'channels'>('inbox');
  const [showDemo, setShowDemo] = useState(false);
  const [status, setStatus] = useState('all');
  const [platform, setPlatform] = useState('all');
  const [kind, setKind] = useState('all');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [detail, setDetail] = useState<InteractionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [replyText, setReplyText] = useState('');
  const [replyDirty, setReplyDirty] = useState(false);
  const [busy, setBusy] = useState('');
  const [form, setForm] = useState<'client' | 'knowledge' | 'channel' | null>(null);
  const [clientName, setClientName] = useState('');
  const [knowledgeId, setKnowledgeId] = useState('');
  const [knowledgeForm, setKnowledgeForm] = useState(blankKnowledge);
  const [channelForm, setChannelForm] = useState(blankChannel);
  const [nativeOptions,setNativeOptions]=useState<NativeInteractionOptions>({accounts:[],works:[],conversations:[]});
  const [syncUrls, setSyncUrls] = useState<Record<string, string>>({});
  const [rules, setRules] = useState<InteractionRules>(EMPTY.rules);
  const [rulesDirty, setRulesDirty] = useState(false);
  const clientRef = useRef('');
  const selectedRef = useRef('');
  const latestDetail = useRef<InteractionDetail | null>(null);
  const operationError = useRef(false);
  const taskSequence = useRef(0);
  const loadSequence = useRef(0);
  const detailSequence = useRef(0);
  const dirtyRef = useRef(false);
  const rulesDirtyRef = useRef(false);
  const editRevision = useRef(0);
  const editDraftId = useRef('');
  const formSession = useRef(0);
  const sendOperations = useRef<Record<string, string>>({});
  const clientId = data.client_id;
  const hasRealChannel = data.channels.some(item => item.delivery_mode !== 'simulation');
  const visibleMessages = data.conversations.filter(item => showDemo || !hasRealChannel || item.delivery_mode !== 'simulation');
  const visibleChannels = data.channels.filter(item => showDemo || !hasRealChannel || item.delivery_mode !== 'simulation');
  const conversations = filterInteractionConversations(visibleMessages, { status, platform, kind, query });
  const syncableChannels = visibleChannels.filter(item => item.enabled && item.capabilities?.can_sync);
  const draft = detail?.drafts?.filter(item => item.status !== 'superseded').at(-1);
  const channel = data.channels.find(item => item.id === detail?.channel_id);
  const simulation = detail?.delivery_mode === 'simulation';
  const nativeForm = channelForm.delivery_mode === 'native';
  const creatorHubForm = channelForm.delivery_mode === 'creatorhub';
  const unsettled = detail?.sends?.some(item => ['unknown', 'sending'].includes(item.status));
  const canSend = canOperate && !!detail && !!draft && draft.status === 'approved' && !replyDirty && !unsettled
    && (simulation || channel?.capabilities?.can_send === true || channel?.capabilities?.send_ready === true);

  useEffect(() => {
    if (form !== 'channel' || !nativeForm) return;
    let active=true;
    fetchNativeInteractionOptions().then(options => {if (active) setNativeOptions(options);})
      .catch(cause => {if (active) setError(message(cause));});
    return () => {active=false;};
  },[form,nativeForm]);

  const adoptDetail = useCallback((next: InteractionDetail, forceText = false) => {
    if (next.client_id !== clientRef.current || next.id !== selectedRef.current) return;
    if (latestDetail.current?.id === next.id && latestDetail.current.revision > next.revision) return;
    latestDetail.current = next;
    setDetail(next);
    const current = next.drafts?.filter(item => item.status !== 'superseded').at(-1);
    if (forceText || !dirtyRef.current) {
      setReplyText(current?.text || ''); setReplyDirty(false); dirtyRef.current = false;
      editRevision.current = next.revision; editDraftId.current = current?.id || '';
    }
  }, []);

  const adopt = useCallback((next: InteractionBootstrap, allowSwitch = false, invalidateRead = true) => {
    if (!allowSwitch && clientRef.current && next.client_id !== clientRef.current) return;
    if (invalidateRead) ++loadSequence.current;
    clientRef.current = next.client_id; setData(next);
    if (!rulesDirtyRef.current) setRules(next.rules);
  }, []);

  const load = useCallback(async () => {
    const sequence = ++loadSequence.current;
    const scope = clientRef.current;
    try {
      const next = await fetchInteractionBootstrap(scope);
      if (sequence !== loadSequence.current || scope !== clientRef.current) return;
      adopt(next, false, false); if (!operationError.current) setError('');
      const currentId = selectedRef.current;
      if (currentId) {
        const nextDetail = await getInteractionConversation(currentId, next.client_id);
        if (sequence === loadSequence.current && currentId === selectedRef.current) adoptDetail(nextDetail);
      }
    } catch (cause) { if (sequence === loadSequence.current && !operationError.current) setError(message(cause)); }
    finally { if (sequence === loadSequence.current) setLoading(false); }
  }, [adopt, adoptDetail]);

  const invalidateReads = useCallback(() => { ++loadSequence.current; ++detailSequence.current; }, []);

  useEffect(() => {
    void load();
    const refresh = () => { if (document.visibilityState === 'visible') void load(); };
    const timer = window.setInterval(refresh, 8000);
    window.addEventListener('focus', refresh);
    return () => { invalidateReads(); window.clearInterval(timer); window.removeEventListener('focus', refresh); };
  }, [load, invalidateReads]);

  const clearSelection = () => {
    ++detailSequence.current; selectedRef.current = ''; latestDetail.current = null; setSelectedId(''); setDetail(null);
    setDetailLoading(false); setReplyText(''); setReplyDirty(false); dirtyRef.current = false; editDraftId.current = '';
  };
  const switchClient = (id: string) => {
    ++loadSequence.current; ++taskSequence.current; setBusy(''); operationError.current = false; clientRef.current = id; clearSelection();
    setData(previous => ({ ...EMPTY, clients: previous.clients, client_id: id }));
    rulesDirtyRef.current = false; setRulesDirty(false); setRules(EMPTY.rules); setForm(null); ++formSession.current;
    setShowDemo(false); setError(''); setNotice(''); setLoading(true); void load();
  };
  const open = async (id: string) => {
    const sequence = ++detailSequence.current; const scope = clientRef.current;
    selectedRef.current = id; latestDetail.current = null; setSelectedId(id); setDetail(null); setDetailLoading(true);
    setReplyText(''); setReplyDirty(false); dirtyRef.current = false; setError(''); setNotice('');
    try {
      const next = await getInteractionConversation(id, scope);
      if (sequence === detailSequence.current && scope === clientRef.current) adoptDetail(next, true);
    } catch (cause) { if (sequence === detailSequence.current && scope === clientRef.current) setError(message(cause)); }
    finally { if (sequence === detailSequence.current) setDetailLoading(false); }
  };
  const task = async (name: string, action: (scope: string, active: () => boolean) => Promise<void>) => {
    if (!canOperate || busy) return;
    const scope = clientRef.current;
    const sequence = ++taskSequence.current;
    const active = () => sequence === taskSequence.current && scope === clientRef.current;
    operationError.current = false; setBusy(name); setError(''); setNotice('');
    try { await action(scope, active); }
    catch (cause) { if (active()) { operationError.current = true; setError(message(cause)); } }
    finally { if (sequence === taskSequence.current) setBusy(''); }
  };
  const seed = () => task('seed', async (scope, active) => {
    const next = await seedInteractionSimulation(scope);
    if (!active()) return;
    setShowDemo(true); adopt(next); setNotice('模拟消息已载入，测试不会联系真实用户。');
  });
  const syncMessages = () => task('sync-all', async (scope, active) => {
    let synced = 0;
    const failed: string[] = [];
    for (const item of syncableChannels) {
      if (!active()) return;
      try {
        const next = await syncInteractionChannel(item.id, scope, ['native', 'creatorhub'].includes(item.delivery_mode) ? '' : item.source_url || '');
        if (!active()) return;
        adopt(next);
        synced += 1;
      } catch { if (!active()) return; failed.push(item.name); }
    }
    if (!active()) return;
    if (failed.length) throw new Error(`已同步 ${synced} 个来源，${failed.join('、')}暂缓同步，请稍后重试。`);
    setNotice(`已同步 ${synced} 个消息来源。`);
  });
  const refreshSources = () => task('sources', async () => {
    const session = formSession.current;
    const options = await refreshNativeInteractionSources(channelForm.account_id, channelForm.kind);
    if (session === formSession.current) setNativeOptions(options);
  });
  const copyReply = () => task('copy', async () => {
    await navigator.clipboard.writeText(replyText);
    setNotice('回复已复制，可粘贴到平台处理。');
  });
  const draftAction = (action: 'generate' | 'save' | 'approve' | 'send') => task(action, async (scope, active) => {
    if (!detail) return;
    let next: InteractionDetail;
    if (action === 'generate') next = await buildInteractionDraft(detail.id, scope, detail.revision);
    else if (action === 'save') {
      if (!editDraftId.current) return;
      next = await saveInteractionDraft(editDraftId.current, scope, editRevision.current, replyText.trim());
    } else if (action === 'approve') {
      if (!draft || replyDirty) return;
      next = await approveInteractionDraft(draft.id, scope, detail.revision);
    } else {
      if (!draft || !canSend) return;
      const key = `${detail.client_id}:${draft.id}:${detail.revision}`;
      sendOperations.current[key] ||= `interaction-ui-${crypto.randomUUID()}`;
      next = await sendInteractionDraft(draft.id, scope, detail.revision, sendOperations.current[key]);
    }
    if (!active()) return;
    adoptDetail(next, true); void load();
    setNotice(action === 'send' ? simulation ? '模拟发送已登记，可在处理记录查看回执。' : '发送结果已保存，请核对回执。'
      : action === 'approve' ? simulation ? '模拟审核已记录，下一步可以模拟发送。' : '审核已记录。' : action === 'save' ? '草稿已保存，需重新审核后发送。' : '回复建议已生成，请核对后再审核。');
  });
  const action = (value: 'takeover' | 'resolve' | 'reopen') => task(value, async (scope, active) => {
    if (!detail) return;
    const next = await performInteractionAction(detail.id, scope, detail.revision, value);
    if (!active()) return;
    adoptDetail(next, true); void load();
  });
  const discardReply = () => task('discard', async (scope, active) => {
    if (!detail) return;
    const next = await getInteractionConversation(detail.id, scope);
    if (!active()) return;
    adoptDetail(next, true); setNotice('已放弃本地修改，重新读取当前草稿。');
  });
  const showForm = (type: 'client' | 'knowledge' | 'channel', item?: InteractionKnowledge) => {
    if (!canOperate || (type === 'channel' && !canManageAccounts)) return;
    ++formSession.current; operationError.current = false; setForm(type); setError(''); setClientName(''); setChannelForm(blankChannel);
    setKnowledgeId(item?.id || ''); setKnowledgeForm(item ? { ...item, keywords: item.keywords.join('，') } : blankKnowledge);
  };
  const closeForm = () => { ++formSession.current; setForm(null); setError(''); };
  const saveForm = async (event: FormEvent) => {
    event.preventDefault();
    if (!canOperate || (form === 'channel' && !canManageAccounts)) return;
    const session = formSession.current;
    await task('form', async () => {
      if (form === 'client') {
        const next = await createInteractionClient(clientName.trim());
        if (session !== formSession.current) return;
        clearSelection(); rulesDirtyRef.current = false; setRulesDirty(false); adopt(next, true);
      } else if (form === 'channel') {
        const fields = nativeForm ? ['account_id','video_id','conversation_id'] : creatorHubForm ? ['env_prefix'] : ['env_prefix', 'account_id', 'video_id', 'profile_name'];
        const config = Object.fromEntries(fields
          .map(key => [key, channelForm[key as keyof typeof channelForm].trim()]).filter(([, value]) => value));
        const account = nativeOptions.accounts.find(item => item.id === channelForm.account_id);
        const sources = channelForm.kind === 'comment' ? nativeOptions.works : nativeOptions.conversations;
        const source = sources.find(item => item.account_id === channelForm.account_id && item.value === (channelForm.kind === 'comment' ? channelForm.video_id : channelForm.conversation_id));
        const name = channelForm.name.trim() || `${account?.name || platformName(channelForm.platform)} · ${source?.name || kindName(channelForm.kind)}`;
        adopt(await createInteractionChannel(clientRef.current, { name: name.slice(0,120), platform: channelForm.platform,
          kind: channelForm.kind, delivery_mode: channelForm.delivery_mode, account_label: account?.name || channelForm.account_label,
          source_url: creatorHubForm || nativeForm ? '' : channelForm.source_url, config }));
      }
      else {
        const input = { question: knowledgeForm.question.trim(), answer: knowledgeForm.answer.trim(),
          keywords: knowledgeForm.keywords.split(/[,，\n]/).map(value => value.trim()).filter(Boolean),
          auto_send: knowledgeForm.auto_send, enabled: knowledgeForm.enabled };
        adopt(knowledgeId ? await updateInteractionKnowledge(knowledgeId, clientRef.current, input)
          : await saveInteractionKnowledge(clientRef.current, input));
      }
      if (session === formSession.current) closeForm();
    });
  };

  return <div className="interaction-center">
    <header className="interaction-header"><div>
      <h1>互动中心</h1><p>集中处理评论与私信。</p></div>
      <div className="interaction-header-actions">
        {data.clients.length > 1 && <select className="field interaction-client-select" aria-label="客户工作区" value={clientId} onChange={event => switchClient(event.target.value)}>
          {data.clients.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}
        <button className="btn btn-sm" aria-label="刷新互动中心" onClick={() => void load()}><IconRefresh size={13} />刷新</button>
        {canOperate && <button className="btn btn-sm btn-primary" disabled={!!busy || !syncableChannels.length} onClick={() => void syncMessages()}><IconRefresh size={13} />{busy === 'sync-all' ? '同步中…' : '同步消息'}</button>}
        {canOperate && <details className="interaction-more"><summary aria-label="更多互动选项">···</summary><div>
          <button type="button" onClick={() => showForm('client')}>＋ 新增客户</button>
          <button type="button" disabled={!!busy || !clientId} onClick={() => void seed()}>载入模拟消息</button>
        </div></details>}
      </div></header>
    <div className="interaction-tabs" role="tablist" aria-label="互动中心功能" onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const options = ['inbox', 'knowledge', 'channels'] as const;
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (options.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : 2)) % 3;
      setTab(options[index]); document.getElementById(`interaction-${options[index]}-tab`)?.focus();
    }}>
      {([['inbox', '收件箱'], ['knowledge', '回复规则'], ['channels', '账号连接']] as const).map(([key, label]) =>
        <button role="tab" id={`interaction-${key}-tab`} key={key} aria-selected={tab === key} aria-controls={`interaction-${key}-panel`}
          tabIndex={tab === key ? 0 : -1} onClick={() => setTab(key)}>{label}</button>)}</div>
    {error && <div className="interaction-error" role="alert">{error}<button type="button" onClick={() => void load()}>重新读取</button></div>}
    {!canOperate && <p className="interaction-notice" role="status">只读权限：可以查看消息、草稿、知识库与处理记录。</p>}
    {notice && <p className="interaction-notice" role="status">{notice}</p>}
    {tab === 'inbox' && <section className={`interaction-inbox ${selectedId ? 'has-selection' : ''}`} id="interaction-inbox-panel" role="tabpanel" aria-labelledby="interaction-inbox-tab">
      <div className="interaction-list"><div className="interaction-list-top">
        <div className="interaction-list-heading"><strong>全部消息</strong><span>{visibleMessages.length}</span></div>
        <input className="field" type="search" aria-label="搜索会话" placeholder="搜索联系人或消息" value={query} onChange={event => setQuery(event.target.value)} />
        <div className="interaction-list-selects"><select className="field" aria-label="筛选互动平台" value={platform} onChange={event => setPlatform(event.target.value)}><option value="all">全部平台</option><option value="xiaohongshu">小红书</option><option value="douyin">抖音</option></select>
          <select className="field" aria-label="筛选会话状态" value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部状态</option><option value="pending">待处理</option><option value="review">待审核</option><option value="handled">已处理</option></select></div>
        <div className="interaction-queue-tabs" role="group" aria-label="筛选消息类型">{[['all', '全部'], ['comment', '评论'], ['dm', '私信']].map(([key, label]) =>
          <button type="button" key={key} aria-pressed={kind === key} onClick={() => setKind(key)}>{label}</button>)}</div></div>
        <div className="interaction-conversation-list" aria-label="会话列表">{conversations.map(item => <button key={item.id} type="button"
          className={`interaction-conversation ${selectedId === item.id ? 'selected' : ''}`} aria-label={`打开会话：${item.author_name} · ${item.subject}`} onClick={() => void open(item.id)}>
          <div className="interaction-conversation-title"><span className="interaction-avatar">{item.author_name.slice(0, 1) || '访'}</span><strong>{item.author_name}</strong><time>{dateLabel(item.updated_at)}</time></div>
          <h3>{item.subject}</h3><p>{item.preview}</p><div className="interaction-row-labels"><span>{platformName(item.platform)} · {kindName(item.kind)}</span>
            {item.delivery_mode === 'simulation' && <span className="interaction-simulation">模拟</span>}<span className={`interaction-state state-${item.status}`}>{interactionStatus(item.status)}</span>
            {item.human_takeover && <span>人工接管</span>}</div></button>)}
          {!conversations.length && <div className="interaction-empty"><IconChat size={28} /><h2>{loading ? '正在读取消息…' : visibleMessages.length ? '没有匹配的会话' : '暂时没有新消息'}</h2>
            <p>{visibleMessages.length ? '调整搜索或筛选条件。' : '连接账号并添加消息来源后，在这里接收和处理咨询。'}</p>{!visibleMessages.length && <button className="btn btn-sm" onClick={() => setTab('channels')}>连接消息来源</button>}</div>}</div></div>
      <div className="interaction-detail">{detailLoading ? <div className="interaction-empty" role="status">正在打开会话…</div> : detail ? <>
        <div className="interaction-detail-head"><button className="btn btn-sm btn-ghost interaction-detail-back" onClick={clearSelection}>返回消息列表</button>
          <div><h2>{detail.author_name}</h2><p>{platformName(detail.platform)} · {kindName(detail.kind)} · {detail.subject}</p></div>
          <span className={`interaction-state state-${detail.status}`}>{interactionStatus(detail.status)}</span></div>
        {simulation && <div className="interaction-simulation-banner" aria-label="模拟验收会话">模拟验收消息 · 发送只生成本地回执，不会联系真实用户。</div>}
        {detail.source_url && <p className="interaction-hint"><a href={detail.source_url} target="_blank" rel="noreferrer">查看原作品</a></p>}
        {detail.human_takeover && <div className="interaction-takeover">已由人工接管，自动回复暂停。</div>}
        {detail.last_error && <p className="interaction-inline-error">{detail.last_error}</p>}
        <div className="interaction-message-thread" aria-label="会话消息">{detail.messages.map(item => <article key={item.id} className={`interaction-message ${item.direction}`}>
          <div><span>{item.direction === 'outbound' ? '工作区回复' : item.author_name}</span><time>{dateLabel(item.created_at)}</time></div><p>{item.text}</p>
          {item.simulated && <span className="interaction-message-foot">模拟消息</span>}</article>)}</div>
        <div className="interaction-composer"><div className="interaction-composer-title"><strong>回复</strong>
          {canOperate && <button className="btn btn-sm" disabled={!!busy || replyDirty || !!unsettled} onClick={() => void draftAction('generate')}>{busy === 'generate' ? '正在生成…' : '生成回复建议'}</button>}</div>
          {draft && <p className="interaction-source">{draft.source === 'knowledge_base' ? '来源：客户知识库匹配 · 请核对后审核' : draft.source === 'manual_required' ? '未匹配到知识，请填写回复或补充知识库' : draft.source === 'agent' ? '来源：Agent 草稿 · 待人工核对' : '来源：人工编辑'}
            {draft.status === 'approved' ? ' · 已审核' : draft.status === 'sent' ? ' · 已发送' : ' · 待审核'}</p>}
          <label><span className="interaction-sr-only">回复内容</span><textarea className="field" rows={4} aria-label="回复内容" maxLength={10000} value={replyText}
            readOnly={!canOperate} disabled={!canOperate || !!busy || !draft || draft.status === 'sent' || !!unsettled} placeholder={draft ? '在这里加工回复；保存后需要重新审核。' : '先生成建议，再编辑与审核。'}
            onChange={event => { if (!canOperate) return; if (!dirtyRef.current) { editRevision.current = detail.revision; editDraftId.current = draft?.id || ''; }
              dirtyRef.current = true; setReplyDirty(true); setReplyText(event.target.value); }} /></label>
          {unsettled && <p className="interaction-inline-error" role="status">发送结果待核对，请先确认原回执，避免重复发送。</p>}
          {!simulation && !canSend && <p className="interaction-hint">{['native', 'creatorhub'].includes(detail.delivery_mode) ? '当前连接支持读取。可复制回复到平台处理。' : '发送需要平台连接可用，并审核当前回复。'}</p>}
          {canOperate && <div className="interaction-composer-actions"><button className="btn btn-sm" disabled={!!busy || !replyDirty || !replyText.trim() || !!unsettled} onClick={() => void draftAction('save')}>保存草稿</button>
            <button className="btn btn-sm" disabled={!!busy || !draft || !draft.text.trim() || draft.status !== 'draft' || replyDirty || !!unsettled} onClick={() => void draftAction('approve')}>{simulation ? '模拟审核' : '审核通过'}</button>
            {!simulation && ['native', 'creatorhub', 'manual'].includes(detail.delivery_mode) ? <button className="btn btn-sm btn-primary" disabled={!!busy || !replyText.trim()} onClick={() => void copyReply()}>复制回复</button>
              : <button className="btn btn-sm btn-primary" disabled={!!busy || !canSend} onClick={() => void draftAction('send')}>{busy === 'send' ? '正在提交…' : simulation ? '模拟发送' : '发送回复'}</button>}</div>}
          {canOperate && replyDirty && <button className="interaction-discard" disabled={!!busy} onClick={() => void discardReply()}>放弃本地修改并刷新</button>}
          {canOperate && <div className="interaction-case-actions"><button className="btn btn-sm btn-ghost" disabled={!!busy || detail.human_takeover} onClick={() => void action('takeover')}>人工接管</button>
            {detail.status === 'handled' ? <button className="btn btn-sm btn-ghost" disabled={!!busy} onClick={() => void action('reopen')}>重新打开</button>
              : <button className="btn btn-sm btn-ghost" disabled={!!busy || !!unsettled} onClick={() => void action('resolve')}>标记已处理</button>}</div>}</div>
        <details className="interaction-history"><summary>处理记录与发送回执 <IconChevron size={12} /></summary>
          {detail.sends.map((item, index) => <div key={`${item.operation_id}-${index}`} className="interaction-receipt"><strong>{item.mode === 'simulation' ? '模拟发送' : '平台发送'} · {item.status === 'sent' ? '成功' : item.status === 'unknown' ? '结果待核对' : item.status === 'sending' ? '提交中' : '失败'}</strong>
            <p>{item.message}</p><code>{item.receipt_id || item.operation_id}</code><time>{dateLabel(item.created_at)}</time></div>)}
          {[...detail.events].reverse().map(item => <div className="interaction-event" key={item.id}><time>{dateLabel(item.created_at)}</time><p>{item.message || item.type}</p></div>)}
          {!detail.events.length && !detail.sends.length && <p className="interaction-hint">尚无处理记录。</p>}</details>
      </> : <div className="interaction-empty interaction-detail-empty"><IconChat size={34} /><h2>选择一条评论或私信</h2><p>左侧选择消息，在这里查看上下文和处理回复。</p></div>}</div>
    </section>}
    {tab === 'knowledge' && <section className="interaction-settings-scroll" id="interaction-knowledge-panel" role="tabpanel" aria-labelledby="interaction-knowledge-tab">
      <div className="interaction-settings-card"><div className="interaction-card-title"><div><h2>接待规则</h2><p>先核对知识，再决定哪些问题可以自动回复。</p></div></div>
        <div className="interaction-rule-grid"><label><span className="field-label">接待模式</span><select className="field" value={rules.mode} aria-label="接待模式" disabled={!canOperate}
          onChange={event => { if (!canOperate) return; rulesDirtyRef.current = true; setRulesDirty(true); setRules({ ...rules, mode: event.target.value as InteractionRules['mode'] }); }}>
          <option value="manual">人工处理</option><option value="assist">建议回复 · 人工审核</option><option value="auto_faq">知识库常见问题自动回复</option></select></label>
          <label className="interaction-check"><input type="checkbox" checked={rules.enabled} disabled={!canOperate} onChange={event => { if (!canOperate) return; rulesDirtyRef.current = true; setRulesDirty(true); setRules({ ...rules, enabled: event.target.checked }); }} />启用自动接待规则</label>
          <label className="interaction-rule-keywords"><span className="field-label">需要人工处理的关键词</span><input className="field" aria-label="转人工关键词" readOnly={!canOperate} value={rules.blocked_keywords.join('，')}
            onChange={event => { if (!canOperate) return; rulesDirtyRef.current = true; setRulesDirty(true); setRules({ ...rules, blocked_keywords: event.target.value.split(/[,，]/) }); }} /></label></div>
        <p className="interaction-hint">自动回复需要平台发送权限，并只使用允许自动回复的常用答复。其他问题交给人工处理。</p>
        {canOperate && <div className="interaction-settings-actions"><button className="btn btn-sm" disabled={!!busy || rulesDirty || !rules.enabled} onClick={() => void task('automation', async (scope, active) => { const next = await runInteractionAutomation(scope); if (!active()) return; adopt(next); setNotice('已完成一轮规则处理，请到收件箱核对结果。'); })}>运行一轮规则</button>
          <button className="btn btn-sm btn-primary" disabled={!!busy || !rulesDirty} onClick={() => void task('rules', async (scope, active) => { const next = await updateInteractionRules(scope, { ...rules, blocked_keywords: rules.blocked_keywords.map(value => value.trim()).filter(Boolean) });
            if (!active()) return; rulesDirtyRef.current = false; setRulesDirty(false); adopt(next); setNotice('接待规则已保存。'); })}>保存规则</button></div>}</div>
      <div className="interaction-settings-card"><div className="interaction-card-title"><div><h2>常用回复</h2><p>保存常见问题的标准答复，用于生成回复建议。</p></div>{canOperate && <button className="btn btn-sm" onClick={() => showForm('knowledge')}>＋ 新增知识</button>}</div>
        {data.knowledge.map(item => <article className={`interaction-knowledge-row ${!item.enabled ? 'disabled' : ''}`} key={item.id}><div><h3>{item.question}</h3><p>{item.answer}</p><span>{item.keywords.join(' · ') || '没有关键词'}{item.auto_send ? ' · 允许自动回复' : ' · 需人工审核'}</span></div>
          {canOperate && <div><button className="btn btn-sm btn-ghost" onClick={() => showForm('knowledge', item)}>编辑</button><button className="btn btn-sm btn-ghost" disabled={!!busy} onClick={() => void task('knowledge', async () => adopt(await updateInteractionKnowledge(item.id, clientRef.current, { enabled: !item.enabled })))}>{item.enabled ? '停用' : '启用'}</button></div>}</article>)}
        {!data.knowledge.length && <div className="interaction-empty"><h3>把常见问题变成可靠的回复依据</h3><p>添加常见问题及其确认后的答复。</p></div>}</div>
    </section>}
    {tab === 'channels' && <section className="interaction-settings-scroll" id="interaction-channels-panel" role="tabpanel" aria-labelledby="interaction-channels-tab">
      <div className="interaction-settings-card"><div className="interaction-card-title"><div><h2>消息来源</h2><p>选择账号的作品评论或私信会话。</p></div>{canManageAccounts && <button className="btn btn-sm" onClick={() => showForm('channel')}>添加来源</button>}</div>
        {visibleChannels.map(item => <article className="interaction-channel-row" key={item.id}><div className="interaction-channel-heading"><div><h3>{item.name}</h3><p>{platformName(item.platform)} · {kindName(item.kind)}{item.account_label ? ` · ${item.account_label}` : ''}</p></div>
          <span className={item.delivery_mode === 'simulation' ? 'interaction-simulation' : 'interaction-channel-status'}>{item.delivery_mode === 'native' ? '已关联' : item.delivery_mode === 'creatorhub' ? '迁移前的只读通道' : item.delivery_mode === 'simulation' ? '模拟通道' : item.status === 'connected' ? '已连接' : item.delivery_mode === 'manual' ? '人工接待' : '待授权'}</span></div>
          {(!item.capabilities?.can_sync || !item.last_sync_at) && <p className="interaction-hint">{item.capabilities?.message || (item.delivery_mode === 'simulation' ? '用于本机验收，数据与真实平台分开，发送不会触达用户。' : '需要完成账号授权并核验接口权限后才能收发。')}</p>}
          {item.last_sync_at && <p className="interaction-hint">最近成功同步 · {dateLabel(item.last_sync_at)}</p>}
          {canOperate && item.capabilities?.can_sync && <div className="interaction-channel-sync">
            {!['creatorhub','native'].includes(item.delivery_mode) && <input className="field" type="url" aria-label={`同步链接：${item.name}`} placeholder="粘贴已选择的作品链接" value={syncUrls[item.id] || ''} onChange={event => setSyncUrls({ ...syncUrls, [item.id]: event.target.value })} />}
            <button className="btn btn-sm" disabled={!!busy || (!['creatorhub','native'].includes(item.delivery_mode) && !syncUrls[item.id]?.trim())} onClick={() => void task('sync', async (scope, active) => {
              const source = ['creatorhub','native'].includes(item.delivery_mode) ? '' : syncUrls[item.id].trim();
              const next = await syncInteractionChannel(item.id, scope, source); if (!active()) return; adopt(next); setNotice('同步结果已保存，可进入收件箱查看。');
            })}>同步评论</button></div>}</article>)}
        {!visibleChannels.length && <div className="interaction-empty"><h3>还没有消息来源</h3><p>连接平台账号，再添加要接收的评论或私信。</p></div>}</div>
      {onManageAccounts && <div className="interaction-connection-guide"><p>需要连接或重新登录平台账号？</p><button className="btn btn-sm" onClick={onManageAccounts}>管理账号</button></div>}
    </section>}
    {canOperate && form && (form !== 'channel' || canManageAccounts) && <div className="interaction-modal-backdrop"><form className="interaction-modal" role="dialog" aria-modal="true" aria-label={form === 'client' ? '新增客户工作区' : form === 'channel' ? '添加消息来源' : knowledgeId ? '编辑知识条目' : '新增知识条目'} onSubmit={event => void saveForm(event)}
      onKeyDown={event => { if (event.key === 'Escape' && !busy) closeForm(); if (event.key !== 'Tab') return;
        const items = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled])')];
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
        else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); } }}>
      <div className="interaction-card-title"><h2>{form === 'client' ? '新增客户工作区' : form === 'channel' ? '添加消息来源' : knowledgeId ? '编辑知识条目' : '新增知识条目'}</h2><button type="button" className="icon-btn" aria-label="关闭互动表单" disabled={!!busy} onClick={closeForm}>×</button></div>
      {form === 'client' ? <label><span className="field-label">客户名称</span><input className="field" required autoFocus maxLength={120} value={clientName} onChange={event => setClientName(event.target.value)} /></label>
        : form === 'channel' ? <>
          <div className="interaction-form-grid"><label><span className="field-label">平台</span><select className="field" autoFocus disabled={creatorHubForm} value={channelForm.platform} onChange={event => setChannelForm({ ...channelForm, platform: event.target.value as InteractionPlatform, account_id: '', video_id: '', conversation_id: '' })}><option value="xiaohongshu">小红书</option><option value="douyin">抖音</option></select></label>
            <label><span className="field-label">消息类型</span><select className="field" disabled={creatorHubForm} value={channelForm.kind} onChange={event => setChannelForm({ ...channelForm, kind: event.target.value as InteractionKind, video_id: '', conversation_id: '' })}><option value="comment">评论</option><option value="dm">私信</option></select></label></div>


          {nativeForm ? <>
            <label><span className="field-label">平台账号</span><select className="field" aria-label="内置平台账号" required value={channelForm.account_id}
              onChange={event => setChannelForm({...channelForm,account_id:event.target.value,video_id:'',conversation_id:''})}>
              <option value="">请选择已登录账号</option>{nativeOptions.accounts.filter(account=>account.platform===channelForm.platform)
                .map(account=><option key={account.id} value={account.id}>{account.name}</option>)}</select></label>
            <label><span className="field-label">{channelForm.kind==='comment'?'自有作品':'私信会话'}</span><select className="field" aria-label="内置消息来源" required
              value={channelForm.kind==='comment'?channelForm.video_id:channelForm.conversation_id}
              onChange={event => setChannelForm({...channelForm,[channelForm.kind==='comment'?'video_id':'conversation_id']:event.target.value})}>
              <option value="">请选择作品或会话；没有结果时点击刷新来源</option>
              {(channelForm.kind==='comment'?nativeOptions.works:nativeOptions.conversations).filter(item=>item.account_id===channelForm.account_id)
                .map(item=><option key={item.value} value={item.value}>{item.name}</option>)}</select></label>
            <button className="btn btn-sm" type="button" disabled={!!busy || !channelForm.account_id} onClick={() => void refreshSources()}>{busy === 'sources' ? '正在读取…' : '刷新来源'}</button>
          </> : creatorHubForm ? <label><span className="field-label">服务端绑定前缀</span>
            <input className="field" aria-label="CreatorHub 服务端绑定前缀" required placeholder="EASEL_INTERACTION_DEFAULT_CREATORHUB_PILOT" maxLength={100}
              value={channelForm.env_prefix} onChange={event => setChannelForm({ ...channelForm, env_prefix: event.target.value })} />
          </label> : <details className="interaction-advanced"><summary>高级关联设置</summary>
            <label><span className="field-label">作品来源链接</span><input className="field" type="url" aria-label="渠道来源链接" maxLength={4096} value={channelForm.source_url} onChange={event => setChannelForm({ ...channelForm, source_url: event.target.value })} /></label>
            <label><span className="field-label">环境变量前缀</span><input className="field" aria-label="环境变量前缀" placeholder="EASEL_INTERACTION_CUSTOMER_A" maxLength={100} value={channelForm.env_prefix} onChange={event => setChannelForm({ ...channelForm, env_prefix: event.target.value })} /></label>
            <div className="interaction-form-grid"><label><span className="field-label">平台账号 ID</span><input className="field" aria-label="平台账号ID" maxLength={256} value={channelForm.account_id} onChange={event => setChannelForm({ ...channelForm, account_id: event.target.value })} /></label>
              <label><span className="field-label">视频作品 ID</span><input className="field" aria-label="视频作品ID" maxLength={256} value={channelForm.video_id} onChange={event => setChannelForm({ ...channelForm, video_id: event.target.value })} /></label></div>
            <label><span className="field-label">浏览器配置名称</span><input className="field" aria-label="浏览器配置名称" maxLength={64} value={channelForm.profile_name} onChange={event => setChannelForm({ ...channelForm, profile_name: event.target.value })} /></label>
            <p className="interaction-hint">只保存账号与配置引用，不在这里填写密钥或 Cookie。</p>
          </details>}
          <details className="interaction-advanced"><summary>高级设置</summary><label><span className="field-label">连接名称</span><input className="field" maxLength={120} placeholder="留空时使用账号和来源名称" value={channelForm.name} onChange={event => setChannelForm({ ...channelForm, name: event.target.value })} /></label>          <label><span className="field-label">接入方式</span><select className="field" value={channelForm.delivery_mode} onChange={event => {
            const mode = event.target.value as InteractionMode;
            setChannelForm(mode === 'creatorhub' ? { ...channelForm, delivery_mode: mode, platform: 'douyin', kind: 'comment' }
              : { ...channelForm, delivery_mode: mode });
          }}><option value="simulation">模拟通道 · 本机验收</option><option value="manual">人工接待</option><option value="official">官方接口 · 待授权配置</option><option value="native">内置平台账号</option></select></label>
</details>
          <p className="interaction-hint">新建渠道只保存关联信息，不会登录账号或自动取得平台权限。</p></>
          : <><label><span className="field-label">常见问题</span><input className="field" required autoFocus maxLength={300} value={knowledgeForm.question} onChange={event => setKnowledgeForm({ ...knowledgeForm, question: event.target.value })} /></label>
            <label><span className="field-label">匹配关键词</span><input className="field" required placeholder="用逗号分隔，例如：课程介绍，了解课程" value={knowledgeForm.keywords} onChange={event => setKnowledgeForm({ ...knowledgeForm, keywords: event.target.value })} /></label>
            <label><span className="field-label">已确认的回复内容</span><textarea className="field" required rows={5} maxLength={10000} value={knowledgeForm.answer} onChange={event => setKnowledgeForm({ ...knowledgeForm, answer: event.target.value })} /></label>
            <label className="interaction-check"><input type="checkbox" checked={knowledgeForm.auto_send} onChange={event => setKnowledgeForm({ ...knowledgeForm, auto_send: event.target.checked })} />允许该条知识用于自动回复</label>
            <label className="interaction-check"><input type="checkbox" checked={knowledgeForm.enabled} onChange={event => setKnowledgeForm({ ...knowledgeForm, enabled: event.target.checked })} />启用该知识条目</label></>}
      {error && <p className="interaction-error" role="alert">{error}</p>}
      <div className="interaction-settings-actions"><button className="btn btn-sm" type="button" disabled={!!busy} onClick={closeForm}>取消</button><button className="btn btn-sm btn-primary" disabled={!!busy}>{busy === 'form' ? '正在保存…' : '保存'}</button></div>
    </form></div>}
  </div>;
}
