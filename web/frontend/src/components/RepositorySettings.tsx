import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { fetchProject, leaveProject, transferOwnership, updateProject } from '../lib/access';
import type { Member, Repository } from '../lib/access';
import { openProjectHome } from '../lib/projectScope';

export default function RepositorySettings({ projectId, canManage, members, onChanged, onTransferred }: {
  projectId: string; canManage: boolean; members: Member[];
  onChanged: (name?: string) => void; onTransferred: () => Promise<void>;
}) {
  const [project, setProject] = useState<Repository | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [target, setTarget] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [leaving, setLeaving] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const alive = useRef(true);
  const load = async () => {
    try {
      const result = await fetchProject(projectId);
      if (!alive.current) return;
      setProject(result); setName(result.name); setDescription(result.description);
    } catch (error) { if (alive.current) setError(error instanceof Error ? error.message : '项目信息加载失败'); }
  };
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; }; }, [projectId]);
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!project || !canManage || busy || !name.trim()) return;
    setBusy('save'); setError(''); setNotice('');
    try {
      const next = await updateProject(projectId, { name: name.trim(), description: description.trim() });
      if (!alive.current) return;
      setProject(next); setName(next.name); setDescription(next.description); setConfirmation('');
      onChanged(next.name); setNotice('项目设置已保存');
    } catch (error) { if (alive.current) setError(error instanceof Error ? error.message : '保存失败，请重试'); }
    finally { if (alive.current) setBusy(''); }
  };
  const transfer = async (event: FormEvent) => {
    event.preventDefault(); if (!project || !canManage || busy || !target || confirmation !== project.name) return;
    setBusy('transfer'); setError(''); setNotice('');
    try {
      await transferOwnership(projectId, { user_id: target, expected_owner_id: project.owner.id, confirmation });
      if (alive.current) await onTransferred();
    } catch (error) { if (alive.current) setError(error instanceof Error ? error.message : '所有权转移未完成，请刷新后重试'); }
    finally { if (alive.current) setBusy(''); }
  };
  const leave = async () => {
    if (canManage || !leaving || busy) return;
    setBusy('leave'); setError('');
    try { await leaveProject(projectId); if (alive.current) { onChanged(); openProjectHome(); } }
    catch (error) { if (alive.current) setError(error instanceof Error ? error.message : '退出未完成，请重试'); }
    finally { if (alive.current) setBusy(''); }
  };
  if (!project) return <div className="repository-settings">{error ? <><p role="alert" className="access-error">{error}</p><button className="btn" onClick={() => { setError(''); void load(); }}>重新加载</button></> : <p className="access-empty">正在加载项目设置…</p>}</div>;
  const targets = members.filter(member => member.active && member.role !== 'owner');
  return <div className="repository-settings">
    <div className="repository-setting-section"><div className="repository-section-heading"><h3>项目基本信息</h3><span className="repo-private">私有项目</span></div>
      {canManage ? <form className="access-form" onSubmit={save}>
        <label htmlFor="repository-name">IP 项目名称</label><input id="repository-name" name="name" required maxLength={60} value={name} disabled={!!busy} onChange={event => setName(event.target.value)} />
        <label htmlFor="repository-description">项目简介</label><textarea id="repository-description" name="description" rows={3} maxLength={500} value={description} disabled={!!busy} onChange={event => setDescription(event.target.value)} />
        <div className="access-form-actions"><button className="btn btn-primary" disabled={!!busy || !name.trim()}>{busy === 'save' ? '保存中…' : '保存设置'}</button></div>
      </form> : <dl className="repository-facts"><dt>项目名称</dt><dd>{project.name}</dd><dt>项目简介</dt><dd>{project.description || '暂未填写'}</dd><dt>Owner</dt><dd>@{project.owner.username}</dd></dl>}
      <p className="access-field-help">项目仅对成员开放。内容、账号和权限随项目管理，平台管理员身份不自动授予项目访问权。</p>
    </div>
    {canManage ? <section className="repository-danger-zone" aria-labelledby="transfer-title">
      <div className="repository-section-heading"><h3 id="transfer-title">转移项目所有权</h3><span>谨慎操作</span></div>
      <p>将项目交给一位已有成员。转移后，对方成为唯一 Owner；你保留 Operator 权限，项目内容和账号继续留在项目内。</p>
      <form className="access-form" data-form="transfer" onSubmit={transfer}>
        <label htmlFor="repository-new-owner">接收所有权的成员</label><select id="repository-new-owner" name="new_owner" value={target} disabled={!!busy || !targets.length} required onChange={event => { setTarget(event.target.value); setConfirmation(''); }}>
          <option value="">{targets.length ? '选择一位项目成员' : '请先添加一位有效成员'}</option>{targets.map(member => <option key={member.user_id} value={member.user_id}>{member.display_name || member.username}（@{member.username}）</option>)}</select>
        <label htmlFor="repository-transfer-confirmation">输入 <strong>{project.name}</strong> 确认转移</label><input id="repository-transfer-confirmation" name="confirmation" value={confirmation} autoComplete="off" disabled={!!busy || !target} onChange={event => setConfirmation(event.target.value)} placeholder="输入项目完整名称" />
        <div className="access-form-actions"><button className="btn repository-danger-button" data-action="transfer-ownership" disabled={!!busy || !target || confirmation !== project.name}>{busy === 'transfer' ? '正在转移…' : '确认转移所有权'}</button></div>
      </form>
    </section> : <section className="repository-danger-zone"><h3>退出项目</h3><p>退出「{project.name}」后，你将失去此项目的访问权限。项目内容会保留，重新加入需要 Owner 添加你。</p>
      {!leaving ? <button className="btn repository-danger-button" data-action="request-leave" onClick={() => setLeaving(true)}>退出此项目</button>
        : <div className="repository-leave-confirm" role="group" aria-label="确认退出项目"><span>确定退出「{project.name}」？</span><button className="btn" disabled={!!busy} onClick={() => setLeaving(false)}>取消</button><button className="btn repository-danger-button" data-action="confirm-leave" disabled={!!busy} onClick={() => { void leave(); }}>{busy === 'leave' ? '正在退出…' : '确认退出项目'}</button></div>}
    </section>}
    {error && <p className="access-error" role="alert">{error}</p>}{notice && <p className="access-notice" role="status">{notice}</p>}
  </div>;
}
