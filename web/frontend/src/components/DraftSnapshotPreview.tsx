import { useEffect, useState, useSyncExternalStore } from 'react';
import { draftSnapshotHref, type ChatDeliverable } from '../lib/deliverables';
import { HostReadStore } from '../vendor/deepseek-harness/deliverables/HostReadStore';

const MAX_BYTES = 1024 * 1024;
type DraftRead = { state: 'loading' | 'missing' | 'failed' | 'too-large' } | { state: 'ready'; text: string };

async function decode(response: Response): Promise<DraftRead> {
  if (!response.ok) return { state: response.status === 404 ? 'missing' : 'failed' };
  if (!response.body) return { state: 'ready', text: '' };
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let size = 0;
  let text = '';
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) return { state: 'ready', text: text + decoder.decode() };
      size += chunk.value.byteLength;
      if (size > MAX_BYTES) {
        await reader.cancel().catch(() => {});
        return { state: 'too-large' };
      }
      text += decoder.decode(chunk.value, { stream: true });
    }
  } finally { reader.releaseLock(); }
}

class DraftReadStore extends HostReadStore<DraftRead> {
  constructor() {
    super({ loading: { state: 'loading' }, failed: { state: 'failed' },
      retryable: value => value.state === 'failed' || value.state === 'missing', decode });
  }
  load(url: string) { return this.loadUrl(url); }
}

export default function DraftSnapshotPreview({ file, initialOpen = false }: { file: ChatDeliverable; initialOpen?: boolean }) {
  const href = draftSnapshotHref(file);
  const [open, setOpen] = useState(initialOpen);
  const [store] = useState(() => new DraftReadStore());
  const reads = useSyncExternalStore(store.state.subscribe, store.state.getSnapshot, store.state.getSnapshot);
  useEffect(() => () => store.reset(), [store]);
  useEffect(() => { if (open && href) void store.load(href); }, [href, open, store]);
  if (!href) return null;
  const read = reads[href];
  return <section className="business-object__draft-preview" aria-label="固定稿件内容">
    <button type="button" className="btn btn-sm" aria-expanded={open} onClick={() => {
      setOpen(!open);
    }}>{open ? '收起稿件内容' : '查看稿件内容'}</button>
    {open && read?.state === 'loading' && <p role="status">正在读取固定稿件…</p>}
    {open && read?.state === 'ready' && <pre tabIndex={0}>{read.text || '此稿件为空。'}</pre>}
    {open && read && read.state !== 'loading' && read.state !== 'ready' && <div role="alert">
      <p>{read.state === 'too-large' ? '稿件超过预览上限。' : '固定稿件暂不可用。'}</p>
      {read.state !== 'too-large' && <button type="button" className="btn btn-sm" onClick={() => void store.load(href)}>重试</button>}
      {' '}<a href={`${href}?download=1`} download={file.name}>下载此版本</a>
    </div>}
  </section>;
}
