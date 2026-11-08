import { test as base, expect, type APIRequestContext, type ConsoleMessage, type Page } from '@playwright/test';

export function token(): string {
  const value = process.env.MSJ_E2E_TOKEN;
  if (value === undefined || value === '') throw new Error('global setup did not provide MSJ_E2E_TOKEN');
  return value;
}

/**
 * Console errors fail the test. The one expected message is Chrome's network
 * log of the sign-in probe: GET /api/v1/session answers 401 by contract when
 * the browser has no session.
 */
export interface ExpectedFailure {
  readonly status: number;
  /** Matches the request path, for example /\/api\/v1\/entries\/[^/]+$/. */
  readonly path: RegExp;
}

export function watchConsole(page: Page, expected: readonly ExpectedFailure[] = []): string[] {
  const errors: string[] = [];
  page.on('console', (message: ConsoleMessage) => {
    if (message.type() !== 'error') return;
    const url = message.location().url;
    const failedLoad = message.text().startsWith('Failed to load resource');
    if (failedLoad && message.text().includes('401') && url.endsWith('/api/v1/session')) return;
    // Failures a test provokes on purpose (a revoked session, a version conflict), declared by that test.
    if (failedLoad && expected.some((item) => message.text().includes(`status of ${item.status}`) && item.path.test(new URL(url).pathname))) return;
    errors.push(`${message.text()} (${url})`);
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  return errors;
}

export function expectNoConsoleErrors(errors: readonly string[]): void {
  expect(errors, 'console errors (CSP violations and failed requests show up here)').toEqual([]);
}

/** Signs the page's browser context in through the API (the session cookie is shared with the page). */
export async function signInQuietly(page: Page): Promise<void> {
  const response = await page.request.post('/api/v1/session', { data: { token: token() }, headers: { 'X-MSJ-CSRF': '1' } });
  expect(response.status(), await response.text()).toBe(204);
}

export async function bearer(request: APIRequestContext, path: string): Promise<unknown> {
  const response = await request.get(path, { headers: { Authorization: `Bearer ${token()}` } });
  expect(response.ok(), `GET ${path}: ${response.status()}`).toBe(true);
  return response.json();
}

export async function noHorizontalScroll(page: Page): Promise<void> {
  const device = page.viewportSize()?.width ?? 0;
  const sizes = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
  expect(sizes.scroll, 'page must not scroll sideways').toBeLessThanOrEqual(device);
  expect(sizes.inner, 'the browser must not zoom out to fit over-wide content').toBe(device);
}

/**
 * Every test fails on console errors (see watchConsole). A test that provokes
 * a failing request on purpose declares it with `expectFailure`.
 */
export const test = base.extend<{ expected: ExpectedFailure[]; consoleErrors: string[]; expectFailure: (failure: ExpectedFailure) => void }>({
  expected: [async ({}, use) => { await use([]); }, { scope: 'test' }],
  consoleErrors: [
    async ({ page, expected }, use) => {
      const errors = watchConsole(page, expected);
      await use(errors);
      expectNoConsoleErrors(errors);
    },
    { auto: true },
  ],
  expectFailure: async ({ expected }, use) => {
    await use((failure) => {
      expected.push(failure);
    });
  },
});

export { expect };
