import fs from 'node:fs'
import path from 'node:path'
import { Readable, Transform } from 'node:stream'
import { pipeline } from 'node:stream/promises'
import { STORAGE_ROOT } from '../utils/paths.js'

export class VideoDownloadError extends Error {
  constructor(public code: string, message: string, public retryable = false) { super(message) }
}

const transportNames = new Set(['TypeError', 'TimeoutError', 'AbortError', 'AggregateError',
  'HeadersTimeoutError', 'BodyTimeoutError', 'ConnectTimeoutError', 'SocketError', 'RequestAbortedError'])
const transportCodes: Record<string, string> = {
  UND_ERR_HEADERS_TIMEOUT: '等待响应头超时', UND_ERR_BODY_TIMEOUT: '读取响应体超时',
  UND_ERR_CONNECT_TIMEOUT: '建立连接超时', UND_ERR_SOCKET: '连接意外断开', UND_ERR_ABORTED: '请求已中止',
  ECONNRESET: '连接被重置', ECONNREFUSED: '连接被拒绝', ETIMEDOUT: '连接超时', EPIPE: '连接写入中断',
  ENOTFOUND: '地址解析失败', EAI_AGAIN: '地址解析暂时失败', ERR_STREAM_PREMATURE_CLOSE: '响应流提前关闭',
  ERR_TLS_CERT_ALTNAME_INVALID: '连接证书校验失败', CERT_HAS_EXPIRED: '连接证书已过期',
  DEPTH_ZERO_SELF_SIGNED_CERT: '连接证书未受信任',
  UND_ERR_REQ_CONTENT_LENGTH_MISMATCH: '请求长度不一致', UND_ERR_RES_CONTENT_LENGTH_MISMATCH: '响应长度不一致',
}

/** Read data fields only: custom getters, messages and socket objects are never diagnostic payloads. */
function errorField(value: object, key: string): { present: boolean; value?: unknown } {
  try {
    let current: object | null = value
    for (let depth = 0; current && depth < 4; depth++, current = Object.getPrototypeOf(current)) {
      const descriptor = Object.getOwnPropertyDescriptor(current, key)
      if (descriptor) return { present: true, value: 'value' in descriptor ? descriptor.value : undefined }
    }
  } catch { return { present: true } }
  return { present: false }
}

function safeTransportError(error: unknown): string | undefined {
  if (!error || typeof error !== 'object') return undefined
  const pending = [{ value: error, depth: 0 }], seen = new Set<object>()
  const names = new Set<string>(), codes = new Set<string>()
  let hasCause = false, transport = false
  try {
    if (error instanceof TypeError) { transport = true; names.add('TypeError') }
    if (error instanceof AggregateError) { transport = true; names.add('AggregateError') }
    if (error instanceof DOMException) transport = true
  } catch { transport = true }
  while (pending.length && seen.size < 12) {
    const { value, depth } = pending.shift()!
    if (!value || typeof value !== 'object' || seen.has(value)) continue
    seen.add(value)
    let name = errorField(value, 'name').value
    // DOMException uses a native accessor rather than an own data field.
    try {
      if (value instanceof DOMException) name = Object.getOwnPropertyDescriptor(DOMException.prototype, 'name')?.get?.call(value)
    } catch { /* An unknown name is intentionally omitted. */ }
    if (typeof name === 'string' && (name === 'Error' || transportNames.has(name))) names.add(name)
    if (typeof name === 'string' && transportNames.has(name)) transport = true
    const code = errorField(value, 'code').value
    if (typeof code === 'string' && Object.hasOwn(transportCodes, code)) codes.add(code)
    const cause = errorField(value, 'cause')
    hasCause ||= cause.present
    if (depth < 4 && cause.value && typeof cause.value === 'object') pending.push({ value: cause.value, depth: depth + 1 })
    if (name === 'AggregateError' && depth < 4) {
      const errors = errorField(value, 'errors').value
      try {
        if (Array.isArray(errors)) for (let index = 0; index < Math.min(errors.length, 3); index++) {
          const item = errorField(errors, String(index)).value
          if (item && typeof item === 'object') pending.push({ value: item, depth: depth + 1 })
        }
      } catch { /* Do not serialize malformed aggregate causes. */ }
    }
  }
  if (!transport && !hasCause && !codes.size) return undefined
  const knownCodes = [...codes]
  const reason = knownCodes.length ? transportCodes[knownCodes[0]]
    : names.has('TimeoutError') ? '请求达到取消时限' : names.has('AbortError') ? '请求已中止' : '请求执行失败'
  return `${reason}（${[...names].join(' → ') || '错误类型未识别'}；${knownCodes.join(', ') || '底层原因未记录'}）`.slice(0, 600)
}

/** 不把 API key、签名链接带到诊断文本、日志或剪贴板。 */
export function safeTaskError(error: unknown): string {
  const transport = safeTransportError(error)
  if (transport) return transport
  return String(error instanceof Error ? error.message : error || 'Unknown failure')
    .replace(/https?:\/\/[^\s"'<>]+/gi, '[URL omitted]')
    .replace(/(Bearer\s+)\S+/gi, '$1[redacted]')
    .replace(/((?:api[_-]?key|signature|token|secret)\s*["']?\s*[:=]\s*["']?)[^\s,"'}&]+/gi, '$1[redacted]')
    .replace(/\bsk-[\w-]+/g, '[redacted]')
    .slice(0, 600)
}

/** 下载到临时文件，校验容器头后原子改名；同任务重复恢复不会新增媒体文件。 */
export async function downloadVideo(url: string, taskId: number, options: {
  root?: string; attempts?: number; timeoutMs?: number; retryDelayMs?: number; maxBytes?: number; headers?: Record<string, string>
} = {}): Promise<string> {
  const parsed = new URL(url)
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new VideoDownloadError('download_url', 'Invalid video URL protocol')
  const root = options.root ?? STORAGE_ROOT
  const dir = path.join(root, 'videos')
  await fs.promises.mkdir(dir, { recursive: true })
  const partial = path.join(dir, `task-${taskId}.part`)
  const attempts = options.attempts ?? 3
  const maxBytes = options.maxBytes ?? 512 * 1024 * 1024
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const signal = AbortSignal.timeout(options.timeoutMs ?? 120_000)
      const resp = await fetch(url, { signal, headers: options.headers, ...(options.headers ? { redirect: 'error' as const } : {}) })
      if (!resp.ok) {
        await resp.body?.cancel()
        throw new VideoDownloadError('download_http', `Video download HTTP ${resp.status}`, [408, 429].includes(resp.status) || resp.status >= 500)
      }
      if (Number(resp.headers.get('content-length')) > maxBytes) {
        await resp.body?.cancel()
        throw new VideoDownloadError('download_size', 'Video exceeds download size limit')
      }
      if (!resp.body) throw new VideoDownloadError('download_empty', 'Empty video response', true)
      let bytes = 0
      let head = Buffer.alloc(0)
      const validator = new Transform({ transform(chunk, _encoding, callback) {
        bytes += chunk.length
        if (head.length < 16) head = Buffer.concat([head, chunk.subarray(0, 16 - head.length)])
        if (bytes > maxBytes) return callback(new VideoDownloadError('download_size', 'Video exceeds download size limit'))
        callback(null, chunk)
      } })
      await pipeline(Readable.fromWeb(resp.body as any), validator, fs.createWriteStream(partial), { signal })
      const mp4 = head.length >= 12 && head.toString('ascii', 4, 8) === 'ftyp'
      const webm = head.length >= 4 && head.readUInt32BE(0) === 0x1a45dfa3
      if (!mp4 && !webm) throw new VideoDownloadError('download_format', 'Response is not an MP4 or WebM video')
      const name = `task-${taskId}.${webm ? 'webm' : 'mp4'}`
      await fs.promises.rename(partial, path.join(dir, name))
      return `static/videos/${name}`
    } catch (error) {
      await fs.promises.rm(partial, { force: true })
      const retryable = error instanceof VideoDownloadError ? error.retryable
        : error instanceof TypeError || ['TimeoutError', 'AbortError'].includes((error as Error).name)
      if (!retryable || attempt === attempts - 1) throw error
      await new Promise(resolve => setTimeout(resolve, (options.retryDelayMs ?? 1000) * 2 ** attempt))
    }
  }
  throw new VideoDownloadError('download_failed', 'Video download failed')
}
