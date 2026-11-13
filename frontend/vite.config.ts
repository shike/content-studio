import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 后端正式端口 8100（8000 是本机另一个项目，绝不能指过去）
    proxy: { '/api': 'http://127.0.0.1:8100' },
  },
})
