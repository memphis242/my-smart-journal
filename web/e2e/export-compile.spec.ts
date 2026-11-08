import { expect, openApp, rows, test } from './fixtures';

test.describe('export and compile', () => {
  test('copies the plain-text export to the clipboard', async ({ page }) => {
    await openApp(page);
    await rows(page).nth(4).getByTestId('row-select').click();
    await rows(page).nth(6).getByTestId('row-select').click({ modifiers: ['Shift'] });
    await expect(page.getByTestId('selection-bar')).toBeVisible();
    await page.getByTestId('open-export').click();
    const preview = page.getByTestId('export-preview');
    await expect(preview).not.toHaveValue('');
    const text = await preview.inputValue();
    expect(text).not.toMatch(/^#|\*\*/m);
    await page.getByTestId('export-copy').click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(text);
  });

  test('a failed batch request after queueing a compilation is reported on its own, without a duplicate', async ({ page }) => {
    await openApp(page, { params: 'mock-worker=stale' });
    await rows(page).nth(4).getByTestId('row-select').click();
    await rows(page).nth(5).getByTestId('row-select').click();
    await page.getByTestId('open-compile').click();
    await page.getByTestId('compile-submit').click();
    // The compilation exists: the dialog closes and the selection is cleared.
    await expect(page.getByTestId('compile-dialog')).toBeHidden();
    await expect(page.getByTestId('selection-bar')).toBeHidden();
    // The batch request's own failure opens the queue sheet with its code.
    const sheet = page.getByTestId('queue-sheet');
    await expect(sheet).toBeVisible();
    await expect(sheet.getByTestId('error-code').filter({ hasText: 'WORKER_NOT_RUNNING' })).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByTestId('nav-compilations').click();
    await expect(page.getByTestId('compilation-row')).toHaveCount(3);
    await expect(page.locator('[data-testid=compilation-row][data-status=queued]')).toHaveCount(1);
  });

  test('queues a compilation and shows its result when the batch is done', async ({ page }) => {
    await openApp(page);
    await rows(page).nth(4).getByTestId('row-select').click();
    await rows(page).nth(5).getByTestId('row-select').click();
    await page.getByTestId('open-compile').click();
    await page.getByTestId('compile-submit').click();
    await expect(page.getByTestId('compile-dialog')).toBeHidden();
    await page.getByTestId('nav-compilations').click();
    const newest = page.getByTestId('compilation-row').first();
    await expect(newest).toHaveAttribute('data-status', 'done', { timeout: 15_000 });
    await newest.click();
    await expect(page.getByTestId('compilation-dialog')).toBeVisible();
    await page.getByTestId('compile-tab-threads').click();
    await expect(page.getByTestId('compilation-copy-all')).toBeEnabled();
  });
});
