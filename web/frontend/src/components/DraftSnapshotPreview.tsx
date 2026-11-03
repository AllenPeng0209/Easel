import { useEffect, useState, useSyncExternalStore } from 'react';
import { draftSnapshotHref, MAX_DELIVERABLE_TEXT_PREVIEW_BYTES, type ChatDeliverable } from '../lib/deliverables';
import { HostReadStore } from '../vendor/deepseek-harness/deliverables/HostReadStore';

type DraftRead = { state: 'loading' | 'missing' | 'forbidden' | 'failed' | 'too-large' } | { state: 'ready'; text: string };

async function decode(response: Response): Promise<DraftRead> {
  if (!response.ok) return { state: response.status === 404 ? 'missing'
    : response.status === 401 || response.status === 403 ? 'forbidden' : 'failed' };
  if (!response.body) return { state: 'ready', text: '' };
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let size = 0;
  let text = '';
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) return { state: 'ready', text: text + decoder.decode() };
      size += chunk.value.byteLength;
      if (size > MAX_DELIVERABLE_TEXT_PREVIEW_BYTES) {
        await reader.cancel().catch(() => {});
        return { state: 'too-large' };
      }
      text += decoder.decode(chunk.value, { stream: true });
    }
  } catch {
    await reader.cancel().catch(() => {});
    return { state: 'failed' };
  } finally { reader.releaseLock(); }
}

class DraftReadStore extends HostReadStore<DraftRead> {
  constructor() {
    super({ loading: { state: 'loading' }, failed: { state: 'failed' },
      retryable: value => ['failed', 'missing', 'forbidden'].includes(value.state), decode });
  }
  load(url: string) { return this.loadUrl(url); }
}

export default function DraftSnapshotPreview({ file, initialOpen = false }: { file: ChatDeliverable; initialOpen?: boolean }) {
  const href = draftSnapshotHref(file);
  const [open, setOpen] = useState(initialOpen);
  const [store] = useState(() => new DraftReadStore());
  const [retained, setRetained] = useState<{ href: string; text: string }>();
  const reads = useSyncExternalStore(store.state.subscribe, store.state.getSnapshot, store.state.getSnapshot);
  useEffect(() => () => store.reset(), [store]);
  useEffect(() => { if (open && href) void store.load(href); }, [href, open, store]);
  const read = href ? reads[href] : undefined;
  useEffect(() => {
    if (read?.state === 'forbidden') setRetained(undefined);
  }, [read?.state]);
  if (!href) return null;
  const keep = read?.state !== 'ready' && read?.state !== 'forbidden' && retained?.href === href;
  const text = read?.state === 'ready' ? read.text : keep ? retained.text : undefined;
  const reload = () => {
    if (read?.state !== 'ready' || store.state.getSnapshot()[href]?.state !== 'ready') return;
    setRetained({ href, text: read.text });
    store.reset();
    void store.load(href);
  };
  return <section className="business-object__draft-preview" aria-label="固定稿件内容">
    <button type="button" className="btn btn-sm" aria-expanded={open} onClick={() => {
      setOpen(!open);
    }}>{open ? '收起稿件内容' : '查看稿件内容'}</button>
    {open && read?.state === 'ready' && <button type="button" className="btn btn-sm" data-reload-fixed-text
      onClick={reload}>重新读取此版本</button>}
    {open && (!read || read.state === 'loading') && <p role="status">正在读取固定稿件…</p>}
    {open && keep && <p role="status">保留上次成功读取的固定版本内容。</p>}
    {open && text !== undefined && <pre tabIndex={0}>{text || '此稿件为空。'}</pre>}
    {open && read && read.state !== 'loading' && read.state !== 'ready' && <div role="alert">
      <p>{read.state === 'too-large' ? '稿件超过预览上限。' : read.state === 'failed' ? '固定稿件读取失败。'
        : read.state === 'forbidden' ? '当前权限无法读取固定稿件。' : '固定稿件暂不可用。'}</p>
      {read.state !== 'too-large' && <button type="button" className="btn btn-sm" data-retry-fixed-text onClick={() => void store.load(href)}>重试</button>}
      {read.state !== 'forbidden' && <> <a href={`${href}?download=1`} download={file.name}>下载此版本</a></>}
    </div>}
  </section>;
}
