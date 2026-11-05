import { useState, useRef, useEffect, useCallback } from 'react';
import {
  createSchedule, executeSkill, runAgent, streamChat,
  fetchAccounts, publishNow, preparePublication, fetchPublicationApproval, refreshPublicationApproval, fetchPublicationArtifact, decidePublication, submitPublishSms, fetchOutputs, mediaUrl,
} from '../lib/api';
import type { AccountItem, OutputFile } from '../lib/api';
import type { PublicationApprovalRecord } from '../lib/publicationApproval';
import PublicationApprovalDialog, { PublicationSubjectView } from './PublicationApprovalDialog';
import PublicationReceiptCard from './PublicationReceiptCard';
import { publicationReceiptInScope } from '../lib/publicationReceipt';
import { normalizePublicationHandoff, publicationMediaReferenceMatches, validPublicationMediaReference,
  validPublicationApprovalReference, type PublicationHandoff, type PublicationMediaReference,
  type PublicationApprovalReference } from '../lib/publicationHandoff';
import { loadPublishDraft, savePublishDraft } from '../lib/store';
import { renderMarkdown } from '../lib/sanitize';
import { IconPublish, IconCopy, IconCheck, IconCalendar, IconSkills, IconEdit, IconStop, IconTrash } from './icons';

interface PublishPageProps {
  persona: string;
  handoff?: PublicationHandoff | null;
  onHandoffConsumed?: () => void;
}

// 平台列表须与后端 LOGIN_RUNNERS 对齐（有登录/发布链路的才列）
// draft: true = 该平台支持「存草稿」（落后台草稿箱，不公开发布）
const PLATFORMS: { key: string; label: string; titleLimit?: number; bodyLimit: number; hint: string; draft?: boolean }[] = [
  { key: 'xiaohongshu', label: '小红书', titleLimit: 20, bodyLimit: 1000, hint: '标题≤20，正文≤1000，重情绪+话题标签' },
  { key: 'douyin', label: '抖音', titleLimit: 30, bodyLimit: 1000, hint: '标题≤30，正文建议简短' },
  { key: 'kuaishou', label: '快手', titleLimit: 30, bodyLimit: 1000, hint: '视频或图片(图文)，标题≤30，需附媒体' },
  { key: 'weixin-channels', label: '视频号', bodyLimit: 1000, hint: '需附视频，短描述+话题标签，微信扫码登录' },
  { key: 'zhihu', label: '知乎', bodyLimit: 5000, hint: '长文/回答，讲清逻辑' },
  { key: 'juejin', label: '掘金', titleLimit: 100, bodyLimit: 20000, hint: '技术文章（Markdown），标题≤100；发布默认分类，标签需掘金已有标签' },
  { key: 'bilibili', label: 'B站', titleLimit: 80, bodyLimit: 2000, hint: '需附视频，标题≤80、简介≤2000，默认投「知识」分区' },
  { key: 'toutiao', label: '今日头条', titleLimit: 30, bodyLimit: 10000, draft: true, hint: '图文长文，标题≤30 字，话题≤5 个；附件图片插进正文，可存草稿' },
  { key: 'wechat-oa', label: '公众号', titleLimit: 64, bodyLimit: 20000, hint: '图文文章，正文用 Markdown，首图作封面，发到草稿箱；需先在账号页「登录公众号后台」扫码' },
  { key: 'twitter-x', label: '推特 X', bodyLimit: 280, hint: '标题与正文合并，最终长度以 X 校验为准；可附 1 图或 1 视频；窗口登录需要运行 响芽 的电脑有图形桌面及外网连接' },
];
const LABEL2KEY = Object.fromEntries(PLATFORMS.map((p) => [p.label, p.key]));

// 能一键发布的平台（有后端 publisher）
const PUBLISHABLE = new Set(['xiaohongshu', 'douyin', 'kuaishou', 'weixin-channels', 'zhihu', 'juejin', 'bilibili', 'wechat-oa', 'twitter-x', 'toutiao']);
// 必须附带媒体的平台（无媒体发不了）——公众号需要一张封面图，也计入；X 纯文字推文合法，不计入
const MEDIA_REQUIRED = new Set(['xiaohongshu', 'douyin', 'kuaishou', 'weixin-channels', 'bilibili', 'wechat-oa']);
// 只能发视频的平台（抖音/视频号/B站：图文不走此链路，必须视频）
const VIDEO_ONLY = new Set(['douyin', 'weixin-channels', 'bilibili']);
const VIDEO_RE = /\.(mp4|mov|webm|mkv|avi|m4v|flv|ts)$/i;

function parseSections(text: string): Record<string, string> {
  const parts = text.split(/^\s*={2,}\s*(.+?)\s*={2,}\s*$/m);
  const map: Record<string, string> = {};
  for (let i = 1; i < parts.length; i += 2) map[parts[i].trim()] = (parts[i + 1] || '').trim();
  return map;
}

type PubState = { status: 'publishing' | 'ok' | 'fail' | 'pending'; msg: string };

export default function PublishPage({ persona, handoff, onHandoffConsumed }: PublishPageProps) {
  const draft0 = loadPublishDraft();
  const [title, setTitle] = useState(draft0.title);
  const [body, setBody] = useState(draft0.body);
  const [platforms, setPlatforms] = useState<string[]>(draft0.platforms);
  const [overrides, setOverrides] = useState<Record<string, string>>(draft0.overrides);
  const [tags, setTags] = useState(draft0.tags || '');
  const [aiGenerated, setAiGenerated] = useState(draft0.aiGenerated === true);
  // 存草稿模式：只对声明了 draft 能力的平台（今日头条）生效，其余平台仍真发布
  const [asDraft, setAsDraft] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [copied, setCopied] = useState('');
  const [toast, setToast] = useState('');
  const [adapting, setAdapting] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState('');
  const adaptCtl = useRef<AbortController | null>(null);
  // 异步发布轮询的 interval 集合：离开页面统一清掉，否则最长还会继续转 15 分钟
  const pollTimers = useRef<Set<ReturnType<typeof setInterval>>>(new Set());
  const aliveRef = useRef(true);

  // 发布相关
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [mediaFiles, setMediaFiles] = useState<OutputFile[]>([]);
  const [selectedMedia, setSelectedMedia] = useState<string[]>([]);
  const [selectedArtifacts, setSelectedArtifacts] = useState<PublicationMediaReference[]>(draft0.mediaReferences || []);
  const [approvalReference, setApprovalReference] = useState<PublicationApprovalReference | undefined>(draft0.approvalReference);
  const [linkedApproval, setLinkedApproval] = useState<PublicationApprovalRecord | null>(null);
  const [handoffError, setHandoffError] = useState('');
  const [showPicker, setShowPicker] = useState(false);
  const [pub, setPub] = useState<Record<string, PubState>>({});
  const [publishing, setPublishing] = useState(false);
  const [approvals, setApprovals] = useState<PublicationApprovalRecord[]>([]);
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [approvalError, setApprovalError] = useState('');
  const draftKey = JSON.stringify({ title, body, platforms, overrides, tags, persona, selectedMedia, selectedArtifacts, asDraft, aiGenerated });
  const draftKeyRef = useRef(draftKey);
  draftKeyRef.current = draftKey;
  useEffect(() => { setApprovals([]); setApprovalError(''); }, [draftKey]);
  // 发布时的短信验证窗口（抖音风控条件触发；没触发就不弹）
  const [pubSms, setPubSms] = useState<{ platform: string; name: string; state: string; message: string; approvalId: string } | null>(null);
  const [pubSmsCode, setPubSmsCode] = useState('');
  const [pubSmsBusy, setPubSmsBusy] = useState(false);

  // 草稿持久化：任何改动即写 localStorage，切页/刷新回来都在
  useEffect(() => {
    savePublishDraft({ title, body, platforms, overrides, tags, aiGenerated, mediaReferences: selectedArtifacts, approvalReference });
  }, [title, body, platforms, overrides, tags, aiGenerated, selectedArtifacts, approvalReference]);

  useEffect(() => {
    if (!handoff) return;
    const accepted = normalizePublicationHandoff(handoff);
    if (!accepted) { setHandoffError('此发布入口已过期或不属于当前用户 / IP，请回原对话重新选择。'); onHandoffConsumed?.(); return; }
    setHandoffError(''); setApprovals([]); setPub({});
    if (accepted.kind === 'approval') {
      setApprovalReference({ actorId: accepted.actorId, projectId: accepted.projectId,
        approvalId: accepted.approvalId, payloadHash: accepted.payloadHash });
      onHandoffConsumed?.();
      return;
    }
    setApprovalReference(undefined); setLinkedApproval(null);
    setSelectedMedia([]); setSelectedArtifacts(accepted.media);
    setTitle(accepted.copy.title || ''); setBody(accepted.copy.body || ''); setTags(accepted.copy.tags || '');
    setOverrides({}); setAsDraft(false); setAiGenerated(false); setCheckResult('');
    // A media card cannot silently choose either an account or another platform.
    setPlatforms(accepted.copy.platform && PUBLISHABLE.has(accepted.copy.platform) ? [accepted.copy.platform] : []);
    onHandoffConsumed?.();
  }, [handoff, onHandoffConsumed]);

  useEffect(() => {
    if (!approvalReference) return;
    let active = true;
    setLinkedApproval(null); setApprovalBusy(true); setHandoffError('');
    if (!validPublicationApprovalReference(approvalReference)) {
      setHandoffError('此确认不属于当前用户 / IP。'); setApprovalBusy(false); return;
    }
    fetchPublicationApproval(approvalReference.approvalId).then(record => {
      if (!active) return;
      if (!publicationReceiptInScope(record) || record.id !== approvalReference.approvalId
          || record.payloadHash !== approvalReference.payloadHash) throw new Error('确认对象或固定版本已变化，不能复用此入口。');
      setLinkedApproval(record); setApprovals([record]);
    }).catch(error => {
      if (active) setHandoffError(error instanceof Error ? error.message : '无法读取此确认。');
    }).finally(() => { if (active) setApprovalBusy(false); });
    return () => { active = false; };
  }, [approvalReference]);

  const cleanupPublishActivity = useCallback(() => {
    // 卸载：中止流 + 停掉所有发布状态轮询（否则在后台空转最长 15 分钟）
    aliveRef.current = false;
    adaptCtl.current?.abort();
    pollTimers.current.forEach((iv) => clearInterval(iv));
    pollTimers.current.clear();
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    return cleanupPublishActivity;
  }, [cleanupPublishActivity]);

  // 登录态 + 可选媒体列表
  useEffect(() => {
    fetchAccounts().then(setAccounts).catch(() => { /* 忽略 */ });
    fetchOutputs().then((roots) => {
      const files: OutputFile[] = [];
      const walk = (n: OutputFile) => {
        if (n.type === 'file') { if (n.kind === 'image' || n.kind === 'video') files.push(n); return; }
        for (const c of n.children || []) walk(c);
      };
      roots.forEach(walk);
      files.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
      setMediaFiles(files);
    }).catch(() => { /* 忽略 */ });
  }, []);

  const loginOf = (key: string) => accounts.find((a) => a.platform === key)?.loggedIn ?? false;

  const toggle = (k: string) =>
    setPlatforms((prev) => prev.includes(k) ? prev.filter((x) => x !== k) : [...prev, k]);
  const showToast = (m: string) => { setToast(m); setTimeout(() => setToast(''), 2800); };
  const effective = (k: string) => overrides[k] ?? body;
  const empty = !title.trim() && !body.trim();
  const isVideoPath = (p: string) => /\.(mp4|mov|flv|mkv|avi|webm|m4v|wmv|ts|mpe?g)$/i.test(p);
  const toggleMedia = (path: string) =>
    setSelectedMedia((prev) => {
      if (selectedArtifacts.length) { showToast('请先移除对话中的固定媒体版本，再选择内容库媒体'); return prev; }
      if (prev.includes(path)) return prev.filter((x) => x !== path);
      // 通用规则：图片和视频不能同时；视频一次只发一个
      if (isVideoPath(path)) {
        if (prev.length && !prev.every(isVideoPath)) { showToast('图片和视频不能同时发布，请先取消已选图片'); return prev; }
        return [path]; // 视频单选
      }
      if (prev.some(isVideoPath)) { showToast('图片和视频不能同时发布，请先取消已选视频'); return prev; }
      return [...prev, path]; // 图片可多选（图文）
    });

  // A. 智能一稿多改（流式：逐字改写，直接流进每个平台卡片）
  const adapt = () => {
    if (empty || platforms.length === 0 || adapting) return;
    const sel = PLATFORMS.filter((p) => platforms.includes(p.key));
    const prompt =
      `请执行 /skill-content-repurposing：把下面这条内容改编到这些平台：${sel.map((p) => p.label).join('、')}。` +
      `务必参考该 SKILL 的 platform-specs 与改写配方，贴合各平台原生格式、语气与字数。\n` +
      `【硬性要求】输出各平台“可直接复制发布的纯文本正文”，禁止任何 Markdown 语法：不要 **加粗**、# 标题、---、表格、代码块、编号列表符号；` +
      `小红书可用 emoji 和 #话题标签，按平台习惯自然分行即可。\n` +
      `严格只按下面格式输出、每个平台之间用分隔线，不要任何额外说明：\n` +
      sel.map((p) => `===${p.label}===\n<该平台纯文本正文>`).join('\n') +
      `\n\n原始内容：\n标题：${title}\n正文：${body}`;

    setAdapting(true);
    let acc = '';
    const base = { ...overrides };
    adaptCtl.current = streamChat(
      prompt, persona, `adapt-${Date.now()}`,
      (chunk) => {                       // 逐字：实时解析并流进对应平台卡片
        acc += chunk;
        const map = parseSections(acc);
        const next = { ...base };
        for (const [label, text] of Object.entries(map)) {
          const key = LABEL2KEY[label];
          if (key && platforms.includes(key)) next[key] = text;
        }
        setOverrides(next);
      },
      () => {                            // 完成
        const hit = Object.keys(parseSections(acc)).length;
        setAdapting(false);
        showToast(hit ? `已生成 ${hit} 个平台版本` : '未能解析，可重试');
      },
      () => { setAdapting(false); showToast('改写失败，请重试'); },
    );
  };
  const stopAdapt = () => { adaptCtl.current?.abort(); setAdapting(false); };

  const performPrecheck = async () => {
    const prompt =
      `你是社媒发布审核助手。针对下面这条待发内容做两项检查，用简洁中文分点输出：\n` +
      `1. **合规风险**：是否含极限词/医疗功效/敏感或违规表述，列出问题词+替换建议；无则写"未见明显风险"。\n` +
      `2. **标题/钩子**：给标题打 1-10 分，并给 1-2 个更好的备选。\n` +
      `最后一行给「✅可发 / ⚠️建议修改」结论。\n\n待检内容：\n标题：${title}\n正文：${body}`;
    const content = `待发布内容：\n标题：${title}\n正文：${body}`;
    const [general, personaResult] = await Promise.all([
      runAgent(prompt),
      persona ? executeSkill('persona-check', content, persona) : Promise.resolve(null),
    ]);
    return `${general.response}\n\n---\n\n## 人设一致性\n\n${personaResult?.response || '未选择画像，已跳过人设一致性检查。'}`;
  };

  // C. 发布前一键预检
  const check = async () => {
    if (empty) return;
    setChecking(true); setCheckResult('');
    try {
      setCheckResult(await performPrecheck());
    } catch (e) {
      setCheckResult(e instanceof Error ? e.message : '预检失败');
    } finally { setChecking(false); }
  };

  // Review Host-captured immutable subjects before any external dispatch.
  const publishAll = async () => {
    if (approvalReference) return; // Existing AI preparations are never prepared again.
    if (empty || publishing || checking || approvalBusy) return;
    const targets = PLATFORMS.filter(p => platforms.includes(p.key) && PUBLISHABLE.has(p.key));
    if (!targets.length) { showToast('请选择支持发布的平台'); return; }
    const key = draftKeyRef.current;
    setApprovalBusy(true); setApprovalError('');
    const prepared: PublicationApprovalRecord[] = [];
    for (const target of targets) {
      try {
        if (!loginOf(target.key)) throw new Error('未登录，请先在账号页登录并校验');
        if (MEDIA_REQUIRED.has(target.key) && !selectedMedia.length && !selectedArtifacts.length) throw new Error('需附带图片或视频');
        if (VIDEO_ONLY.has(target.key) && !(selectedArtifacts.some(item => item.mime.startsWith('video/'))
            || selectedMedia.some(path => VIDEO_RE.test(path)))) throw new Error('此平台需要一个视频');
        for (const reference of selectedArtifacts) {
          if (!validPublicationMediaReference(reference)) throw new Error('固定媒体版本已过期或不属于当前用户 / IP，请回原对话重新选择。');
          const metadata = await fetchPublicationArtifact(reference.artifactId);
          if (!publicationMediaReferenceMatches(reference, metadata)) throw new Error('固定媒体版本已失效或字节发生变化，请回原对话重新选择。');
        }
        if (!aliveRef.current || draftKeyRef.current !== key) break;
        const record = await preparePublication(target.key, { title, body: effective(target.key), media: selectedMedia, tags, persona, aiGenerated,
          ...(selectedArtifacts.length ? { artifactIds: selectedArtifacts.map(item => item.artifactId) } : {}),
          ...(target.draft && asDraft ? { draft: true } : {}) });
        if (!aliveRef.current || draftKeyRef.current !== key) break;
        prepared.push(record);
      } catch (error) {
        if (aliveRef.current && draftKeyRef.current === key) setPub(previous => ({ ...previous,
          [target.key]: { status: 'fail', msg: error instanceof Error ? error.message : '准备确认失败' } }));
      }
    }
    if (aliveRef.current) {
      if (draftKeyRef.current === key) setApprovals(prepared);
      setApprovalBusy(false);
    }
  };

  const showPublicationResult = (record: PublicationApprovalRecord) => {
    const key = record.subject.platform.id;
    const result = record.result;
    const message = ({
      submitted: '已提交，等待核对平台回执', confirmed: '平台回执已确认', unknown: '执行结果待核实，勿重复提交',
      failed: '执行失败', draft_created: '草稿已创建，尚未公开发布', exported: '已导出，尚未发布', scheduled: '定时任务已安排',
    } as Record<string, string>)[result?.status || ''] || '等待执行结果';
    setPub(previous => ({ ...previous, [key]: { status: result?.status === 'confirmed' ? 'ok'
      : result?.status === 'failed' ? 'fail' : 'pending', msg: message } }));
  };

  const pollApprovedPublication = (record: PublicationApprovalRecord) => new Promise<void>(resolve => {
    const started = Date.now();
    let polling = false;
    const iv = setInterval(async () => {
      if (!aliveRef.current) { clearInterval(iv); pollTimers.current.delete(iv); resolve(); return; }
      if (Date.now() - started > 16 * 60 * 1000) {
        clearInterval(iv); pollTimers.current.delete(iv); setPubSms(null);
        setPub(previous => ({ ...previous, [record.subject.platform.id]: { status: 'pending', msg: '执行结果待核实，请核对平台，勿重复提交' } }));
        resolve(); return;
      }
      if (polling) return;
      polling = true;
      try {
        const current = await fetchPublicationApproval(record.id);
        if (!aliveRef.current) return;
        setApprovals(previous => previous.map(item => item.id === current.id ? current : item));
        setLinkedApproval(previous => previous?.id === current.id ? current : previous);
        if (current.runnerStatus?.state === 'sms_required' || current.runnerStatus?.state === 'verifying') {
          setPubSms({ platform: current.subject.platform.id, name: current.subject.platform.name,
            state: current.runnerStatus.state, message: current.runnerStatus.message, approvalId: current.id });
        }
        if (current.result && current.result.status !== 'executing') {
          clearInterval(iv); pollTimers.current.delete(iv); setPubSms(null); showPublicationResult(current); resolve();
        }
      } catch { /* Keep the operation ID; a polling failure never retries dispatch. */ }
      finally { polling = false; }
    }, 2500);
    pollTimers.current.add(iv);
  });

  const startApproved = async (record: PublicationApprovalRecord) => {
    setPublishing(true);
      const subject = record.subject;
      const started = await publishNow(subject.platform.id, { title: subject.title, body: subject.body,
        tags: subject.tags, persona: subject.persona, aiGenerated: subject.aiGenerated ?? false, media: [], artifactIds: subject.media.map(item => item.artifactId),
        approvalId: record.id, payloadHash: record.payloadHash,
        ...(subject.platform.id === 'toutiao' && subject.mode === 'create_draft' ? { draft: true } : {}) });
      if (!aliveRef.current) return;
      setApprovals(previous => previous.map(item => item.id === record.id ? started : item));
      setLinkedApproval(previous => previous?.id === record.id ? started : previous);
      if (started.result?.status === 'executing') {
        setPub(previous => ({ ...previous, [subject.platform.id]: { status: 'publishing', msg: '正在执行此批准版本…' } }));
        void pollApprovedPublication(started).finally(() => { if (aliveRef.current) setPublishing(false); });
      } else { showPublicationResult(started); setPublishing(false); }
  };

  const decide = async (id: string, hash: string, approved: boolean) => {
    const record = approvals.find(item => item.id === id && item.payloadHash === hash);
    if (!record || approvalBusy) return;
    const key = draftKeyRef.current;
    setApprovalBusy(true); setApprovalError('');
    try {
      const decision = await decidePublication(id, approved, hash);
      if (!aliveRef.current || draftKeyRef.current !== key) return;
      setApprovals(previous => previous.map(item => item.id === id ? decision : item));
      setLinkedApproval(previous => previous?.id === id ? decision : previous);
      if (!approved) return;
      await startApproved(record);
    } catch (error) {
      if (aliveRef.current) { setApprovalError(error instanceof Error ? error.message : '批准或执行未被接受'); setPublishing(false); }
      throw error;
    } finally { if (aliveRef.current) setApprovalBusy(false); }
  };

  const resumeApproved = async (id: string, hash: string) => {
    if (approvalBusy) return;
    const key = draftKeyRef.current;
    setApprovalBusy(true); setApprovalError('');
    try {
      const current = await fetchPublicationApproval(id);
      if (!aliveRef.current || key !== draftKeyRef.current) return;
      if (current.id !== id || current.payloadHash !== hash || current.state !== 'approved') throw new Error('批准状态已变化，请重新核对');
      setApprovals(previous => previous.map(item => item.id === id ? current : item));
      setLinkedApproval(previous => previous?.id === id ? current : previous);
      if (current.operationId) {
        if (current.result?.status === 'executing') {
          setPublishing(true);
          void pollApprovedPublication(current).finally(() => { if (aliveRef.current) setPublishing(false); });
        } else showPublicationResult(current);
      } else await startApproved(current);
    } catch (error) {
      if (aliveRef.current) { setApprovalError(error instanceof Error ? error.message : '未能核对此操作'); setPublishing(false); }
      throw error;
    } finally { if (aliveRef.current) setApprovalBusy(false); }
  };

  const submitPubSms = async () => {
    if (!pubSms) return;
    const code = pubSmsCode.replace(/\D/g, '');
    if (code.length < 4) { showToast('验证码应为 4-6 位数字'); return; }
    setPubSmsBusy(true);
    try {
      await submitPublishSms(pubSms.platform, code, pubSms.approvalId);
      setPubSmsCode('');
      setPubSms((p) => p && ({ ...p, state: 'verifying', message: '正在验证验证码…' }));
    } catch (e) {
      showToast(e instanceof Error ? e.message : '提交失败');
    } finally {
      setPubSmsBusy(false);
    }
  };

  const copyFor = (key: string) => {
    const text = (title ? title + '\n\n' : '') + effective(key);
    navigator.clipboard?.writeText(text);
    setCopied(key); setTimeout(() => setCopied(''), 1400);
  };
  const addToCalendar = async (key: string) => {
    if (empty) return;
    // 用本地日期（日历页按本地渲染）：toISOString 是 UTC，东八区 0-8 点会落到「昨天」
    const d = new Date();
    const date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    await createSchedule({
      title: title.trim() || effective(key).slice(0, 20), date,
      platform: PLATFORMS.find((p) => p.key === key)?.label || '', time: '', status: 'draft', note: effective(key),
    });
    showToast('已存为草稿并加入今天的日历');
  };

  const canPublish = platforms.some((k) => PUBLISHABLE.has(k));
  // 已选平台里支持存草稿的（目前只有今日头条）——没有就不显示开关，避免误导
  const draftCapable = PLATFORMS.filter((p) => platforms.includes(p.key) && p.draft);

  return (
    <div className="publish-page">
      {approvalReference ? <div className="publish-editor">
        <h1 className="page-title"><IconPublish size={21} /> 发布中心</h1>
        <p className="page-subtitle">已载入 AI 对话中的同一次发布确认。账号、文案和媒体版本保持固定。</p>
        {approvalBusy && !linkedApproval && <p role="status">正在读取此固定版本…</p>}
        {handoffError && <p className="publication-approval-error" role="alert">{handoffError}</p>}
        {linkedApproval && <>
          <PublicationSubjectView subject={linkedApproval.subject} />
          <PublicationReceiptCard record={linkedApproval} onRefresh={refreshPublicationApproval} />
          <button className="btn btn-primary" disabled={approvalBusy || publishing}
            onClick={() => setApprovals([linkedApproval])}>核对此固定版本</button>
        </>}
        <button className="btn btn-sm" disabled={approvalBusy || publishing} onClick={() => {
          setApprovalReference(undefined); setLinkedApproval(null); setApprovals([]); setApprovalError(''); setHandoffError('');
        }}>返回编辑草稿</button>
      </div> : <div className="publish-editor">
        <h1 className="page-title"><IconPublish size={21} /> 发布中心</h1>
        <p className="page-subtitle">编辑文案与媒体，核对实际账号和固定版本后提交；发布结果以平台回执为准。</p>
        {handoffError && <p className="publication-approval-error" role="alert">{handoffError}</p>}

        <label className="field-label">标题</label>
        <input className="field" value={title} placeholder="标题（部分平台需要）"
          onChange={(e) => setTitle(e.target.value)} />
        <label className="field-label">正文（母版）</label>
        <textarea className="field" style={{ minHeight: 180 }} value={body}
          placeholder="写下你的内容，右侧按各平台规则实时预览；点「一键适配」让 AI 分平台改写…"
          onChange={(e) => setBody(e.target.value)} />

        <label className="field-label">话题标签 <span style={{ color: 'var(--text-secondary)', fontWeight: 400, fontSize: 12 }}>（逗号分隔，如「AI,职场,干货」；小红书会用 # 联想真正绑定话题）</span></label>
        <input className="field" value={tags} placeholder="AI,职场,干货"
          onChange={(e) => setTags(e.target.value)} />

        <label className="field-label">发布平台</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {PLATFORMS.map((p) => (
            <button key={p.key} className={`chip ${platforms.includes(p.key) ? 'active' : ''}`}
              onClick={() => toggle(p.key)}>{p.label}</button>
          ))}
        </div>
        <label className="draft-toggle">
          <input type="checkbox" aria-label="AI生成内容" checked={aiGenerated} onChange={(e) => setAiGenerated(e.target.checked)} />
          <span>AI生成内容（将作为此版本的发布声明）</span>
        </label>
        {draftCapable.length > 0 && (
          <label className="draft-toggle">
            <input type="checkbox" checked={asDraft} onChange={(e) => setAsDraft(e.target.checked)} />
            <span>存草稿（{draftCapable.map((p) => p.label).join('、')} 进平台草稿箱，不公开发布；同批次的其它平台仍会真发布）</span>
          </label>
        )}

        <label className="field-label" style={{ marginTop: 14 }}>
          媒体附件 {selectedMedia.length + selectedArtifacts.length > 0 && <span className="pv-badge">{selectedMedia.length + selectedArtifacts.length} 个</span>}
          <span style={{ color: 'var(--text-secondary)', fontWeight: 400, fontSize: 12 }}>（小红书/抖音/快手/微信视频号/B站必需，从内容库选；抖音、视频号、B站须为视频；今日头条选中的图会插进正文）</span>
        </label>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn btn-sm" onClick={() => setShowPicker((v) => !v)}>
            <IconSkills size={13} /> {showPicker ? '收起' : '选择媒体'}
          </button>
          {selectedArtifacts.map(reference => <div key={reference.artifactId} className="media-chip"
            style={{ width: 'auto', padding: 8, gap: 8 }} title={`固定版本 V${reference.revision} · SHA-256 ${reference.sha256}`}>
            <span>{reference.name} · V{reference.revision}</span>
            <a href={reference.url} target="_blank" rel="noopener noreferrer">预览</a>
            <button type="button" className="btn btn-sm btn-ghost" aria-label={`移除 ${reference.name}`}
              onClick={() => setSelectedArtifacts(previous => previous.filter(item => item.artifactId !== reference.artifactId))}>×</button>
          </div>)}
          {selectedMedia.map((path) => (
            <div key={path} className="media-chip" onClick={() => toggleMedia(path)} title="点击移除">
              {mediaFiles.find((f) => f.path === path)?.kind === 'image'
                ? <img src={mediaUrl(path)} alt="" /> : <span className="media-vid">🎬</span>}
              <span className="media-x">×</span>
            </div>
          ))}
        </div>
        {showPicker && (
          <div className="media-grid">
            {mediaFiles.length === 0 && <div className="dash-empty">内容库暂无图片/视频</div>}
            {mediaFiles.slice(0, 40).map((f) => (
              <div key={f.path}
                className={`media-cell ${selectedMedia.includes(f.path) ? 'sel' : ''}`}
                onClick={() => toggleMedia(f.path)} title={f.path}>
                {f.kind === 'image'
                  ? <img src={mediaUrl(f.path)} alt={f.name} loading="lazy" />
                  : <span className="media-vid">🎬<br />{f.name.slice(0, 12)}</span>}
                {selectedMedia.includes(f.path) && <span className="media-check">✓</span>}
              </div>
            ))}
          </div>
        )}

        <div className="publish-actions">
          {adapting ? (
            <button className="btn btn-sm" onClick={stopAdapt}><IconStop size={13} /> 停止生成</button>
          ) : (
            <button className="btn btn-sm btn-primary" disabled={empty || platforms.length === 0} onClick={adapt}>
              <IconSkills size={14} /> 一键适配各平台
            </button>
          )}
          <button className="btn btn-sm" disabled={empty || checking || adapting} onClick={check}>
            <IconCheck size={14} /> {checking ? '预检中…' : '发布前预检'}
          </button>
          <button className="btn btn-sm" disabled={empty} onClick={() => addToCalendar(platforms[0] || 'xiaohongshu')}>
            <IconCalendar size={14} /> 存草稿并排期
          </button>
          <button className="btn btn-sm btn-primary" disabled={empty || publishing || checking || approvalBusy || !canPublish}
            title={canPublish ? '真实发布到已登录平台' : '所选平台无一键发布（B站走终端 biliup）'}
            onClick={publishAll}>
            <IconPublish size={14} /> {publishing ? '执行中…' : approvalBusy ? '正在准备确认…' : '核对并发布'}
          </button>
          <button className="btn btn-sm btn-ghost" disabled={empty || adapting}
            onClick={() => { setTitle(''); setBody(''); setTags(''); setOverrides({}); setCheckResult(''); setPub({}); showToast('已清空'); }}>
            <IconTrash size={13} /> 清空
          </button>
        </div>
        {adapting && <div className="adapt-hint"><span className="live-pulse" />AI 正在逐字改写各平台版本…可随时停止。</div>}
        <p className="publish-saved-note">草稿已自动保存，切换页面/刷新回来内容都在。一键发布仅对「已登录 + 媒体齐全」的平台生效。</p>
        {checkResult && (
          <div className="panel" style={{ marginTop: 14 }}>
            <div className="panel-title"><IconCheck size={14} /> 发布前预检</div>
            <div className="skill-body-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(checkResult) }} />
          </div>
        )}
      </div>}

      {!approvalReference && <div className="publish-previews">
        {platforms.length === 0 && <div className="dash-empty">选择至少一个平台查看预览</div>}
        {PLATFORMS.filter((p) => platforms.includes(p.key)).map((p) => {
          const text = effective(p.key);
          const over = text.length > p.bodyLimit;
          const titleOver = p.titleLimit != null && title.length > p.titleLimit;
          const isEdit = editing === p.key;
          const ps = pub[p.key];
          const publishable = PUBLISHABLE.has(p.key);
          const logged = loginOf(p.key);
          return (
            <div key={p.key} className={`card pv-card pv-${p.key}`}>
              <div className="pv-head">
                <span className="pv-plat">
                  {p.label}
                  {overrides[p.key] != null && <span className="pv-badge">AI 版</span>}
                  {publishable && (logged
                    ? <span className="pv-badge pv-badge-ok">已登录</span>
                    : <span className="pv-badge">未登录</span>)}
                </span>
                <span className={`pv-count ${over ? 'over' : ''}`}>{text.length}/{p.bodyLimit}</span>
              </div>
              <div className="pv-body">
                {p.titleLimit != null && (
                  <div className={`pv-title ${titleOver ? 'over' : ''}`}>{title || <span className="pv-ph">标题…</span>}</div>
                )}
                {isEdit
                  ? <textarea className="field" style={{ minHeight: 120 }} value={text} autoFocus
                      onChange={(e) => setOverrides((o) => ({ ...o, [p.key]: e.target.value }))} />
                  : <div className="pv-text">{text || <span className="pv-ph">正文预览…</span>}{adapting && overrides[p.key] != null && <span className="streaming-cursor" />}</div>}
              </div>
              {ps && (
                <div className={`pv-pubstate ${ps.status}`}>
                  {ps.status === 'publishing' && <span className="live-pulse" />}
                  {ps.status === 'ok' ? '✅ ' : ps.status === 'fail' ? '⚠️ ' : ''}{ps.msg}
                </div>
              )}
              <div className="pv-foot">
                <span className="pv-hint">{p.hint}{over ? ' · 已超字数' : ''}</span>
                <div style={{ display: 'flex', gap: 6 }}>
                  <button className="pv-copy" onClick={() => setEditing(isEdit ? null : p.key)}>
                    <IconEdit size={13} />{isEdit ? '完成' : '编辑'}
                  </button>
                  <button className="pv-copy" onClick={() => copyFor(p.key)}>
                    {copied === p.key ? <IconCheck size={13} /> : <IconCopy size={13} />}{copied === p.key ? '已复制' : '复制'}
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>}

      {toast && <div className="toast ok"><span className="toast-icon">✓</span>{toast}</div>}

      {approvals.length > 0 && !pubSms && <PublicationApprovalDialog approvals={approvals} busy={approvalBusy} error={approvalError}
        onApprove={(id, hash) => decide(id, hash, true)} onReject={(id, hash) => decide(id, hash, false)}
        onResume={resumeApproved} onClose={() => setApprovals([])} />}

      {pubSms && (
        <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) setPubSms(null); }}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 380 }}>
            <h3 style={{ margin: '0 0 4px' }}>发布验证 · {pubSms.name}</h3>
            <p style={{ fontSize: 13, color: /错误|过期|失败|重新|未完成|不正确|失效/.test(pubSms.message || '') ? 'var(--red)' : 'var(--text-secondary)' }}>
              {pubSms.message || '平台风控要求短信验证，验证码已发到你手机，请输入：'}
            </p>
            {pubSms.state === 'verifying' ? (
              <div className="dash-empty" style={{ padding: 16 }}>正在验证验证码…</div>
            ) : (
              <>
                <input inputMode="numeric" autoFocus
                  placeholder="请输入手机收到的验证码" value={pubSmsCode}
                  onChange={(e) => setPubSmsCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                  onKeyDown={(e) => { if (e.key === 'Enter') submitPubSms(); }}
                  style={{ width: '100%', boxSizing: 'border-box', textAlign: 'center',
                    letterSpacing: 6, fontSize: 20, padding: '10px 12px', margin: '4px 0 10px',
                    border: '1px solid var(--border)', borderRadius: 8 }} />
                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                  <button className="btn btn-sm btn-ghost" onClick={() => setPubSms(null)}>关闭</button>
                  <button className="btn btn-sm btn-primary" disabled={pubSmsBusy} onClick={submitPubSms}>
                    {pubSmsBusy ? '提交中…' : '提交验证码'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
