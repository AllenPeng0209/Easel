import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { assetArchivePlugin } from './build/assetArchive.mjs'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), assetArchivePlugin()],
  build: { emptyOutDir: false },
  base: './',  // 相对路径，适配 proxy
})
