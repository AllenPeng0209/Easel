import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { addMember, changeMemberRole, fetchMembers, fetchUserDirectory, removeMember } from '../lib/access';
import type { CollaboratorRole, DirectoryUser, Member, Role } from '../lib/access';
import { useAccessDialog, useAuth } from './AuthGate';
import RepositorySettings from './RepositorySettings';
import RepositoryActivity from './RepositoryActivity';
import '../styles/access.css';

const ROLES: { value: Role; label: string; detail: string }[] = [
  { value: 'owner', label: '所有者', detail: '项目的唯一 Owner，管理成员、账号连接与项目设置' },
  { value: 'operator', label: '运营者', detail: '创作、对话、内容排期与发布，处理日常运营' },
  { value: 'viewer', label: '查看者', detail: '只读浏览 IP 内容与运营记录' },
];
const COLLABORATORS = ROLES.filter(role => role.value !== 'owner');
const CAPABILITIES = [
  ['查看项目内容、排期与运营记录', true, true, true],
  ['AI 对话、创作与上传内容', true, true, false],
  ['内容排期、受控发布与互动处理', true, true, false],
  ['连接、退出与配置平台账号', true, false, false],
  ['修改项目、管理成员与查看权限记录', true, false, false],
  ['转移项目所有权', true, false, false],
] as const;

export default function AccessMembersPanel({ projectId, projectName, onClose, onChanged }: {
  projectId: string;
  projectName: string;
  onClose: () => void;
  role?: Role;
  onChanged?: () => void;
}) {
  const auth = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [directory, setDirectory] = useState<DirectoryUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedUser, setSelectedUser] = useState('');
  const [selectedRole, setSelectedRole] = useState<Role>('operator');
  const [tab, setTab] = useState<'members' | 'permissions' | 'activity' | 'settings'>('members');
  const [title, setTitle] = useState(projectName);
  const [removeFor, setRemoveFor] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const version = useRef(0);
  const dialog = useRef<HTMLDivElement>(null);
  const userId = auth?.user.id;
  const currentRole = members.find(member => member.user_id === userId)?.role;
  const canManage = !loading && currentRole === 'owner';

  const load = useCallback(async (expected: number) => {
    const next = await fetchMembers(projectId);
    if (version.current !== expected) return;
    setMembers(next);
    if (next.find(member => member.user_id === userId)?.role === 'owner') {
      const people = await fetchUserDirectory();
      if (version.current === expected) setDirectory(people);
    } else setDirectory([]);
  }, [projectId, userId]);

  useEffect(() => {
    const expected = ++version.current;
    setMembers([]); setDirectory([]); setLoading(true); setError(''); setNotice(''); setSelectedUser('');
    void load(expected).catch(error => { if (version.current === expected) setError(error instanceof Error ? error.message : '成员列表未加载，请重试'); })
      .finally(() => { if (version.current === expected) setLoading(false); });
    return () => { version.current += 1; };
  }, [load]);
  useEffect(() => {
    const update = () => { void load(version.current).catch(() => { /* mutation endpoints still recheck current roles */ }); };
    window.addEventListener('focus', update);
    const timer = setInterval(update, 15_000);
    return () => { window.removeEventListener('focus', update); clearInterval(timer); };
  }, [load]);
  useAccessDialog(dialog, onClose, !!busy);
  if (!auth) return null;

  const mutate = async (key: string, operation: () => Promise<unknown>, success: string) => {
    if (busy || !canManage) return;
    const expected = version.current;
    setBusy(key); setError(''); setNotice('');
    try {
      await operation();
      if (version.current !== expected) return;
      onChanged?.();
      await load(expected);
      if (version.current === expected) { setSelectedUser(''); setRemoveFor(''); setNotice(success); }
    } catch (error) { if (version.current === expected) setError(error instanceof Error ? error.message : '成员操作未完成，请重试'); }
    finally { if (version.current === expected) setBusy(''); }
  };
  const add = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedUser) return;
    await mutate('add', () => addMember(projectId, { user_id: selectedUser, role: selectedRole as CollaboratorRole }), '成员已添加，可立即访问此项目');
  };
  const available = directory.filter(user => !members.some(member => member.user_id === user.id));

  return createPortal(<div className="overlay access-overlay" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div ref={dialog} className="modal access-members-modal" role="dialog" aria-modal="true" aria-labelledby="access-members-title">
      <div className="access-dialog-heading"><div><span className="access-eyebrow">PROJECT / ACCESS & SETTINGS</span><h2 id="access-members-title">{title}</h2></div>
        <button autoFocus className="access-close" aria-label="关闭成员管理" disabled={!!busy} onClick={onClose}>×</button></div>
      <p className="access-intro"><span className="repo-private">私有项目</span> · 项目由 Owner 拥有，每位协作者只拥有这个项目内的权限。</p>
      <div className="repository-tabs" aria-label="项目管理导航">
        <button aria-pressed={tab === 'members'} disabled={!!busy} onClick={() => setTab('members')}>成员与权限 <span>{members.length}</span></button>
        <button aria-pressed={tab === 'permissions'} disabled={!!busy} onClick={() => setTab('permissions')}>权限说明</button>
        {canManage && <button aria-pressed={tab === 'activity'} disabled={!!busy} onClick={() => setTab('activity')}>权限记录</button>}
        <button aria-pressed={tab === 'settings'} disabled={!!busy} onClick={() => setTab('settings')}>{canManage ? '项目设置' : '项目信息'}</button>
      </div>
      {error && <p className="access-error" role="alert">{error}</p>}
      {notice && <p className="access-notice" role="status">{notice}</p>}
      {loading ? <p className="access-empty">正在加载成员…</p> : tab === 'members' ? <>
        <div className="repository-members-heading"><h3>项目成员</h3><p>Owner 唯一；协作者可以是 Operator 或 Viewer。</p></div>
        <div className="access-members-list" aria-label="IP 成员列表">
          {members.map(member => <div key={member.user_id} className="access-member-row">
            <span className="access-avatar">{(member.display_name || member.username).slice(0, 1)}</span>
            <div className="access-user-copy"><strong>{member.display_name || member.username}{member.user_id === userId && <span className="access-self">你</span>}</strong><small>@{member.username}{!member.active ? ' · 已停用' : ''}</small></div>
            {canManage && member.role !== 'owner' ? <select aria-label={`${member.display_name || member.username} 的角色`} disabled={!!busy || !member.active}
              value={member.role} onChange={event => { const role = event.target.value as CollaboratorRole; void mutate(`role:${member.user_id}`, () => changeMemberRole(projectId, member.user_id, role), '成员角色已更新，后续请求立即按新权限执行'); }}>
              {COLLABORATORS.map(role => <option key={role.value} value={role.value}>{role.label} · {role.value}</option>)}</select>
              : <span className={`access-role-badge role-${member.role}`}>{ROLES.find(role => role.value === member.role)?.label} · {member.role}</span>}
            {canManage && member.role !== 'owner' && (removeFor === member.user_id ? <div className="repository-remove-confirm"><button className="project-text-button" disabled={!!busy} onClick={() => setRemoveFor('')}>取消</button><button className="access-remove" disabled={!!busy}
              onClick={() => { void mutate(`remove:${member.user_id}`, () => removeMember(projectId, member.user_id), '成员已移除，项目访问权限已撤销'); }}>确认移除</button></div> : <button className="access-remove" data-action="remove-member" disabled={!!busy} onClick={() => setRemoveFor(member.user_id)}>移除</button>)}
          </div>)}
          {!members.length && <p className="access-empty">当前没有可显示的成员</p>}
        </div>
        {canManage ? <form className="access-member-add" onSubmit={add}>
          <h3>添加协作者</h3>
          <div className="access-member-add-fields">
            <label><span>选择用户</span><select name="user_id" aria-label="选择用户" value={selectedUser} required disabled={!!busy}
              onChange={event => setSelectedUser(event.target.value)}><option value="">选择已有的平台用户</option>
              {available.map(user => <option key={user.id} value={user.id}>{user.display_name || user.username}（{user.username}）</option>)}</select></label>
            <label><span>IP 角色</span><select name="role" aria-label="新成员角色" value={selectedRole} disabled={!!busy} onChange={event => setSelectedRole(event.target.value as Role)}>
              {COLLABORATORS.map(role => <option key={role.value} value={role.value}>{role.label} · {role.value}</option>)}</select></label>
            <button className="btn btn-primary" disabled={!!busy || !selectedUser}>{busy === 'add' ? '添加中…' : '添加成员'}</button>
          </div>
          <p className="access-field-help">对方先注册 Chirpiva，再按用户名选择并添加。所有权交接请到「项目设置」，不能通过成员角色直接更换 Owner。</p>
        </form> : <p className="access-field-help access-readonly-note">你可以查看成员。成员邀请、移除与角色调整由这个 IP 的 owner 管理。</p>}
      </> : tab === 'permissions' ? <section><div className="access-role-guide">{ROLES.map(role => <div key={role.value}><strong>{role.label}<code>{role.value}</code></strong><p>{role.detail}</p></div>)}</div>
        <div className="repository-permissions-scroll"><table className="repository-permissions"><caption>项目角色与权限</caption><thead><tr><th scope="col">项目能力</th><th scope="col">Owner</th><th scope="col">Operator</th><th scope="col">Viewer</th></tr></thead>
          <tbody>{CAPABILITIES.map(([label, ...roles]) => <tr key={label}><th scope="row">{label}</th>{roles.map((allowed, index) => <td key={index} aria-label={allowed ? '允许' : '不允许'}>{allowed ? '✓' : '—'}</td>)}</tr>)}</tbody></table></div>
        <p className="access-field-help">平台管理员负责平台账号、模型和系统配置，身份本身不授予任何其他项目的内容权限。个人 AI 对话按用户独立保存。</p>
      </section> : tab === 'activity' && canManage ? <RepositoryActivity projectId={projectId} /> : tab === 'settings' ? <RepositorySettings projectId={projectId} canManage={canManage} members={members}
        onChanged={name => { if (name) setTitle(name); onChanged?.(); }} onTransferred={async () => { onChanged?.(); await load(version.current); setTab('members'); setNotice('所有权已转移，你现在是此项目的 Operator。'); }} /> : <p className="access-field-help">权限已变化，请选择成员或项目信息查看当前访问权限。</p>}
    </div>
  </div>, document.body);
}
