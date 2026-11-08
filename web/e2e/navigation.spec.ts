import { expect, openApp, rows, test } from './fixtures';

test.describe('navigation and session', () => {
  test('an evidence date opens that day in Entries, survives a reload, and Back returns', async ({ page }) => {
    await openApp(page, { path: '/compilations' });
    await page.getByTestId('compilation-row').first().click();
    await page.locator('.evidence-date').first().click();
    await expect(page.getByTestId('entries-page')).toBeVisible();
    await expect(page).toHaveURL(/range=custom/);
    const shown = await rows(page).count();
    expect(shown).toBeGreaterThan(0);
    await page.reload();
    await expect(rows(page).first()).toBeVisible();
    expect(await rows(page).count()).toBe(shown);
    await page.goBack();
    await expect(page.getByTestId('compilations-page')).toBeVisible();
  });

  test('Back and Forward restore the entries view, not just the page', async ({ page }) => {
    await openApp(page, { params: 'range=week' });
    const week = await rows(page).count();
    await page.getByTestId('nav-compilations').click();
    await expect(page.getByTestId('compilations-page')).toBeVisible();
    await page.getByTestId('nav-entries').click();
    await expect(page).toHaveURL(/range=week/);
    await page.goBack();
    await page.goForward();
    await expect(rows(page).first()).toBeVisible();
    expect(await rows(page).count()).toBe(week);
  });

  test('signing out clears drafts and the selection', async ({ page }) => {
    await openApp(page);
    await rows(page).first().getByTestId('row-select').click();
    await page.getByTestId('new-entry').click();
    await page.getByTestId('entry-text-input').fill('A draft that must not outlive the session');
    await page.keyboard.press('Escape');
    await page.getByTestId('app-menu-button').click();
    await page.getByTestId('sign-out').click();
    await page.getByTestId('sign-in-token').fill('a-good-token');
    await page.getByTestId('sign-in-submit').click();
    await expect(rows(page).first()).toBeVisible();
    await expect(page.getByTestId('selection-bar')).toBeHidden();
    await page.getByTestId('new-entry').click();
    await expect(page.getByTestId('entry-text-input')).toHaveValue('');
  });

  test('the rows are one tab stop with arrow-key movement', async ({ page }) => {
    await openApp(page);
    await rows(page).first().focus();
    await page.keyboard.press('ArrowDown');
    await expect(rows(page).nth(1)).toBeFocused();
    await expect(rows(page).nth(1)).toHaveAttribute('tabindex', '0');
    await expect(rows(page).first()).toHaveAttribute('tabindex', '-1');
    await page.keyboard.press('Space');
    await expect(rows(page).nth(1)).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('entry-modal')).toBeVisible();
    // Focus starts on the entry itself, not on the Older bar.
    await expect(page.getByTestId('entry-modal').locator('.entry-sheet')).toBeFocused();
  });
});
