import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';
import { buildRoot } from './scripts/build-root.ts';

const OUT = buildRoot();
const PORT = 15174;

/**
 * End-to-end tests run against the production bundle built in mock mode and
 * served with the backend's CSP, so CSP regressions fail here too.
 */
export default defineConfig({
  testDir: './e2e',
  outputDir: path.join(OUT, 'test-results'),
  fullyParallel: true,
  workers: process.env.CI === undefined ? 12 : 4,
  forbidOnly: process.env.CI !== undefined,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 6_000 },
  reporter: [['list'], ['html', { outputFolder: path.join(OUT, 'playwright-report'), open: 'never' }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `pnpm build:mock && node scripts/serve-static.mjs --dir ${path.join(OUT, 'dist-mock')} --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: process.env.CI === undefined,
    timeout: 120_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
  projects: [
    {
      name: 'desktop',
      testIgnore: /screenshots\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, permissions: ['clipboard-read', 'clipboard-write'] },
    },
    {
      name: 'phone',
      testIgnore: /screenshots\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 360, height: 740 },
        isMobile: true,
        hasTouch: true,
        permissions: ['clipboard-read', 'clipboard-write'],
      },
    },
    {
      name: 'screenshots',
      testMatch: /screenshots\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
