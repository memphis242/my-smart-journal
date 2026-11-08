import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import { buildRoot } from './scripts/build-root.ts';

/**
 * End-to-end tests against a REAL running backend (`msj serve` serving the
 * production build, plus `msj worker`); see README "Run against a local
 * backend". Not part of `pnpm e2e`. The suite shares one database, so it runs
 * serially.
 */
const OUT = buildRoot();

export default defineConfig({
  testDir: './e2e-real',
  outputDir: path.join(OUT, 'test-results-real'),
  globalSetup: './e2e-real/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { outputFolder: path.join(OUT, 'playwright-report-real'), open: 'never' }]],
  use: {
    baseURL: process.env.MSJ_E2E_BASE_URL ?? 'http://127.0.0.1:18765',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    permissions: ['clipboard-read', 'clipboard-write', 'microphone'],
    launchOptions: {
      // A synthetic microphone, so the real MediaRecorder path can be recorded and uploaded.
      args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
    },
  },
  projects: [
    {
      name: 'real-desktop',
      testMatch: /flow\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'real-phone',
      testMatch: /sweep\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 360, height: 740 }, isMobile: true, hasTouch: true },
    },
  ],
});
