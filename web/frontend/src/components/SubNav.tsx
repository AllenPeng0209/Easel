import { useIpPermissions } from './ProjectSpace';
import type { Page } from './Sidebar';
import type { ComponentType } from 'react';
import { IconChat, IconDashboard, IconFire, IconIdea, IconSkills } from './icons';

interface SubNavProps {
  current: Page;
  onNavigate: (page: Page) => void;
}

const TOOLS: { page: Page; Icon: ComponentType<{ size?: number }>; label: string }[] = [
  { page: 'trends', Icon: IconFire, label: '热点雷达' },
  { page: 'ideas', Icon: IconIdea, label: '选题库' },
  { page: 'breakdown', Icon: IconSkills, label: '爆款拆解' },
];

export default function SubNav({ current, onNavigate }: SubNavProps) {
  const { canOperate } = useIpPermissions();
  return (
    <nav className="subnav" aria-label="选题策划">
      <button className="subnav-back" onClick={() => onNavigate(canOperate ? 'chat' : 'dashboard')} title={canOperate ? '返回 AI 对话' : '返回运营总览'}>
        {canOperate ? <IconChat size={15} /> : <IconDashboard size={15} />}<span>{canOperate ? 'AI 对话' : '运营总览'}</span>
      </button>
      <span className="subnav-div" />
      <div className="subnav-tabs">
        {TOOLS.filter(item => canOperate || item.page !== 'breakdown').map(({ page, Icon, label }) => (
          <button key={page} className={`subnav-tab ${current === page ? 'active' : ''}`}
            onClick={() => onNavigate(page)}>
            <Icon size={14} />{label}
          </button>
        ))}
      </div>
    </nav>
  );
}
