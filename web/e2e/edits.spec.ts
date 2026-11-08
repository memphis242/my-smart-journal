/**
 * Concurrent edits, drafts per entry, deletes against stale copies, and
 * sign-out failures. "Another device" is simulated by calling the in-page mock
 * server directly (window.__msjMockFetch, mock builds only).
 */
import type { Page } from '@playwright/test';
import { expect, openApp, rows, test } from './fixtures';

async function rowId(page: Page, index: number): Promise<string> {
  const id = await rows(page).nth(index).getAttribute('data-row-id');
  if (id === null) throw new Error(`row ${index} has no id`);
  return id;
}

/** Saves a change to an entry as another device would: unconditionally, through the server. */
async function patchElsewhere(page: Page, id: string, patch: Record<string, unknown>): Promise<void> {
  await page.evaluate(
    async ({ entryId, body }) => {
      const send = (window as unknown as { __msjMockFetch: (url: string, init: RequestInit) => Promise<Response> }).__msjMockFetch;
      const response = await send(`/api/v1/entries/${entryId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'X-MSJ-CSRF': '1' },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(`PATCH answered ${response.status}`);
    },
    { entryId: id, body: patch },
  );
}

async function openEditor(page: Page, id: string): Promise<void> {
  await page.locator(`[data-row-id="${id}"]`).click();
  await expect(page.getByTestId('modal-edit')).toBeEnabled();
  await page.getByTestId('modal-edit').click();
  await expect(page.getByTestId('entry-editor')).toBeVisible();
}

test.describe('concurrent edits and drafts', () => {
  test('saving over a newer version keeps the editor and reapplies only my changes', async ({ page }) => {
    await openApp(page);
    const id = await rowId(page, 3);
    await openEditor(page, id);
    await page.getByTestId('entry-title').fill('My title');
    await patchElsewhere(page, id, { text: 'Edited on the phone meanwhile.' });
    await page.getByTestId('entry-submit').click();
    const editor = page.getByTestId('entry-editor');
    await expect(editor.getByTestId('entry-changed')).toBeVisible();
    await expect(editor.getByTestId('entry-title')).toHaveValue('My title');
    await expect(editor.getByTestId('entry-submit')).toBeDisabled();
    await editor.getByTestId('reapply').click();
    await expect(editor.getByTestId('entry-text-input')).toHaveValue('Edited on the phone meanwhile.');
    await expect(editor.getByTestId('entry-title')).toHaveValue('My title');
    await editor.getByTestId('entry-submit').click();
    await expect(editor).toBeHidden();
    await expect(page.getByTestId('modal-title')).toHaveText('My title');
  });

  test('a draft from an older version asks first, then applies only the drafted fields', async ({ page }) => {
    await openApp(page);
    const id = await rowId(page, 3);
    await openEditor(page, id);
    await page.getByTestId('entry-title').fill('Drafted title');
    // The page goes away with the draft unsaved; the mock server restarts with the seed data.
    await page.reload();
    await expect(rows(page).first()).toBeVisible();
    await patchElsewhere(page, id, { text: 'Text changed on another device.' });
    await openEditor(page, id);
    const editor = page.getByTestId('entry-editor');
    await expect(editor.getByTestId('draft-conflict')).toBeVisible();
    await expect(editor.getByTestId('entry-title')).not.toHaveValue('Drafted title');
    await editor.getByTestId('apply-draft').click();
    await expect(editor.getByTestId('entry-title')).toHaveValue('Drafted title');
    await expect(editor.getByTestId('entry-text-input')).toHaveValue('Text changed on another device.');
    await editor.getByTestId('entry-submit').click();
    await expect(editor).toBeHidden();
    await expect(page.getByTestId('modal-title')).toHaveText('Drafted title');
  });

  test('each entry keeps its own draft', async ({ page }) => {
    await openApp(page);
    const first = await rowId(page, 3);
    const second = await rowId(page, 4);
    await openEditor(page, first);
    await page.getByTestId('entry-title').fill('First draft');
    await page.reload();
    await expect(rows(page).first()).toBeVisible();
    await openEditor(page, second);
    await page.getByTestId('entry-title').fill('Second draft');
    await page.reload();
    await expect(rows(page).first()).toBeVisible();
    await openEditor(page, first);
    await expect(page.getByTestId('entry-title')).toHaveValue('First draft');
    await page.reload();
    await expect(rows(page).first()).toBeVisible();
    await openEditor(page, second);
    await expect(page.getByTestId('entry-title')).toHaveValue('Second draft');
  });

  test('an edit survives a lost session and a new sign-in', async ({ page }) => {
    await openApp(page);
    const id = await rowId(page, 3);
    await openEditor(page, id);
    await page.getByTestId('entry-title').fill('Kept across a 401');
    // The server forgets the session; saving is answered with 401.
    await page.evaluate(() => {
      window.sessionStorage.setItem('msj.mock.session', '0');
    });
    await page.getByTestId('entry-submit').click();
    await expect(page.getByTestId('sign-in-dialog')).toBeVisible();
    await page.getByTestId('sign-in-token').fill('a-good-token');
    await page.getByTestId('sign-in-submit').click();
    await expect(page.getByTestId('sign-in-dialog')).toBeHidden();
    await openEditor(page, id);
    await expect(page.getByTestId('entry-title')).toHaveValue('Kept across a 401');
  });
});

test.describe('deletes and sign-out against stale state', () => {
  test('delete re-reads the entry, so a cached copy cannot hide the compilations it removes', async ({ page }) => {
    await openApp(page, { params: 'mock-latency=900' });
    const target = await rowId(page, 0);
    const other = await rowId(page, 1);
    // Load the entry once (no compilation cites it yet), then close it: a cached copy remains.
    await page.locator(`[data-row-id="${target}"]`).click();
    await expect(page.getByTestId('modal-delete')).toBeEnabled({ timeout: 8000 });
    await page.keyboard.press('Escape');
    await page.locator(`[data-row-id="${target}"]`).getByTestId('row-select').click();
    await page.locator(`[data-row-id="${other}"]`).getByTestId('row-select').click();
    await page.getByTestId('open-compile').click();
    await page.getByTestId('compile-submit').click();
    await expect(page.getByTestId('compile-dialog')).toBeHidden({ timeout: 8000 });
    // Reopened: the cached copy shows at once, but Delete waits for the current entry.
    await page.locator(`[data-row-id="${target}"]`).click();
    await expect(page.getByTestId('modal-delete')).toBeDisabled();
    await expect(page.getByTestId('modal-delete')).toBeEnabled({ timeout: 8000 });
    await page.getByTestId('modal-delete').click();
    await expect(page.getByTestId('confirm-dialog').getByTestId('delete-compilations')).toBeVisible({ timeout: 8000 });
  });

  test('deleting from search results removes the entry from the results and the selection', async ({ page }) => {
    await openApp(page, { params: 'range=all' });
    await page.getByTestId('search-input').fill('Priya');
    const results = page.getByTestId('search-results');
    await expect(results.getByTestId('entry-row').first()).toBeVisible();
    const id = await results.getByTestId('entry-row').first().getAttribute('data-row-id');
    if (id === null) throw new Error('result row has no id');
    await results.getByTestId('entry-row').first().getByTestId('row-select').click();
    await expect(page.getByTestId('selection-bar')).toBeVisible();
    await results.getByTestId('entry-row').first().click();
    await expect(page.getByTestId('modal-delete')).toBeEnabled();
    await page.getByTestId('modal-delete').click();
    await page.getByTestId('confirm-action').click();
    await page.keyboard.press('Escape');
    await expect(results.locator(`[data-row-id="${id}"]`)).toHaveCount(0);
    await expect(page.getByTestId('selection-bar')).toBeHidden();
  });

  test('sign-out clears drafts and the selection even when the server fails, and says why', async ({ page }) => {
    await openApp(page, { params: 'mock-signout=fail' });
    await rows(page).first().getByTestId('row-select').click();
    await page.getByTestId('new-entry').click();
    await page.getByTestId('entry-text-input').fill('Must not outlive sign-out');
    await page.keyboard.press('Escape');
    await page.getByTestId('app-menu-button').click();
    await page.getByTestId('sign-out').click();
    await expect(page.locator('#app-menu').getByTestId('error-code')).toHaveText('INTERNAL_ERROR');
    await expect(page.getByTestId('selection-bar')).toBeHidden();
    await expect(page.getByTestId('sign-in-dialog')).toBeHidden();
    await page.keyboard.press('Escape');
    await page.getByTestId('new-entry').click();
    await expect(page.getByTestId('entry-text-input')).toHaveValue('');
  });
});
