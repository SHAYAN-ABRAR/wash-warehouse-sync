/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { readFileSync } from 'node:fs'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8'))
const target = process.env.VITE_DEPLOY_TARGET ?? 'pages'

export default defineConfig({
  base: process.env.VITE_BASE ?? '/',
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      '@shared': path.resolve(__dirname, '../../packages/sync_core/wash_sync/resources'),
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __DEPLOY_TARGET__: JSON.stringify(target),
  },
  server: {
    fs: { allow: [path.resolve(__dirname, '../..')] },
    proxy: target === 'local' ? { '/api': 'http://127.0.0.1:8765' } : undefined,
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts'],
          table: ['@tanstack/react-table', '@tanstack/react-virtual'],
        },
      },
    },
  },
  worker: { format: 'es' },
  test: {
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
    include: ['tests/**/*.test.{ts,tsx}'],
    css: false,
  },
})
