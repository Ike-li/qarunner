import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // Unit tests only. Keep Playwright's *.spec.ts under tests-e2e/ out of
    // Vitest's default glob, which would otherwise try to run them.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['tests-e2e/**', 'node_modules/**', '.git/**'],
    environment: 'jsdom',
    setupFiles: ['src/setupTests.ts'],
  },
})
