/**
 * Per-fence copy adapted from DeepSeek Harness ui-primitives/src/markdown/CodeBlock.tsx
 * @ ce7073d (MIT). Copy reads the literal code DOM, independently of grammar decoration.
 */
import { writeClipboard } from '../vendor/deepseek-harness/message/clipboard';

type CopyState = 'idle' | 'pending' | 'copied' | 'failed';

const labels: Record<CopyState, string> = {
  idle: '复制代码', pending: '复制中…', copied: '已复制', failed: '复制失败',
};

interface Classes { toolbar: string; button: string; status: string }

export function bindAnswerCodeCopy(root: HTMLElement, classes: Classes): () => void {
  const document = root.ownerDocument;
  const controls = [...root.querySelectorAll<HTMLElement>('pre > code')].map((code, index) => {
    const toolbar = document.createElement('div');
    toolbar.className = classes.toolbar;
    toolbar.setAttribute('data-code-copy-toolbar', '');
    toolbar.setAttribute('data-text-interaction-exclude', '');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = classes.button;
    button.setAttribute('data-code-copy-button', '');
    button.setAttribute('aria-label', `复制代码块 ${index + 1}`);
    const status = document.createElement('span');
    status.className = classes.status;
    status.setAttribute('role', 'status');
    toolbar.append(button, status);
    code.parentElement?.before(toolbar);

    let state: CopyState = 'idle';
    let mounted = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const update = (next: CopyState) => {
      state = next;
      button.dataset.codeCopyState = next;
      button.textContent = labels[next];
      button.disabled = next === 'pending';
      status.textContent = next === 'copied' || next === 'failed' ? labels[next] : '';
    };
    const copy = async () => {
      if (state === 'pending' || !mounted || !root.contains(code)) return;
      if (timer !== undefined) clearTimeout(timer);
      const source = code.textContent ?? '';
      update('pending');
      let ok = false;
      try { ok = await writeClipboard(source); } catch { /* Clipboard unavailable. */ }
      if (!mounted || !button.isConnected || !root.contains(code)) return;
      update(ok ? 'copied' : 'failed');
      timer = setTimeout(() => { timer = undefined; update('idle'); }, 1_500);
    };
    update('idle');
    button.addEventListener('click', copy);
    return () => {
      mounted = false;
      if (timer !== undefined) clearTimeout(timer);
      button.removeEventListener('click', copy);
      toolbar.remove();
    };
  });

  return () => { controls.forEach(dispose => dispose()); };
}
