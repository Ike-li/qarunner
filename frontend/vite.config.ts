import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Load environment variables (using empty string as prefix loads all process env variables)
  const env = loadEnv(mode, process.cwd(), '')
  const backendTarget = process.env.VITE_BACKEND_URL || env.VITE_BACKEND_URL || 'http://localhost:8000'
  const usePolling = process.env.VITE_USE_POLLING === 'true' || env.VITE_USE_POLLING === 'true'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      host: '0.0.0.0', // Allow external access (essential when running in Docker)
      allowedHosts: ['localhost', '127.0.0.1', 'frontend'],
      watch: usePolling ? { usePolling: true, interval: 100 } : undefined,
      proxy: {
        '/runs': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/tests': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/suites': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/auth': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/users': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/profiles': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/schedules': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/credentials': {
          target: backendTarget,
          changeOrigin: true,
        },
        '/cases': {
          target: backendTarget,
          changeOrigin: true,
        }
      }
    }
  }
})
