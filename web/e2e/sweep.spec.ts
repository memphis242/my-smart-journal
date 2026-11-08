/**
 * The scripted GUI sweep: scrolling never jumps backward, opening and closing
 * panes keeps the main area's width, nothing scrolls sideways (including at
 * 360 px), pinned bars stay pinned, and both themes apply before first paint.
 */
import type { Page } from '@playwright/test';
import { chooseRange, expect, isPhone, noHorizontalScroll, openApp, rows, test } from './fixtures';

async function listWidth(page: Page): Promise<number> {
  return page.getByTestId('list-scroll').evaluate((element) => element.getBoundingClientRect().width);
}

test.describe('GUI sweep', () => {
  test('wheeling through the list never moves it backward, and bars stay pinned', async ({ page }) => {
    await openApp(page, { params: 'range=all&mock-latency=20' });
    await page.getByTestId('density-summaries').click();
    const scroller = page.getByTestId('list-scroll');
    await expect(rows(page).first()).toBeVisible();
    const box = await scroller.boundingBox();
    if (box === null) throw new Error('list scroller has no box');
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    const topbarTop = await page.getByTestId('topbar').evaluate((el) => el.getBoundingClientRect().top);

    let previous = 0;
    let stalled = 0;
    for (let step = 0; step < 120 && stalled < 4; step += 1) {
      await page.mouse.wheel(0, 240);
      await page.waitForTimeout(30);
      const position = await scroller.evaluate((el) => el.scrollTop);
      const windowY = await page.evaluate(() => window.scrollY);
      expect(position, `scroll moved backward at step ${step}`).toBeGreaterThanOrEqual(previous);
      expect(windowY, 'the window itself never scrolls').toBe(0);
      stalled = position === previous ? stalled + 1 : 0;
      previous = position;
    }
    expect(previous).toBeGreaterThan(0);
    expect(await page.getByTestId('topbar').evaluate((el) => el.getBoundingClientRect().top)).toBe(topbarTop);
    if (!isPhone(page)) {
      // The table heading stays at the top of its scroller.
      const offset = await page.evaluate(() => {
        const header = document.querySelector('[data-testid=list-header]');
        const container = document.querySelector('[data-testid=list-scroll]');
        if (header === null || container === null) return Number.NaN;
        return header.getBoundingClientRect().top - container.getBoundingClientRect().top;
      });
      expect(Math.abs(offset)).toBeLessThanOrEqual(1);
    }
    await noHorizontalScroll(page);
  });

  test('panes, sheets, popovers and modals leave the list its full width', async ({ page }) => {
    await openApp(page);
    const width = await listWidth(page);
    await noHorizontalScroll(page);

    await page.getByTestId('queue-indicator').click();
    await expect(page.getByTestId('queue-sheet')).toBeVisible();
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('queue-sheet')).toBeHidden();
    expect(await listWidth(page)).toBe(width);

    await page.getByTestId(isPhone(page) ? 'filter-all' : 'filter-tags').click();
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    expect(await listWidth(page)).toBe(width);

    await rows(page).first().click();
    await expect(page.getByTestId('entry-modal')).toBeVisible();
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    expect(await listWidth(page)).toBe(width);

    await page.getByTestId('new-entry').click();
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    expect(await listWidth(page)).toBe(width);
  });

  test('no view scrolls sideways', async ({ page }) => {
    await openApp(page, { params: 'range=all' });
    await noHorizontalScroll(page);
    await page.getByTestId('density-summaries').click();
    await noHorizontalScroll(page);
    await chooseRange(page, 'week');
    await page.getByTestId('search-input').fill('Priya');
    await expect(page.getByTestId('tier-group').first()).toBeVisible();
    await noHorizontalScroll(page);
    await page.getByTestId('mode-relevance').click();
    await expect(page.getByTestId('relevance-results').getByTestId('entry-row').first()).toBeVisible();
    await noHorizontalScroll(page);
    await page.getByTestId('relevance-results').getByTestId('row-select').first().click();
    // The selection bar replaces the list head; it must fit too.
    await noHorizontalScroll(page);
    await page.getByTestId('open-export').click();
    await expect(page.getByTestId('export-preview')).not.toHaveValue('');
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    await page.getByTestId('nav-compilations').click();
    await expect(page.getByTestId('compilation-row').first()).toBeVisible();
    await noHorizontalScroll(page);
    await page.getByTestId('compilation-row').first().click();
    await noHorizontalScroll(page);
  });

  test('the theme switch shows the mode it switches to, persists, and applies before first paint', async ({ page }) => {
    await openApp(page);
    const html = page.locator('html');
    const start = await html.getAttribute('data-theme');
    const other = start === 'dark' ? 'light' : 'dark';
    await expect(page.getByTestId('theme-switch')).toHaveAttribute('data-next-theme', other);
    await page.getByTestId('theme-switch').click();
    await expect(html).toHaveAttribute('data-theme', other);
    await expect(page.getByTestId('theme-switch')).toHaveAttribute('data-next-theme', start ?? '');

    // The stored choice is applied by the blocking head script, before the body is parsed.
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

  test('follows the system theme until the user picks one', async ({ browser }) => {
    for (const scheme of ['dark', 'light'] as const) {
      const context = await browser.newContext({ colorScheme: scheme });
      const page = await context.newPage();
      await page.goto('/?mock-signed-in=1&mock-latency=0');
      await expect(page.locator('html')).toHaveAttribute('data-theme', scheme);
      await context.close();
    }
  });
});
