/** Canonical identity for an existing image reference; does not fetch any URL. */
export function primaryReferenceKey(value: unknown): string | null {
  if (typeof value !== 'string' || !value || value.length > 4096 || /[\s\\\x00-\x1f\x7f]/u.test(value)) return null
  if (/^\/?static\//.test(value)) {
    const relative = value.replace(/^\//, '')
    if (/[?#%]/.test(relative) || relative.split('/').some(part => !part || part === '.' || part === '..')) return null
    return relative
  }
  try {
    const url = new URL(value)
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.hash) return null
    return url.href
  } catch { return null }
}
