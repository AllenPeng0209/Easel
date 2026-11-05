/** Xiangya embedding transport only; the upstream workbench owns all production UI. */
export function xiangyaContext() {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  const project = params.get('xiangyaProject') || ''
  const ip = params.get('xiangyaIp') || 'default'
  if (!/^[a-f0-9]{32}$/.test(project) || !/^(default|[a-f0-9]{16})$/.test(ip)) return null
  return { project, ip, user: params.get('xiangyaUser') || '', prefix: (ip === 'default' ? '/api' : `/api/projects/${ip}`) + `/workshop/projects/${project}` }
}
export function xiangyaHeaders(): Record<string, string> { const context = xiangyaContext(); return context?.user ? { 'X-Easel-User': context.user } : {} }
export function xiangyaApiBase() { const context = xiangyaContext(); return context ? context.prefix + '/engine/api/v1' : '/api/v1' }
export function xiangyaMediaUrl(value: string) {
  if (!value || /^(https?:|data:|blob:)/i.test(value)) return value || ''
  const local = '/' + value.replace(/^\/+/, '')
  return xiangyaContext() && local.startsWith('/static/') ? xiangyaContext()!.prefix + '/engine' + local : local
}
export function xiangyaAction(action: 'back' | 'review' | 'chat') {
  const context = xiangyaContext()
  if (context) window.parent.postMessage({ type: 'xiangya-huobao', project: context.project, action }, window.location.origin)
}
export function xiangyaBack(fallback: () => unknown) { if (xiangyaContext()) xiangyaAction('back'); else fallback() }
export async function xiangyaWait(job: { id: string }) {
  const context = xiangyaContext()
  if (!context) throw new Error('制作任务所属项目不可用')
  while (true) {
    const response = await fetch(context.prefix + '/actions', { cache: 'no-store', headers: xiangyaHeaders() })
    const data = await response.json()
    if (!response.ok) throw new Error(data.detail || '无法读取原任务')
    const current = data.jobs.find((row: any) => row.id === job.id)
    if (!current) throw new Error('原制作任务不存在')
    window.parent.postMessage({ type: 'xiangya-huobao', project: context.project, action: 'job', job: current }, window.location.origin)
    if (current.status === 'completed') return
    if (['failed', 'unconfirmed', 'cancelled'].includes(current.status)) throw new Error(current.message || '制作任务未完成')
    await new Promise(resolve => setTimeout(resolve, 1500))
  }
}
