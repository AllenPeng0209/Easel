import { useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import coreVisual from '../assets/chirpiva-v3/creation-core-web.webp';
import storyboardVisual from '../assets/chirpiva-v3/city-storyboard-web.webp';
import '../styles/landing.css';

type AuthMode = 'login' | 'register';
type Preview = 'idea' | 'create' | 'publish';
type IconName = 'spark' | 'layers' | 'film' | 'calendar' | 'team' | 'arrow' | 'check' | 'lock' | 'plus';
const PATHS: Record<IconName, string> = {
  spark: 'm12 3 2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2L12 3ZM20 2v4m-2-2h4',
  layers: 'm12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5',
  film: 'M3 5h18v14H3Zm4 0v14M17 5v14M3 9h4m-4 6h4m10-6h4m-4 6h4',
  calendar: 'M5 5h14a2 2 0 0 1 2 2v13H3V7a2 2 0 0 1 2-2Zm2-3v6m10-6v6M3 11h18m-14 4h2m4 0h2',
  team: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm8-7a4 4 0 0 1 0 8',
  arrow: 'M4 12h15m-6-6 6 6-6 6', check: 'm5 12 4 4L19 6',
  lock: 'M6 10h12v11H6Zm3 0V6a3 3 0 0 1 6 0v4', plus: 'M12 5v14M5 12h14',
};
const PUBLIC_BASE = window.location.pathname.replace(/\/index\.html$/, '').replace(/\/$/, '').replace(/\/(?:login|register)$/, '');

function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={PATHS[name]} /></svg>;
}

function AuthLink({ mode, className, children }: { mode: AuthMode; className?: string; children: ReactNode }) {
  return <a href={`${PUBLIC_BASE}/${mode}/`} className={className} data-auth-mode={mode}>{children}</a>;
}

function Wordmark() {
  return <a className="cs-brand" href="#top" aria-label="Chirpiva 响芽首页"><span className="cs-brand-mark" aria-hidden="true">c</span><strong>Chirpiva</strong><span className="cs-brand-cn">响芽</span></a>;
}

const PREVIEWS: { id: Preview; label: string; icon: IconName }[] = [
  { id: 'idea', label: '灵感策划', icon: 'spark' }, { id: 'create', label: '内容创作', icon: 'film' }, { id: 'publish', label: '发布排期', icon: 'calendar' },
];

function ProductStudio() {
  const [selected, setSelected] = useState<Preview>('create');
  const keyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const next = event.key === 'ArrowRight' ? (index + 1) % 3 : event.key === 'ArrowLeft' ? (index + 2) % 3 : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : -1;
    if (next < 0) return;
    event.preventDefault(); setSelected(PREVIEWS[next].id);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('button')[next]?.focus();
  };
  return <div className="cs-studio" aria-label="Chirpiva 创作工作台交互演示">
    <div className="cs-studio-chrome"><div aria-hidden="true"><i /><i /><i /></div><span>Chirpiva / 城市漫游指南</span><small>交互演示</small></div>
    <div className="cs-studio-body"><aside className="cs-studio-sidebar" aria-hidden="true"><div className="cs-studio-project"><span>城</span><strong>城市漫游指南</strong><small>Owner</small></div><span className="active"><Icon name="spark" size={14} />内容创作</span><span><Icon name="film" size={14} />视频工坊</span><span><Icon name="calendar" size={14} />发布中心</span><span><Icon name="team" size={14} />团队成员</span><div className="cs-studio-account"><span>你</span><small>你的创作空间</small></div></aside>
      <div className="cs-studio-main"><div className="cs-studio-heading"><div><span className="cs-project-dot" /><strong>创作工作台</strong></div><span><Icon name="lock" size={10} />私有项目</span></div>
        <div className="cs-studio-tabs" role="tablist" aria-label="体验创作流程">{PREVIEWS.map((item, index) => <button key={item.id} type="button" role="tab" id={`cs-tab-${item.id}`} data-preview-tab={item.id} tabIndex={selected === item.id ? 0 : -1} aria-selected={selected === item.id} aria-controls={`cs-panel-${item.id}`} onClick={() => setSelected(item.id)} onKeyDown={event => keyDown(event, index)}><Icon name={item.icon} size={13} />{item.label}</button>)}</div>
        <div className="cs-studio-panel" role="tabpanel" id={`cs-panel-${selected}`} aria-labelledby={`cs-tab-${selected}`} data-preview-panel={selected}>
          {selected === 'idea' ? <><div className="cs-prompt"><span className="cs-small-spark">✳</span><p>把一次周末城市漫游，<br />变成一组有温度的内容。</p></div><div className="cs-idea"><span className="cs-result-label">灵感方向</span><h3>不赶路，去感受一座城。</h3><p>从老街的树影，到雨后亮起的灯。<br />用一个周末，找到生活里的新视角。</p><div><span>城市生活</span><span>周末灵感</span><span>慢节奏叙事</span></div></div></>
            : selected === 'create' ? <><div className="cs-creation-title"><div><span className="cs-result-label">创作示例 / 城市生活</span><h3>把周末，交还给自己。</h3></div><span className="cs-draft-badge">脚本与分镜</span></div><img className="cs-storyboard" src={storyboardVisual} alt="城市漫步、窗边咖啡和傍晚江景组成的三镜头故事示例" loading="lazy" /><div className="cs-shot-labels"><span>01 <b>走进城市</b></span><span>02 <b>感受片刻</b></span><span>03 <b>看见远方</b></span></div><div className="cs-video-tracks" aria-hidden="true"><i /><i /><i /></div><div className="cs-studio-caption"><span><Icon name="check" size={10} />灵感、脚本与分镜，在一个项目内衔接。</span><small>内容示例</small></div></>
              : <><div className="cs-creation-title"><div><span className="cs-result-label">内容日历</span><h3>让好内容，按自己的节奏发生。</h3></div></div><div className="cs-schedule-row"><div><small>周五</small><strong>07</strong></div><section><small>图文草稿 · 待确认</small><h4>不赶路，去感受一座城。</h4><p><i className="cs-channel red" />小红书<span>18:00</span></p></section></div><div className="cs-schedule-row"><div><small>周六</small><strong>08</strong></div><section><small>短视频 · 计划发布</small><h4>把周末，交还给自己。</h4><p><i className="cs-channel cyan" />抖音<span>10:00</span></p></section></div></>}
        </div>
      </div>
    </div>
  </div>;
}

export default function LandingPage() {
  return <div className="chirpiva-landing cs-site" data-page-type="software">
    <a className="cs-skip" href="#main">跳到主要内容</a>
    <header className="cs-header" id="top"><div className="cs-shell cs-header-inner"><Wordmark /><nav className="cs-nav" aria-label="首页导航"><a href="#product">产品</a><a href="#workflow">创作流程</a><a href="#collaboration">团队协作</a></nav><div className="cs-header-actions lp-header-actions"><AuthLink mode="login" className="cs-login">登录</AuthLink><AuthLink mode="register" className="cs-register">注册</AuthLink></div></div></header>
    <main id="main">
      <section className="cs-hero lp-hero" aria-labelledby="cs-hero-title"><div className="cs-hero-copy"><span className="cs-category"><span />AI 创作与运营工作空间</span><h1 id="cs-hero-title">创作的下一种可能。</h1><p>在一个 IP 里，连接灵感、图文、视频与多平台运营。<br />把重复的工作交给 AI，把创造力留给你。</p><div className="cs-hero-actions"><AuthLink mode="register" className="cs-button cs-button-primary"><Icon name="plus" size={16} />创建我的 IP</AuthLink><a className="cs-button cs-button-secondary lp-how-link" href="#product">探索创作系统<Icon name="arrow" size={16} /></a></div></div>
        <div className="cs-core-stage" aria-label="光学玻璃与钛金属构成的 Chirpiva 创作中枢"><div className="cs-core-aura" /><img className="cs-core-image" src={coreVisual} alt="悬浮在暗色空间中的玻璃与钛金属 C 形雕塑" fetchPriority="high" /><div className="cs-core-orbit orbit-left"><span /><span>灵感</span></div><div className="cs-core-orbit orbit-right"><span /><span>作品</span></div><div className="cs-core-origin"><span /><i /><span /></div></div>
        <div className="cs-hero-foot"><span>一个 IP，一套完整的创作系统。</span><div><span>图文</span><i /><span>视频</span><i /><span>发布</span><i /><span>运营</span></div></div>
      </section>
      <section className="cs-shell cs-product-section" id="product" aria-labelledby="cs-product-title"><div className="cs-section-heading"><h2 id="cs-product-title">从灵感到作品，<br />让创作连续发生。</h2><p>一个想法不必辗转多个工具。<br />在同一个工作空间，完成策划、创作和内容准备。</p></div><div className="cs-product-layout"><ProductStudio /><aside className="cs-feature-notes"><article><span><Icon name="spark" size={20} /></span><div><h3>让想法，更快有形状。</h3><p>选题、文案与脚本，和 AI 一起完成第一步，再打磨成你的表达。</p></div></article><article><span><Icon name="film" size={20} /></span><div><h3>让故事，真正动起来。</h3><p>连接脚本、分镜和视频制作，让每个创意成为看得见的作品。</p></div></article><article><span><Icon name="calendar" size={20} /></span><div><h3>让运营，有自己的节奏。</h3><p>管理平台账号、内容排期与运营记录，把工作收进同一处。</p></div></article></aside></div></section>
      <section className="cs-workflow-section" id="workflow" aria-labelledby="cs-workflow-title"><div className="cs-shell"><div className="cs-workflow-heading"><h2 id="cs-workflow-title">一条工作流，<br />连接创作的每个环节。</h2><p>内容、账号和协作，围绕同一个 IP 运转。</p></div><ol className="cs-flow"><li><span className="cs-flow-icon"><Icon name="layers" size={25} /></span><h3>建立 IP</h3><p>确定品牌定位，<br />建立独立的创作空间。</p></li><li><span className="cs-flow-icon"><Icon name="spark" size={25} /></span><h3>AI 创作</h3><p>从选题到文案，<br />把灵感整理成内容。</p></li><li><span className="cs-flow-icon"><Icon name="film" size={25} /></span><h3>内容制作</h3><p>完善脚本与分镜，<br />准备图文和视频作品。</p></li><li><span className="cs-flow-icon"><Icon name="calendar" size={25} /></span><h3>发布与运营</h3><p>连接平台、安排排期，<br />在复盘中找到新方向。</p></li></ol><div className="cs-platform-line"><span>为多平台内容而设计</span><div><b>小红书</b><b>抖音</b><b>公众号</b><b>bilibili</b><b>YouTube</b><b>𝕏</b></div></div></div></section>
      <section className="cs-shell cs-collaboration" id="collaboration" aria-labelledby="cs-collaboration-title"><div className="cs-collaboration-copy"><span className="cs-private-label"><Icon name="lock" size={14} />你的项目，由你拥有。</span><h2 id="cs-collaboration-title">独立的创作空间。<br />清晰的协作边界。</h2><p>每个 IP 都是独立的私有项目。<br />由你决定谁能运营、谁能查看，让内容与账号<br className="cs-desktop-break" />在清晰的权限里安全协作。</p><AuthLink mode="register" className="cs-text-link">建立你的创作空间<Icon name="arrow" size={16} /></AuthLink></div><div className="cs-access-panel"><div className="cs-access-heading"><span className="cs-project-glyph"><Icon name="layers" size={21} /></span><div><strong>城市漫游指南</strong><small>成员与权限</small></div><span><Icon name="lock" size={11} />私有</span></div><div className="cs-access-member"><span className="cs-avatar owner">你</span><div><strong>项目所有者</strong><small>拥有项目，管理账号与成员</small></div><span className="cs-role owner">Owner</span></div><div className="cs-access-member"><span className="cs-avatar operator">创</span><div><strong>创作与运营伙伴</strong><small>参与内容创作和日常运营</small></div><span className="cs-role">Operator</span></div><div className="cs-access-member"><span className="cs-avatar viewer">看</span><div><strong>项目查看者</strong><small>查看内容与运营记录</small></div><span className="cs-role">Viewer</span></div><div className="cs-access-foot"><span><Icon name="check" size={12} />权限按项目独立生效</span><small>角色示意</small></div></div></section>
      <section className="cs-closing" id="get-started" aria-labelledby="cs-closing-title"><div className="cs-closing-beam" /><div className="cs-shell"><span className="cs-closing-brand">Chirpiva</span><h2 id="cs-closing-title">把下一个作品，<br className="cs-mobile-break" />留给想象力。</h2><p>从你的第一个 IP 项目开始。</p><AuthLink mode="register" className="cs-button cs-button-primary"><Icon name="plus" size={16} />创建我的 IP</AuthLink></div></section>
    </main>
    <footer className="cs-footer"><div className="cs-shell cs-footer-inner"><Wordmark /><span>属于创作者的 AI 内容工作空间</span><small>© {new Date().getFullYear()} Chirpiva 响芽</small><a href="#top">回到顶部 ↑</a></div></footer>
  </div>;
}
