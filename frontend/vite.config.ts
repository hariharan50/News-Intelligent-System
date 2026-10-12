import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// In dev, the browser talks to Vite and Vite forwards /api to FastAPI, so the app
// always calls relative /api URLs (in Docker, nginx does the same forwarding).
// Set VITE_API_PROXY_TARGET in frontend/.env.local if your API isn't on port 8000.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
