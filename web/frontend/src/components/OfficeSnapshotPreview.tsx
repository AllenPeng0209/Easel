/**
 * On-demand Office conversion, request lifetime, and font notice adapted from
 * DeepSeek Harness ui-sidebar-documentpreview/src/client/office
 * @ ce7073d (MIT). Conversion is bound to 响芽's fixed artifact identity.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { officeSnapshotPreviewHref, safeDeliverableHref, type ChatDeliverable } from '../lib/deliverables';
import { projectFetch } from '../lib/projectScope';
import './OfficeSnapshotPreview.css';

const MAX_PDF_BYTES = 32 * 1024 * 1024;
const knownErrors = new Map([
  ['converter_unavailable', 'Office 转换服务未安装或不可用。可下载原文件，请管理员检查本机转换服务。'],
  ['busy', 'Office 转换服务正在处理其他文档，请稍后重试。'],
  ['conversion_timeout', 'Office 文档转换超时，可重试或下载原文件。'],
  ['invalid_document', 'Office 文件格式不完整，无法转换预览。'],
  ['conversion_failed', 'Office 文档转换失败，可下载原文件查看。'],
]);
type Fonts = { status: 'unknown' } | { status: 'checked'; missing: string[]; missingCount: number };
type PreviewState = { key: string; phase: 'loading' | 'ready' | 'error' | 'unavailable'; message?: string;
  pdf?: { url: string; fonts: Fonts } };

function readFonts(headers: Headers): Fonts {
  if (headers.get('X-Easel-Office-Font-Status') === 'checked' &&
      headers.get('X-Easel-Office-Font-Source') === 'declared_inventory') {
    try {
      const encoded = headers.get('X-Easel-Office-Missing-Fonts') || '';
      const missing: unknown = encoded.length <= 12_000 ? JSON.parse(decodeURIComponent(encoded)) : null;
      if (Array.isArray(missing) && missing.length <= 64 && missing.every(font => typeof font === 'string'
          && !!font.trim() && font.length <= 120 && !/[\p{Cc}\p{Cs}]/u.test(font))) {
        const names = [...new Set<string>(missing)];
        const countHeader = headers.get('X-Easel-Office-Missing-Font-Count');
        const missingCount = countHeader === null ? names.length
          : /^(?:0|[1-9]\d{0,7})$/.test(countHeader) ? Number(countHeader) : -1;
        if (missingCount >= names.length) return { status: 'checked', missing: names, missingCount };
      }
    } catch { /* Unknown diagnostics must not masquerade as an empty checked list. */ }
  }
  return { status: 'unknown' };
}

async function failureLabel(response: Response): Promise<string> {
  if (response.status === 413) return '固定版本超过 Office 预览大小限制。';
  if (response.status === 415) return '此 Office 格式暂不支持转换预览。';
  try {
    const payload: unknown = await response.json();
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : undefined;
    const code = detail && typeof detail === 'object' && 'code' in detail ? detail.code : undefined;
    if (typeof code === 'string') return knownErrors.get(code) ?? 'Office 预览暂时无法生成，请重试。';
  } catch { /* Use fixed wording for other errors. */ }
  return 'Office 预览暂时无法生成，请重试。';
}

export default function OfficeSnapshotPreview({ file }: { file: ChatDeliverable }) {
  const href = officeSnapshotPreviewHref(file);
  const original = safeDeliverableHref(file);
  const key = JSON.stringify([href, file.artifactId, file.sha256]);
  const [retry, setRetry] = useState<{ key: string; attempt: number }>();
  const attempt = retry?.key === key ? retry.attempt : 0;
  const [state, setState] = useState<PreviewState>();
  const objectUrl = useRef<string | undefined>(undefined);
  const release = useCallback(() => {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    objectUrl.current = undefined;
  }, []);

  useEffect(() => release, [key, release]);

  useEffect(() => {
    if (!href || !file.sha256) return;
    const controller = new AbortController();
    const updateFailure = (message: string, unavailable = false) => {
      if (controller.signal.aborted) return;
      if (unavailable) release();
      setState(previous => ({ key, phase: unavailable ? 'unavailable' : 'error', message,
        pdf: !unavailable && previous?.key === key ? previous.pdf : undefined }));
    };
    setState(previous => ({ key, phase: 'loading', pdf: previous?.key === key ? previous.pdf : undefined }));
    void (async () => {
      const response = await projectFetch(href, { signal: controller.signal, cache: 'no-store', redirect: 'error' });
      if (!response.ok) {
        if ([401, 403, 404, 409, 410].includes(response.status)) updateFailure('此固定 Office 版本暂不可用。', true);
        else updateFailure(await failureLabel(response));
        return;
      }
      if (response.headers.get('X-Easel-Office-Source-Sha256') !== file.sha256 ||
          response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/pdf') {
        updateFailure('转换回执与所选固定版本不匹配。');
        return;
      }
      const advertisedLength = Number(response.headers.get('Content-Length'));
      if (advertisedLength > MAX_PDF_BYTES) {
        updateFailure('转换 PDF 超出预览大小限制。');
        return;
      }
      const pdf = await response.blob();
      const signature = new Uint8Array(await pdf.slice(0, 5).arrayBuffer());
      if (controller.signal.aborted) return;
      if (pdf.size > MAX_PDF_BYTES || String.fromCharCode(...signature) !== '%PDF-') {
        updateFailure('转换 PDF 内容无效或超过预览限制。');
        return;
      }
      const url = URL.createObjectURL(pdf);
      release();
      objectUrl.current = url;
      setState({ key, phase: 'ready', pdf: { url, fonts: readFonts(response.headers) } });
    })().catch(() => updateFailure('Office 预览暂时无法生成，请重试。'));
    return () => controller.abort();
  }, [href, file.sha256, key, attempt, release]);

  const current = state?.key === key ? state : undefined;
  const phase = !href ? 'unavailable' : current?.phase ?? 'loading';
  const pdf = current?.pdf;
  return <section className="office-snapshot-preview" data-office-preview={phase} aria-label="Office 固定版本预览">
    <div className="artifact-recovery">
      {phase === 'loading' && <span role="status">{pdf ? '正在重新转换，保留上次预览。' : '正在转换为 PDF…'}</span>}
      {phase === 'ready' && <span role="status">固定版本 V{file.revision} · 已转换为 PDF</span>}
      {(phase === 'error' || phase === 'unavailable') && <span role="alert">
        {current?.message || '此固定 Office 版本暂不可用。'}{pdf && '保留上次预览。'}
      </span>}
      {href && <button type="button" className="btn btn-sm" data-office-preview-retry disabled={phase === 'loading'}
        onClick={() => setRetry({ key, attempt: attempt + 1 })}>{phase === 'ready' ? '重新转换' : '重试转换'}</button>}
      {original && file.status === 'available' && <a href={`${original}?download=1`} download={file.name}>下载原文件</a>}
    </div>
    {pdf && <>
      <div className="office-font-status" data-office-font-status={pdf.fonts.status}>
        {pdf.fonts.status === 'unknown' ? '未能核验文档字体。'
          : pdf.fonts.missingCount === 0 ? '已检查文档声明的字体，均在本机清单中。'
            : <details><summary>文档声明中，本机未找到 {pdf.fonts.missingCount} 种字体，版式可能与原件不同</summary>
              <ul>{pdf.fonts.missing.map(font => <li key={font}>{font}</li>)}</ul>
              {pdf.fonts.missingCount > pdf.fonts.missing.length && <p>显示其中 {pdf.fonts.missing.length} 种。</p>}
            </details>}
      </div>
      <iframe data-office-pdf src={pdf.url} title={`${file.name} · 固定版本 PDF 预览`} />
    </>}
  </section>;
}
