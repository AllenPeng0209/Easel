import 'dotenv/config'
import { serve } from '@hono/node-server'
import { serveStatic } from '@hono/node-server/serve-static'
import { Hono } from 'hono'
import { cors } from 'hono/cors'
import path from 'path'
import { fileURLToPath } from 'url'

import dramas from './routes/dramas.js'
import episodes from './routes/episodes.js'
import storyboards from './routes/storyboards.js'
import scenes from './routes/scenes.js'
import characters from './routes/characters.js'
import tasks from './routes/tasks.js'
import upload from './routes/upload.js'
import aiConfigs, { aiProviders } from './routes/aiConfigs.js'
import stylePresets from './routes/stylePresets.js'
import prompts from './routes/prompts.js'
import agent from './routes/agent.js'
import merge from './routes/merge.js'
import skills from './routes/skills.js'
import props from './routes/props.js'
import settings from './routes/settings.js'
import storage from './routes/storage.js'
import serverUpdate from './routes/serverUpdate.js'
import { requestLogger, errorHandler } from './middleware/logger.js'
import { resumeInterruptedTasks } from './services/generation.js'
import { DATA_ROOT } from './utils/paths.js'
import { generateImageThumb } from './utils/storage.js'
import { extractVideoPoster } from './utils/video-poster.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(__dirname, '../..')

const app = new Hono()
app.use('*', async (c, next) => {
  if (process.env.XIANGYA_RUNTIME_TOKEN && c.req.header('X-Xiangya-Runtime') !== process.env.XIANGYA_RUNTIME_TOKEN) return c.json({ error: 'Unauthorized runtime client' }, 401)
  await next()
})

// Middleware
app.use('*', cors({
  origin: ['http://localhost:3013', 'http://localhost:5679'],
  credentials: true,
}))
app.use('*', requestLogger)
app.use('*', errorHandler)

// Xiangya imports reuse the upstream thumbnail/poster implementation.
app.post('/api/v1/xiangya/artwork', async c => {
  const body = await c.req.json()
  if (!Array.isArray(body.paths) || body.paths.length > 600 || body.paths.some((value: unknown) => typeof value !== 'string' || !/^static\/xiangya\/[a-f0-9-]+\.(png|jpe?g|webp|mp4|mov|webm)$/i.test(value))) return c.json({ message: 'Invalid imported media' }, 400)
  for (const value of body.paths) {
    if (/\.(mp4|mov|webm)$/i.test(value)) await extractVideoPoster(value)
    else await generateImageThumb(value)
  }
  return c.json({ code: 200, data: { completed: body.paths.length } })
})

// Health check（version 供部署巡检/更新检查核对当前运行版本）
app.get('/api/v1/health', (c) => c.json({
  status: 'ok',
  version: process.env.HUOBAO_VERSION || undefined,
  timestamp: new Date().toISOString(),
}))

// API routes
const api = new Hono()
api.route('/dramas', dramas)
api.route('/episodes', episodes)
api.route('/storyboards', storyboards)
api.route('/scenes', scenes)
api.route('/characters', characters)
api.route('/tasks', tasks)
api.route('/upload', upload)
api.route('/ai-configs', aiConfigs)
api.route('/ai-providers', aiProviders)
api.route('/style-presets', stylePresets)
api.route('/prompts', prompts)
api.route('/agent', agent)
api.route('/merge', merge)
api.route('/skills', skills)
api.route('/props', props)
api.route('/storage', storage)
api.route('/settings', settings)
api.route('/server-update', serverUpdate)

app.route('/api/v1', api)

// Serve static files (storage)
// 生成的图片/视频按 uuid 命名、内容不变，标记为 immutable 让浏览器长缓存
app.use('/static/*', async (c, next) => {
  await next()
  if (c.res.ok) c.header('Cache-Control', 'public, max-age=31536000, immutable')
})
app.use('/static/*', serveStatic({ root: DATA_ROOT }))

// Serve frontend (production build) — 桌面版由主进程注入 FRONTEND_DIST（resources/frontend）
const distPath = process.env.FRONTEND_DIST || path.join(projectRoot, 'frontend', 'dist')
app.use('*', serveStatic({ root: distPath }))
app.get('*', serveStatic({ root: distPath, path: 'index.html' }))

const port = Number(process.env.PORT || 5679)
console.log(`🚀 Huobao Drama TS server on http://localhost:${port}`)

await resumeInterruptedTasks()

serve({ fetch: app.fetch, port, hostname: '127.0.0.1' })
