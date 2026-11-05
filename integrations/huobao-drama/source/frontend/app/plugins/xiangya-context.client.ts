/** Keep the host workspace context when the original router changes pages. */
export default defineNuxtPlugin(() => {
  const params = new URLSearchParams(location.search)
  if (!/^[a-f0-9]{32}$/.test(params.get('xiangyaProject') || '')) return
  const pinned = Object.fromEntries(['xiangyaProject', 'xiangyaIp', 'xiangyaUser', 'theme'].map(key => [key, params.get(key) || '']))
  useRouter().beforeEach(to => Object.entries(pinned).some(([key, value]) => to.query[key] !== value)
    ? { path: to.path, query: { ...to.query, ...pinned }, hash: to.hash, replace: true } : undefined)
})
