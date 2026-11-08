import { chooseRange, expect, isPhone, openApp, rows, test } from './fixtures';

test.describe('entry list and detail modal', () => {
  test('ranges narrow the list', async ({ page }) => {
    await openApp(page, { params: 'range=all' });
    await expect(rows(page).first()).toBeVisible();
    const all = await rows(page).count();
    expect(all).toBeGreaterThan(10);
    await chooseRange(page, 'today');
    await expect.poll(() => rows(page).count()).toBeLessThan(all);
    expect(await rows(page).count()).toBeGreaterThan(0);
  });

  test('bars, arrow keys and swipes step between newer and older entries', async ({ page }) => {
    await openApp(page);
    await rows(page).nth(1).click();
    const modal = page.getByTestId('entry-modal');
    const sheet = modal.locator('.entry-sheet');
    await expect(modal).toBeVisible();
    const first = await sheet.getAttribute('data-entry-id');

    await page.getByTestId('nav-older').click();
    await expect(sheet).not.toHaveAttribute('data-entry-id', first ?? '');
    const older = await sheet.getAttribute('data-entry-id');

    await page.keyboard.press('ArrowRight');
    await expect(sheet).toHaveAttribute('data-entry-id', first ?? '');
    await page.keyboard.press('ArrowLeft');
    await expect(sheet).toHaveAttribute('data-entry-id', older ?? '');

    // A touch swipe from right to left brings the newer entry.
    await sheet.dispatchEvent('pointerdown', { pointerType: 'touch', pointerId: 7, clientX: 300, clientY: 400, isPrimary: true });
    await sheet.dispatchEvent('pointerup', { pointerType: 'touch', pointerId: 7, clientX: 120, clientY: 410, isPrimary: true });
    await expect(sheet).toHaveAttribute('data-entry-id', first ?? '');

    await page.keyboard.press('Escape');
    await expect(modal).toBeHidden();
  });

  test('the newest entry has no newer neighbour', async ({ page }) => {
    await openApp(page, { params: 'range=all' });
    await rows(page).first().click();
    await expect(page.getByTestId('nav-newer')).toBeDisabled();
    await expect(page.getByTestId('nav-older')).toBeEnabled();
  });

  test('stepping past the loaded page asks the server for neighbours', async ({ page }) => {
    // Oldest first: the last loaded row of an "all" list still has newer entries on the server.
    await openApp(page, { params: 'range=all&order=asc' });
    await rows(page).first().click();
    await expect(page.getByTestId('nav-older')).toBeDisabled();
    await expect(page.getByTestId('nav-newer')).toBeEnabled();
  });

  test('copies an entry from its row', async ({ page }) => {
    await openApp(page);
    await rows(page).first().getByTestId('row-copy').click();
    await expect(page.getByTestId('toast')).toBeVisible();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toMatch(/^\d{4}-\d{2}-\d{2}/);
  });

  test('a confirmation raised inside the modal shows above it', async ({ page }) => {
    await openApp(page);
    await rows(page).nth(2).click();
    await page.getByTestId('modal-copy').click();
    const toast = page.getByTestId('toast');
    await expect(toast).toBeVisible();
    // Shown after the dialog, the toast popover is above it in the top layer. (Hit-testing cannot
    // check this: everything outside a modal dialog is inert, and inert nodes are skipped.)
    const inTopLayer = await toast.evaluate((element) => element.closest('[popover]')?.matches(':popover-open') === true);
    expect(inTopLayer).toBe(true);
  });

  test('edits an entry title in a modal', async ({ page }) => {
    await openApp(page);
    await rows(page).nth(3).click();
    await page.getByTestId('modal-edit').click();
    const editor = page.getByTestId('entry-editor');
    await editor.getByTestId('entry-title').fill('Edited in a test');
    await editor.getByTestId('entry-submit').click();
    await expect(editor).toBeHidden();
    await expect(page.getByTestId('modal-title')).toHaveText('Edited in a test');
  });

  test('deletes an entry after its own confirmation', async ({ page }) => {
    await openApp(page, { params: 'range=all' });
    const before = await rows(page).count();
    await rows(page).nth(2).click();
    await page.getByTestId('modal-delete').click();
    await expect(page.getByTestId('confirm-dialog')).toBeVisible();
    await page.getByTestId('confirm-action').click();
    await page.keyboard.press('Escape');
    await expect.poll(() => rows(page).count()).toBe(before - 1);
  });

  test('filters by a project facet', async ({ page }) => {
    await openApp(page, { params: 'range=all' });
    const all = await rows(page).count();
    if (isPhone(page)) {
      await page.getByTestId('filter-all').click();
      await page.locator('#facet-all .facet-panel').nth(1).getByTestId('facet-option').first().click();
    } else {
      await page.getByTestId('filter-projects').click();
      await page.locator('#facet-projects').getByTestId('facet-option').first().click();
    }
    await page.keyboard.press('Escape');
    await expect.poll(() => rows(page).count()).toBeLessThan(all);
    await page.getByTestId('clear-filters').click();
    await expect.poll(() => rows(page).count()).toBe(all);
  });
});
