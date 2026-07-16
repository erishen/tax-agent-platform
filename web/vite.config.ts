import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        // Vite's http-proxy buffers SSE responses by default, so the browser
        // only receives the whole stream once the agent run finishes. Flush
        // headers immediately (and disable downstream buffering) for
        // text/event-stream so EventSource gets events live.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes, _req, res) => {
            const ct = (proxyRes.headers['content-type'] as string) || ''
            if (ct.includes('text/event-stream')) {
              // 浏览器 EventSource 强制要求 Content-Type: text/event-stream。
              // 必须在 flushHeaders()（即发送响应头）之前设置，否则 head 发出时
              // content-type 仍是代理默认/空值，浏览器会按 text/plain 拒绝连接。
              res.setHeader('Content-Type', 'text/event-stream')
              res.setHeader('Cache-Control', 'no-cache')
              res.setHeader('Connection', 'keep-alive')
              res.setHeader('X-Accel-Buffering', 'no')
              if (!res.headersSent) res.flushHeaders()
            }
          })
        },
      },
    },
  },
})
