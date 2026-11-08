/**
 * Screenshots of the main views in both themes, at desktop and phone widths,
 * for design review. Run with `pnpm screenshots`; images go to <build root>/shots.
 */
import path from 'node:path';
import type { Page } from '@playwright/test';
import { buildRoot } from '../scripts/build-root.ts';
import { FAST, expect, test } from './fixtures';

const OUT = path.join(buildRoot(), 'shots');
const SIZES = [
  { name: 'desktop', width: 1440, height: 900, mobile: false },
  { name: 'phone', width: 360, height: 740, mobile: true },
] as const;

async function shot(page: Page, name: string): Promise<void> {
  await page.waitForTimeout(250);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
}

for (const theme of ['light', 'dark'] as const) {
  for (const size of SIZES) {
    test(`views ${theme} ${size.name}`, async ({ browser }) => {
      const context = await browser.newContext({
        viewport: { width: size.width, height: size.height },
        isMobile: size.mobile,
        hasTouch: size.mobile,
        colorScheme: theme,
      });
      const page = await context.newPage();
      const tag = `${size.name}-${theme}`;
      const base = `mock-signed-in=1&${FAST}`;

      await page.goto(`/?${base}`);
      await expect(page.getByTestId('entry-row').first()).toBeVisible();
      await shot(page, `${tag}-01-list`);
      await page.getByTestId('density-summaries').click();
      await shot(page, `${tag}-02-summaries`);
      await page.getByTestId('entry-row').nth(4).click();
      await expect(page.getByTestId('entry-modal')).toBeVisible();
      await shot(page, `${tag}-03-entry`);
      await page.keyboard.press('Escape');

      await page.getByTestId('new-entry').click();
      await shot(page, `${tag}-04-new-entry`);
      await page.keyboard.press('Escape');

      await page.getByTestId('queue-indicator').click();
      await expect(page.getByTestId('queue-sheet')).toBeVisible();
      await shot(page, `${tag}-05-queue`);
      await page.keyboard.press('Escape');

      await page.goto(`/?${base}&range=all`);
      await page.getByTestId('search-input').fill('database');
      await expect(page.locator('[data-testid=tier-progress] [data-state=done]')).toHaveCount(5);
      await shot(page, `${tag}-06-search`);

      await page.goto(`/?${base}&range=all&mode=relevance&q=${encodeURIComponent('teaching someone new')}`);
      await expect(page.getByTestId('relevance-results').getByTestId('entry-row').first()).toBeVisible();
      await page.getByTestId('relevance-results').getByTestId('row-select').nth(0).click();
      await page.getByTestId('relevance-results').getByTestId('row-select').nth(2).click();
      await shot(page, `${tag}-07-relevance-selected`);
      await page.getByTestId('open-export').click();
      await expect(page.getByTestId('export-preview')).not.toHaveValue('');
      await shot(page, `${tag}-08-export`);
      await page.keyboard.press('Escape');

      await page.goto(`/compilations?${base}`);
      await expect(page.getByTestId('compilation-row').first()).toBeVisible();
      await shot(page, `${tag}-09-compilations`);
      await page.getByTestId('compilation-row').first().click();
      await shot(page, `${tag}-10-compilation`);

      // A new tab has its own sessionStorage, so the mock session starts signed out.
      const fresh = await context.newPage();
      await fresh.goto(`/?${FAST}`);
      await expect(fresh.getByTestId('sign-in-dialog')).toBeVisible();
      await shot(fresh, `${tag}-11-sign-in`);
      await context.close();
    });
  }
}
