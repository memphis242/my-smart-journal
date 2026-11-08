import { test as base, expect, type Locator, type Page } from '@playwright/test';

/** Mock switches that make the simulated server fast and deterministic for tests. */
export const FAST = 'mock-latency=0&mock-job-ms=40&mock-search-ms=5&mock-pickup-ms=30';

export const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on('console', (message) => {
        if (message.type() === 'error') errors.push(message.text());
      });
      page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
      await use(errors);
      // CSP violations ("Refused to ...") and uncaught errors land here.
      expect(errors, 'console errors during the test').toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

export async function openApp(page: Page, options: { readonly path?: string; readonly params?: string; readonly signedIn?: boolean } = {}): Promise<void> {
  const signedIn = options.signedIn ?? true;
  const query = [signedIn ? 'mock-signed-in=1' : '', FAST, options.params ?? ''].filter((part) => part !== '').join('&');
  await page.goto(`${options.path ?? '/'}?${query}`);
  if (signedIn) await expect(page.getByTestId(options.path === '/compilations' ? 'compilations-page' : 'entries-page')).toBeVisible();
}

export function rows(page: Page): Locator {
  return page.getByTestId('entry-row');
}

export function isPhone(page: Page): boolean {
  return (page.viewportSize()?.width ?? 1440) < 760;
}

export async function chooseRange(page: Page, key: 'today' | 'week' | 'month' | 'year' | 'all'): Promise<void> {
  if (isPhone(page)) {
    await page.getByTestId('range-menu-button').click();
    const index = ['today', 'week', 'month', 'year', 'all'].indexOf(key);
    await page.locator('#range-menu .menu-item').nth(index).click();
  } else {
    await page.getByTestId(`range-${key}`).click();
  }
}

/**
 * Nothing may be wider than the device. Measured against the configured
 * viewport, not window.innerWidth: on phones an over-wide page makes the
 * browser zoom out, which widens innerWidth along with the content.
 */
export async function noHorizontalScroll(page: Page): Promise<void> {
  const device = page.viewportSize()?.width ?? 0;
  const sizes = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
  expect(sizes.scroll, 'page must not scroll sideways').toBeLessThanOrEqual(device);
  expect(sizes.inner, 'the browser must not zoom out to fit over-wide content').toBe(device);
}
