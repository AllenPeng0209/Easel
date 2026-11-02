/**
 * Copyright (c) 2026 DeepSeek. MIT License.
 * Source: packages/client/ui-sidebar-documentpreview/src/client/html/pack.ts
 * Commit: ce7073d51254c26cd63b22a60835b8f948deb447.
 * Easel changes: static image binary payload extension and local byte-reader type.
 * See integrations/deepseek-harness/LICENSE.
 */
/** Finite HTML-declared classic scripts and stylesheets; no module, CSS dependency or runtime fetch traversal. */
import type { HtmlAsset, HtmlBundle } from './bootstrap.ts'
import type { DocumentFileBytes } from './types'
import { decodeText } from './bytes.ts'

/**
 * A read bound to the original document's session and directory, using ordinary file operations.
 * @param reference - HTML-decoded relative URL, including any query or fragment; the reader resolves its file path.
 * @param signal - cancellation of this packing operation.
 * @returns complete file bytes; permission and read failures reject.
 */
export type ReadHtmlRelative = (reference: string, signal: AbortSignal) => Promise<DocumentFileBytes>

const MAX_ASSET_BYTES = 4 * 1024 * 1024
const MAX_TOTAL_BYTES = 32 * 1024 * 1024
const MAX_ASSETS = 64

/** Whether this reference can be read relative to the original document, never the parent application URL. */
function relative(reference: string): boolean {
  return reference.length > 0 && !/^(?:[a-z][a-z\d+.-]*:|[/\\#?])/iu.test(reference) && !reference.includes('\0')
}

/**
 * Collect static dependencies without executing or mounting document elements in the parent page.
 * A base element leaves URL resolution to the browser. Only direct .js classic scripts and .css
 * links are packed; local CSS url/import, modules and dynamically constructed URLs are unsupported.
 * @param data - complete UTF-8 HTML bytes.
 * @param readRelative - original-document-scoped read, never exposed to the iframe.
 * @param signal - stops reads and prevents publication after cancellation.
 * @returns complete HTML and its finite static asset set; decoding, limits and read failures reject.
 */
export async function packHtml(
  data: Uint8Array<ArrayBuffer>,
  readRelative: ReadHtmlRelative,
  signal: AbortSignal,
): Promise<HtmlBundle> {
  signal.throwIfAborted()
  let total = data.byteLength
  if (total > MAX_TOTAL_BYTES) throw new Error('HTML package exceeds its total byte limit')
  const template = document.createElement('template')
  template.innerHTML = decodeText(data)
  const assets: HtmlAsset[] = []
  if (template.content.querySelector('base[href]') !== null) return { data, assets }

  const seen = new Set<string>()
  for (const element of template.content.querySelectorAll('script[src],link[href],img[src]')) {
    const script = element.localName === 'script'
    const image = element.localName === 'img'
    const type = element.getAttribute('type')?.trim().toLowerCase() ?? ''
    if (script && !['', 'text/javascript', 'application/javascript'].includes(type)) continue
    if (!script && !image && !(element.getAttribute('rel') ?? '').toLowerCase().split(/\s+/u).includes('stylesheet')) continue
    // The selector requires the corresponding URL attribute.
    const reference = element.getAttribute(script || image ? 'src' : 'href') as string
    const suffix = reference.search(/[?#]/u)
    const path = suffix === -1 ? reference : reference.slice(0, suffix)
    if (!relative(reference) || !(script ? /\.js$/iu : image ? /\.(?:png|jpe?g|gif|webp|avif|bmp|ico|svg)$/iu : /\.css$/iu).test(path)) continue
    const kind = script ? 'script' : image ? 'image' : 'stylesheet'
    const key = `${kind}:${reference}`
    if (seen.has(key)) continue
    if (assets.length >= MAX_ASSETS) throw new Error('HTML package exceeds its asset count limit')
    signal.throwIfAborted()
    const asset = await readRelative(reference, signal)
    signal.throwIfAborted()
    const size = asset.data.byteLength
    if (size > MAX_ASSET_BYTES) throw new Error('HTML asset exceeds its byte limit')
    total += size
    if (total > MAX_TOTAL_BYTES) throw new Error('HTML package exceeds its total byte limit')
    if (!image) decodeText(asset.data)
    assets.push({ kind, reference, data: asset.data, ...(image ? { mime: imageMime(path) } : {}) })
    seen.add(key)
  }
  return { data, assets }
}

/** Binary image MIME comes from the declared, allowlisted suffix. */
function imageMime(path: string): string {
  const extension = path.split('.').at(-1)?.toLowerCase();
  const types: Record<string, string> = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
    gif: 'image/gif', webp: 'image/webp', avif: 'image/avif', bmp: 'image/bmp', ico: 'image/x-icon', svg: 'image/svg+xml' };
  return types[extension || ''] || 'application/octet-stream';
}
