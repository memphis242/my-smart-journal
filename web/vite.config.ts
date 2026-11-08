import path from 'node:path';
import preact from '@preact/preset-vite';
import { defineConfig } from 'vitest/config';
import { buildRoot } from './scripts/build-root.ts';

const API_TARGET = process.env.MSJ_API_TARGET ?? 'http://127.0.0.1:18765';

export default defineConfig(({ mode }) => ({
  plugins: [preact()],
  cacheDir: path.join(buildRoot(), '.vite'),
  build: {
    outDir: path.join(buildRoot(), mode === 'mock' ? 'dist-mock' : 'dist'),
    emptyOutDir: true,
    target: 'es2022',
    // Fonts and icons stay separate files: the CSP allows data: only for images.
    assetsInlineLimit: 0,
    sourcemap: true,
  },
  server: {
    port: 15173,
    strictPort: true,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: false },
    },
  },
  preview: {
    port: 15175,
    strictPort: true,
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
}));
