/** User-visible video inputs. Provider output metadata is never a new request. */
export function localVideoInputProfile(model = '', config) {
  if (config?.provider !== 'minimax') return null
  try {
    const base = new URL(config.base_url || config.baseUrl || '')
    if (!['127.0.0.1', 'localhost'].includes(base.hostname) || base.pathname.replace(/\/$/, '') !== '/v1') return null
  } catch { return null }
  const name = String(model).split('/').pop().toLowerCase()
  if (name === 'pika-h3-flex-6step') return { kind: 'pika', min: 5, max: 15, label: 'H3 Flex', singleImage: true }
  if (['minimax-h3-base', 'minimax-h3-dense8'].includes(name)) {
    return { kind: 'fixed', min: 1, max: 14, label: '本机 H3', singleImage: true,
      dimensions: { '9:16': '576×1024', '16:9': '1024×576' } }
  }
  if (name === 'minimax-h3-ref2va-ref11-8step') return { kind: 'fixed', min: 1, max: 14, label: 'H3 全参考', singleImage: false }
  return null
}

export function videoDurationConstraints(model = '', config) {
  return localVideoInputProfile(model, config) || { min: 2, max: 30, label: '当前模型', singleImage: false }
}

export function videoDurationError(value, constraints) {
  const number = typeof value === 'number' ? value
    : typeof value === 'string' && value.trim() ? Number(value) : NaN
  if (!Number.isInteger(number) || number < constraints.min || number > constraints.max) {
    return `${constraints.label} 需要 ${constraints.min}–${constraints.max} 秒整数。请调整分镜时长或合并生产段，不会自动取整或改变计划。`
  }
  return ''
}

export function videoReferencePlan(assets, { limit = 9, primaryImage = '', singleImage = false } = {}) {
  const byUrl = new Map()
  const localKey = url => url.replace(/^\/(static\/)/, '$1')
  for (const asset of assets) {
    if (typeof asset.url !== 'string' || !asset.url.trim()) continue
    const url = asset.url.trim()
    const key = localKey(url)
    if (!byUrl.has(key)) byUrl.set(key, { url, names: [] })
    const names = byUrl.get(key).names
    if (asset.name && !names.includes(asset.name)) names.push(asset.name)
  }
  const primary = typeof primaryImage === 'string' ? primaryImage.trim() : ''
  const primaryKey = localKey(primary)
  if (singleImage && primary) {
    if (!byUrl.has(primaryKey)) byUrl.set(primaryKey, { url: primary, names: ['已保存的镜头首帧'] })
    else byUrl.get(primaryKey).url = primary
  }
  const candidates = [...byUrl.values()]
  const selected = singleImage
    ? (primary ? [byUrl.get(primaryKey)] : candidates.length === 1 ? candidates : [])
    : candidates.slice(0, limit)
  const nameToIndex = Object.create(null)
  selected.forEach((item, index) => {
    item.names.forEach(name => { if (!(name in nameToIndex)) nameToIndex[name] = index + 1 })
  })
  return {
    candidates,
    urls: selected.map(item => item.url),
    nameToIndex,
    selectedUrl: selected[0]?.url || '',
    error: singleImage && !primary && candidates.length > 1
      ? '当前通道每次只发送 1 张参考图。请选择本镜主参考图；其他绑定素材不会作为图片发送。' : '',
  }
}

export function generatedVideoDuration(task) {
  let params = task?.params
  if (typeof params === 'string') {
    try { params = JSON.parse(params) } catch { params = null }
  }
  const value = params?.resultDuration ?? task?.result_duration ?? task?.resultDuration
  const number = typeof value === 'number' ? value
    : typeof value === 'string' && value.trim() ? Number(value) : NaN
  return Number.isFinite(number) && number > 0 ? number : null
}

export function formatVideoSeconds(value) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? String(Math.round(number * 100) / 100) : '—'
}
