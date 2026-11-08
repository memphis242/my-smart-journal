import { expect, openApp, test } from './fixtures';

test.describe('search', () => {
  test('streams keyword tiers and shows similar-meaning passages', async ({ page }) => {
    await openApp(page, { params: 'range=all' });
    await page.getByTestId('search-input').fill('database');
    const results = page.getByTestId('search-results');
    await expect(results.getByTestId('tier-group').first()).toBeVisible();
    await expect(results.locator('[data-testid=tier-progress] [data-state=done]')).toHaveCount(5);
    await expect(results.locator('[data-testid=tier-group][data-tier="5"] .passage').first()).toBeVisible();
    await page.getByTestId('search-clear').click();
    await expect(page.getByTestId('search-results')).toBeHidden();
  });

  test('ranks by relevance and builds a selection to export', async ({ page }) => {
    await openApp(page, { params: 'range=all&mode=relevance' });
    await page.getByTestId('search-input').fill('teaching someone new');
    const results = page.getByTestId('relevance-results');
    await expect(results.getByTestId('entry-row').first()).toBeVisible();
    await results.getByTestId('entry-row').nth(0).getByTestId('row-select').click();
    await results.getByTestId('entry-row').nth(1).getByTestId('row-select').click();
    await page.getByTestId('open-export').click();
    await expect(page.getByTestId('export-preview')).not.toHaveValue('');
  });

  test('reports when semantic search is off', async ({ page }) => {
    await openApp(page, { params: 'range=all&mock-embeddings=disabled' });
    await page.getByTestId('search-input').fill('running');
    await expect(page.getByTestId('semantic-unavailable').locator('.error-code')).toHaveText('EMBEDDINGS_DISABLED');
  });
});
