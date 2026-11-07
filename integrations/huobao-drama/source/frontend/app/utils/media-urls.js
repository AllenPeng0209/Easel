function privateMedia(value, prefix, origin) {
  if (!value || !prefix || !origin) return null
  try {
    const url = new URL(value, origin)
    return url.origin === new URL(origin).origin && url.pathname.startsWith(prefix + '/engine/static/') ? url : null
  } catch { return null }
}

export function playbackMediaUrl(value, prefix, origin, quality = 'original') {
  const url = privateMedia(value, prefix, origin)
  if (!url || !/\.mp4$/i.test(url.pathname)) return value
  url.searchParams.set('playback', quality === 'low' ? 'low' : 'original')
  return url.pathname + url.search + url.hash
}

export function thumbnailMediaUrl(value, prefix, origin, width = 192) {
  const url = privateMedia(value, prefix, origin)
  if (!url || !/\.(?:png|jpe?g|webp)$/i.test(url.pathname)) return value
  url.searchParams.set('thumb', '1')
  url.searchParams.set('w', String(Number.isFinite(width) ? Math.max(64, Math.min(Math.round(width), 640)) : 192))
  return url.pathname + url.search + url.hash
}
