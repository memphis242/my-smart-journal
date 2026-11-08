/**
 * While a batch runs, the queue is polled every few seconds and every change
 * refreshes the data. Open editors, reading positions and search results must
 * survive those refreshes.
 */
import type { Page } from '@playwright/test';
import { expect, openApp, rows, startBatch, test } from './fixtures';

/**
 * Samples a condition in the page every 50 ms for `ms` and returns how many
 * samples failed it, so a brief blank or remount between two checks is caught.
 */
async function failuresDuring(page: Page, ms: number, condition: string): Promise<number> {
  return page.evaluate(
    async ({ duration, source }) => {
      // The condition is test code, written in this file.
      // eslint-disable-next-line @typescript-eslint/no-implied-eval
      const check = new Function(`return (${source});`) as () => boolean;
      let failures = 0;
      const end = performance.now() + duration;
      while (performance.now() < end) {
        if (!check()) failures += 1;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      return failures;
    },
    { duration: ms, source: condition },
  );
}

/** Long enough that several queue polls (every 2.5 s while busy) land during the test. */
const SLOW_BATCH = 'mock-job-ms=350';

test.describe('refreshes during a running batch', () => {
  test('an open edit keeps its text', async ({ page }) => {
    await openApp(page, { params: SLOW_BATCH });
    await startBatch(page);
    await rows(page).nth(5).click();
    await page.getByTestId('modal-edit').click();
    const editor = page.getByTestId('entry-editor');
    const field = editor.getByTestId('entry-text-input');
    await field.press('End');
    await field.pressSequentially(' Added while the batch ran.');
    const typed = await field.inputValue();
    await expect(page.getByTestId('queue-indicator').locator('[data-marker=progress]')).toBeVisible();
    // Mark the field: a remount would replace the element (and drop the caret) even if a draft refills it.
    await field.evaluate((element) => {
      element.setAttribute('data-probe', 'original');
    });
    const broken = await failuresDuring(
      page,
      6000,
      "document.querySelector('[data-testid=entry-text-input]')?.getAttribute('data-probe') === 'original' && document.activeElement?.getAttribute('data-probe') === 'original'",
    );
    expect(broken, 'samples where the editor was gone, replaced or unfocused').toBe(0);
    await expect(field).toHaveValue(typed);
  });

  test('the reading position in an entry stays put', async ({ page }) => {
    await openApp(page, { params: SLOW_BATCH });
    await startBatch(page);
    await rows(page).nth(9).click();
    const scroller = page.getByTestId('entry-modal').locator('.entry-scroll');
    await expect(page.getByTestId('entry-text')).toBeVisible();
    await scroller.evaluate((element) => {
      element.scrollTop = 160;
    });
    const before = await scroller.evaluate((element) => element.scrollTop);
    expect(before).toBeGreaterThan(100);
    await page.waitForTimeout(6000);
    expect(await scroller.evaluate((element) => element.scrollTop)).toBe(before);
    await expect(page.getByTestId('entry-text')).toBeVisible();
  });

  test('search results stay and complete', async ({ page }) => {
    await openApp(page, { params: `${SLOW_BATCH}&range=all` });
    await startBatch(page);
    await page.getByTestId('search-input').fill('database');
    const results = page.getByTestId('search-results');
    await expect(results.locator('[data-testid=tier-progress] [data-state=done]')).toHaveCount(5);
    const groups = await results.getByTestId('tier-group').count();
    const hits = await results.getByTestId('entry-row').count();
    expect(hits).toBeGreaterThan(0);
    const blank = await failuresDuring(
      page,
      6000,
      `document.querySelectorAll('[data-testid=search-results] [data-testid=entry-row]').length === ${hits} && document.querySelectorAll('[data-testid=tier-progress] [data-state=done]').length === 5`,
    );
    expect(blank, 'samples where the results were blank, partial or restarting').toBe(0);
    await expect(results.locator('[data-testid=tier-progress] [data-state=done]')).toHaveCount(5);
    expect(await results.getByTestId('tier-group').count()).toBe(groups);
    expect(await results.getByTestId('entry-row').count()).toBe(hits);
  });

  test('an edit survives a reload and is offered again', async ({ page }) => {
    await openApp(page);
    await rows(page).nth(3).click();
    await page.getByTestId('modal-edit').click();
    await page.getByTestId('entry-title').fill('Kept across a lost session');
    await page.reload();
    await expect(rows(page).first()).toBeVisible();
    await rows(page).nth(3).click();
    await page.getByTestId('modal-edit').click();
    await expect(page.getByTestId('entry-title')).toHaveValue('Kept across a lost session');
  });
});
