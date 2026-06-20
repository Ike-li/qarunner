import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/runs': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/tests': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/auth': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/users': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/profiles': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/schedules': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
