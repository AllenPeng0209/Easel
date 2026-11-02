import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { addMember, changeMemberRole, fetchMembers, fetchUserDirectory, removeMember } from '../lib/access';
import type { DirectoryUser, Member, Role } from '../lib/access';
import { useAccessDialog, useAuth } from './AuthGate';
import '../styles/access.css';

const ROLES: { value: Role; label: string; detail: string }[] = [
  { value: 'owner', label: '负责人', detail: '管理 IP、成员与平台账号，执行全部运营操作' },
  { value: 'operator', label: '运营者', detail: '创作、对话、内容排期与发布，处理日常运营' },
  { value: 'viewer', label: '查看者', detail: '只读浏览 IP 内容与运营记录' },
];

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
      if (version.current === expected) { setSelectedUser(''); setNotice(success); }
    } catch (error) { if (version.current === expected) setError(error instanceof Error ? error.message : '成员操作未完成，请重试'); }
    finally { if (version.current === expected) setBusy(''); }
  };
  const add = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedUser) return;
    await mutate('add', () => addMember(projectId, { user_id: selectedUser, role: selectedRole }), '成员已添加');
  };
  const available = directory.filter(user => !members.some(member => member.user_id === user.id));

  return createPortal(<div className="overlay access-overlay" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <div ref={dialog} className="modal access-members-modal" role="dialog" aria-modal="true" aria-labelledby="access-members-title">
      <div className="access-dialog-heading"><div><span className="access-eyebrow">IP ACCESS</span><h2 id="access-members-title">成员与权限</h2></div>
        <button autoFocus className="access-close" aria-label="关闭成员管理" disabled={!!busy} onClick={onClose}>×</button></div>
      <p className="access-intro"><strong>{projectName}</strong> · 每位成员在这个 IP 的权限独立生效。</p>
      <div className="access-role-guide">{ROLES.map(role => <div key={role.value}><strong>{role.label}<code>{role.value}</code></strong><p>{role.detail}</p></div>)}</div>
      {error && <p className="access-error" role="alert">{error}</p>}
      {notice && <p className="access-notice" role="status">{notice}</p>}
      {loading ? <p className="access-empty">正在加载成员…</p> : <>
        <div className="access-members-list" aria-label="IP 成员列表">
          {members.map(member => <div key={member.user_id} className="access-member-row">
            <span className="access-avatar">{(member.display_name || member.username).slice(0, 1)}</span>
            <div className="access-user-copy"><strong>{member.display_name || member.username}{member.user_id === userId && <span className="access-self">你</span>}</strong><small>@{member.username}{!member.active ? ' · 已停用' : ''}</small></div>
            {canManage ? <select aria-label={`${member.display_name || member.username} 的角色`} disabled={!!busy}
              value={member.role} onChange={event => { const role = event.target.value as Role; void mutate(`role:${member.user_id}`, () => changeMemberRole(projectId, member.user_id, role), '成员角色已更新'); }}>
              {ROLES.map(role => <option key={role.value} value={role.value}>{role.label} · {role.value}</option>)}</select>
              : <span className={`access-role-badge role-${member.role}`}>{ROLES.find(role => role.value === member.role)?.label} · {member.role}</span>}
            {canManage && <button className="access-remove" data-action="remove-member" disabled={!!busy}
              onClick={() => { void mutate(`remove:${member.user_id}`, () => removeMember(projectId, member.user_id), '成员已移除'); }}>移除</button>}
          </div>)}
          {!members.length && <p className="access-empty">当前没有可显示的成员</p>}
        </div>
        {canManage ? <form className="access-member-add" onSubmit={add}>
          <h3>添加成员</h3>
          <div className="access-member-add-fields">
            <label><span>选择用户</span><select name="user_id" aria-label="选择用户" value={selectedUser} required disabled={!!busy}
              onChange={event => setSelectedUser(event.target.value)}><option value="">选择已有的平台用户</option>
              {available.map(user => <option key={user.id} value={user.id}>{user.display_name || user.username}（{user.username}）</option>)}</select></label>
            <label><span>IP 角色</span><select name="role" aria-label="新成员角色" value={selectedRole} disabled={!!busy} onChange={event => setSelectedRole(event.target.value as Role)}>
              {ROLES.map(role => <option key={role.value} value={role.value}>{role.label} · {role.value}</option>)}</select></label>
            <button className="btn btn-primary" disabled={!!busy || !selectedUser}>{busy === 'add' ? '添加中…' : '添加成员'}</button>
          </div>
          <p className="access-field-help">找不到成员时，请先让平台管理员到「用户管理」创建账号。IP 至少保留一位有效的 owner。</p>
        </form> : <p className="access-field-help access-readonly-note">你可以查看成员。成员邀请、移除与角色调整由这个 IP 的 owner 管理。</p>}
      </>}
    </div>
  </div>, document.body);
}
