/// <reference types="vitest/config" />
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const proxyTarget = 'http://127.0.0.1:8600'
const proxyPaths = [
  '/cameras',
  '/videos',
  '/events',
  '/training',
  '/models',
  '/health',
  '/docs',
  '/redoc',
  '/openapi.json',
  '/api',
]

export default defineConfig({
  base: '/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    proxy: Object.fromEntries(
      proxyPaths.map((prefix) => [
        prefix,
        { target: proxyTarget, changeOrigin: true },
      ]),
    ),
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
})
