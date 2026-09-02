import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    // 开发时把 API 代理到本地 web_api 服务
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8501',
        changeOrigin: true,
      },
    },
  },
})