import { expect, openApp, rows, test } from './fixtures';

test.describe('new entries', () => {
  test('adds a written entry, which appears queued', async ({ page }) => {
    await openApp(page);
    const before = await rows(page).count();
    await page.getByTestId('new-entry').click();
    const editor = page.getByTestId('entry-editor');
    await editor.getByTestId('entry-title').fill('A test entry');
    await editor.getByTestId('tag-input').fill('work,');
    await editor.getByTestId('entry-text-input').fill('Paired on the retry queue and wrote down the open questions.');
    await editor.getByTestId('entry-submit').click();
    await expect(editor).toBeHidden();
    await expect.poll(() => rows(page).count()).toBe(before + 1);
    await expect(rows(page).first()).toHaveAttribute('data-status', 'queued');
  });

  test('keeps an unsent draft after the dialog closes', async ({ page }) => {
    await openApp(page);
    await page.getByTestId('new-entry').click();
    await page.getByTestId('entry-text-input').fill('Half a thought');
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('entry-editor')).toBeHidden();
    await page.getByTestId('new-entry').click();
    await expect(page.getByTestId('entry-text-input')).toHaveValue('Half a thought');
  });

  test('uploads an audio file instead of recording', async ({ page }) => {
    await openApp(page);
    const before = await rows(page).count();
    await page.getByTestId('new-entry').click();
    await page.getByTestId('mode-record').click();
    await page.getByTestId('audio-file').setInputFiles({ name: 'note.webm', mimeType: 'audio/webm', buffer: Buffer.from('fake opus bytes') });
    await page.getByTestId('entry-submit').click();
    await expect(page.getByTestId('entry-editor')).toBeHidden();
    await expect.poll(() => rows(page).count()).toBe(before + 1);
  });
});
