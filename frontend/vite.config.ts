import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

const sourceRoot = fileURLToPath(new URL('./src', import.meta.url))

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), '')
  const backendTarget = environment.SECURERAG_API_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    resolve: { alias: { '@': sourceRoot } },
    server: {
      proxy: {
        '/api': {
          target: backendTarget,
          changeOrigin: false,
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: true,
      clearMocks: true,
    },
  }
})
