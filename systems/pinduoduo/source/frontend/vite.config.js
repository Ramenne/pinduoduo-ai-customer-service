// Vite 构建与开发服务器配置：拼多多自动回复系统前端（Vue 3）
// 后端地址一律经环境变量（VITE_DEV_PROXY_TARGET / VITE_API_BASE_URL）配置，禁止在代码中写死 localhost（规范 21）
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // 加载当前 mode 下的环境变量（.env.development / .env.production）
  const env = loadEnv(mode, process.cwd(), '')

  // 开发环境 API 代理目标：经环境变量配置，未提供时回退合理默认值（规范 21 / 需求 25.4）
  const devProxyTarget = env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8089'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        // 统一使用 @ 指向 src 目录，简化模块引用
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: 9100,
      host: '0.0.0.0',
      proxy: {
        // 所有 API 请求经代理转发到后端（含 WebSocket 升级），目标地址由环境变量提供
        '/api': {
          target: devProxyTarget,
          changeOrigin: true,
          ws: true,
        },
        // 后端静态资源（如上传图片）代理
        '/static': {
          target: devProxyTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      assetsDir: 'assets',
    },
  }
})
