/**
 * The GUI sweep at 360 px against the real server, with the CSP the backend
 * sends: scrolling never moves backward, the window never scrolls, nothing is
 * wider than the phone, sheets and modals leave the list its width, and both
 * themes hold. Any console error fails.
 */
import type { Page } from '@playwright/test';
import { expect, noHorizontalScroll, signInQuietly, test } from './fixtures';

async function listWidth(page: Page): Promise<number> {
  return page.getByTestId('list-scroll').evaluate((element) => element.getBoundingClientRect().width);
}

async function open(page: Page, query = ''): Promise<void> {
  await signInQuietly(page);
  await page.goto(`/${query}`);
  await expect(page.getByTestId('entry-row').first()).toBeVisible();
}

test('wheeling through the list never moves backward and the bars stay pinned', async ({ page }) => {
  await open(page, '?range=all');
  await page.getByTestId('density-summaries').click();
  const scroller = page.getByTestId('list-scroll');
  const box = await scroller.boundingBox();
  if (box === null) throw new Error('list scroller has no box');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  const barTop = await page.getByTestId('topbar').evaluate((element) => element.getBoundingClientRect().top);
  let previous = 0;
  let stalled = 0;
  for (let step = 0; step < 150 && stalled < 4; step += 1) {
    await page.mouse.wheel(0, 260);
    await page.waitForTimeout(40);
    const position = await scroller.evaluate((element) => element.scrollTop);
    expect(position, `scroll moved backward at step ${step}`).toBeGreaterThanOrEqual(previous);
    expect(await page.evaluate(() => window.scrollY), 'the window never scrolls').toBe(0);
    stalled = position === previous ? stalled + 1 : 0;
    previous = position;
  }
  expect(previous).toBeGreaterThan(0);
  expect(await page.getByTestId('topbar').evaluate((element) => element.getBoundingClientRect().top)).toBe(barTop);
  await noHorizontalScroll(page);
});

test('every view fits the phone in both themes, and overlays keep the list width', async ({ page }) => {
  for (const theme of ['light', 'dark'] as const) {
    await open(page, '?range=all');
    if ((await page.locator('html').getAttribute('data-theme')) !== theme) await page.getByTestId('theme-switch').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    const width = await listWidth(page);
    await noHorizontalScroll(page);

    await page.getByTestId('entry-row').first().getByTestId('row-select').click();
    await expect(page.getByTestId('selection-bar')).toBeVisible();
    await noHorizontalScroll(page);
    await page.getByTestId('open-export').click();
    await expect(page.getByTestId('export-preview')).not.toHaveValue('');
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    await page.getByTestId('clear-selection').click();

    await page.getByTestId('entry-row').nth(1).click();
    await expect(page.getByTestId('entry-modal')).toBeVisible();
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    expect(await listWidth(page)).toBe(width);

    await page.getByTestId('new-entry').click();
    await page.getByTestId('mode-record').click();
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    expect(await listWidth(page)).toBe(width);

    await page.getByTestId('queue-indicator').click();
    await expect(page.getByTestId('queue-sheet')).toBeVisible();
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    expect(await listWidth(page)).toBe(width);

    await page.getByTestId('filter-all').click();
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    expect(await listWidth(page)).toBe(width);

    await page.getByTestId('search-input').fill('retry queue');
    await expect(page.locator('[data-testid=tier-progress] [data-state=done]')).toHaveCount(5);
    await noHorizontalScroll(page);
    await page.getByTestId('mode-relevance').click();
    await expect(page.getByTestId('relevance-results').getByTestId('entry-row').first()).toBeVisible();
    await noHorizontalScroll(page);

    await page.getByTestId('nav-compilations').click();
    await expect(page.getByTestId('compilations-page')).toBeVisible();
    await noHorizontalScroll(page);
    if ((await page.getByTestId('compilation-row').count()) > 0) {
      await page.getByTestId('compilation-row').first().click();
      await noHorizontalScroll(page);
      await page.keyboard.press('Escape');
    }
  }
});

test('the theme switch shows the mode it switches to and applies before first paint on reload', async ({ page }) => {
  await open(page);
  const html = page.locator('html');
  const start = (await html.getAttribute('data-theme')) ?? 'light';
  const other = start === 'dark' ? 'light' : 'dark';
  await expect(page.getByTestId('theme-switch')).toHaveAttribute('data-next-theme', other);
  await page.getByTestId('theme-switch').click();
  await expect(html).toHaveAttribute('data-theme', other);
  const seen: string[] = [];
  await page.exposeFunction('reportTheme', (value: string) => seen.push(value));
  await page.addInitScript(() => {
    document.addEventListener('readystatechange', () => {
      if (document.readyState === 'interactive') {
        void (window as unknown as { reportTheme: (v: string) => Promise<void> }).reportTheme(document.documentElement.getAttribute('data-theme') ?? 'none');
      }
    });
  });
  await page.reload();
  await expect(html).toHaveAttribute('data-theme', other);
  await expect.poll(() => seen[0]).toBe(other);
});
