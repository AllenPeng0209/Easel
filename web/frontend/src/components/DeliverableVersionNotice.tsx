/** Fixed-version metadata observation adapted from the Harness document preview.
 * Reloading metadata never replaces the bytes already selected in the preview.
 */
import { useEffect, useRef, useState } from 'react';
import { deliverableVersionsUrl, normalizeDeliverableVersions, type ChatDeliverable } from '../lib/deliverables';
import { projectFetch } from '../lib/projectScope';

type CheckState = 'idle' | 'loading' | 'ready' | 'failed' | 'unavailable';

export default function DeliverableVersionNotice({ file }: { file: ChatDeliverable }) {
  const [state, setState] = useState<CheckState>('idle');
  const [newest, setNewest] = useState<number>();
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => { request.current?.abort(); request.current = null; }, []);

  const url = deliverableVersionsUrl(file);
  if (file.storageMode !== 'snapshot' || !file.revision || !url) return null;
  const revision = file.revision;
  const check = async () => {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setState('loading');
    try {
      const response = await projectFetch(url, { cache: 'no-store', signal: controller.signal });
      if (controller.signal.aborted) return;
      if (response.status === 401 || response.status === 403 || response.status === 404 || response.status === 410) {
        setNewest(undefined);
        setState('unavailable');
        return;
      }
      if (!response.ok) throw new Error('Version check failed');
      const value: unknown = await response.json();
      if (controller.signal.aborted) return;
      if (!value || typeof value !== 'object' || !('versions' in value) || !Array.isArray(value.versions)) {
        throw new Error('Malformed version history');
      }
      const rows = normalizeDeliverableVersions(file, value.versions);
      if (!rows.some(item => item.artifactId === file.artifactId && item.revision === revision && item.sha256 === file.sha256)) {
        throw new Error('Selected version not confirmed');
      }
      setNewest(rows.find(item => item.storageMode === 'snapshot')?.revision ?? revision);
      setState('ready');
    } catch {
      if (!controller.signal.aborted) setState('failed');
    } finally {
      if (request.current === controller) request.current = null;
    }
  };

  return <div className="deliverable-version-notice" data-delivery-version-state={state}>
    <span>正在查看固定交付 V{revision}</span>
    <button type="button" className="btn btn-sm" data-check-delivery-versions disabled={state === 'loading'}
      onClick={() => void check()}>{state === 'failed' || state === 'unavailable' ? '重试检查版本' : '检查新版本'}</button>
    {newest !== undefined && newest > revision && <p role="status">发现较新交付 V{newest}，当前预览保留 V{revision}。</p>}
    {state === 'loading' && <p role="status">正在检查交付版本，当前内容保留。</p>}
    {state === 'ready' && newest === revision && <p role="status">未发现较新交付。</p>}
    {state === 'failed' && <p role="alert">检查版本失败，当前内容保留。</p>}
    {state === 'unavailable' && <p role="alert">交付版本信息暂不可用。</p>}
  </div>;
}
