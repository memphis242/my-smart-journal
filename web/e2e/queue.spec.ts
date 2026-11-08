import { expect, openApp, test } from './fixtures';

test.describe('processing queue', () => {
  test('shows the failed job with its error and retries it', async ({ page }) => {
    await openApp(page);
    await page.getByTestId('queue-indicator').click();
    const sheet = page.getByTestId('queue-sheet');
    await expect(sheet).toBeVisible();
    const failed = sheet.locator('[data-testid=queue-job][data-status=failed]');
    await expect(failed).toHaveCount(1);
    await expect(failed.locator('.job-error-text')).toBeVisible();
    await failed.getByTestId('retry-job').click();
    await expect(sheet.locator('[data-testid=queue-job][data-status=failed]')).toHaveCount(0);
  });

  test('runs a requested batch until nothing is waiting', async ({ page }) => {
    await openApp(page);
    await page.getByTestId('queue-indicator').click();
    const sheet = page.getByTestId('queue-sheet');
    const jobs = sheet.getByTestId('queue-job');
    await expect(jobs.first()).toBeVisible();
    const before = await jobs.count();
    await sheet.getByTestId('sheet-run-batch').click();
    // The worker picks the request up and runs every ready job; only failed, blocked and backed-off jobs stay.
    await expect.poll(() => jobs.count(), { timeout: 15_000 }).toBeLessThan(before - 8);
    await expect(sheet.getByTestId('sheet-run-batch')).toBeEnabled({ timeout: 15_000 });
  });

  test('warns when the worker is not running', async ({ page }) => {
    await openApp(page, { params: 'mock-worker=stale' });
    await page.getByTestId('queue-indicator').click();
    const sheet = page.getByTestId('queue-sheet');
    await expect(sheet.getByTestId('worker-stale')).toBeVisible();
    await sheet.getByTestId('sheet-run-batch').click();
    await expect(sheet.getByTestId('error-code').filter({ hasText: 'WORKER_NOT_RUNNING' })).toBeVisible();
  });

  test('shows the last batch failure with its code', async ({ page }) => {
    await openApp(page, { params: 'mock-batch-error=1' });
    await page.getByTestId('queue-indicator').click();
    await expect(page.getByTestId('queue-sheet').getByTestId('error-code').filter({ hasText: 'BATCH_FAILED' })).toBeVisible();
  });
});
