import { expect, openApp, test } from './fixtures';

test.describe('sign-in', () => {
  test('rejects a wrong token with its code, then signs in', async ({ page }) => {
    await openApp(page, { signedIn: false });
    const dialog = page.getByTestId('sign-in-dialog');
    await expect(dialog).toBeVisible();
    await page.getByTestId('sign-in-token').fill('wrong');
    await page.getByTestId('sign-in-submit').click();
    await expect(dialog.getByTestId('error-code')).toHaveText('BAD_TOKEN');
    await page.getByTestId('sign-in-token').fill('a-good-token');
    await page.getByTestId('sign-in-submit').click();
    await expect(dialog).toBeHidden();
    await expect(page.getByTestId('entry-row').first()).toBeVisible();
  });

  test('explains a rotated token', async ({ page }) => {
    await openApp(page, { signedIn: false, params: 'mock-token=rotated' });
    await expect(page.getByTestId('token-reissue')).toBeVisible();
  });

  test('signs out from the menu and asks for the token again', async ({ page }) => {
    await openApp(page);
    await page.getByTestId('app-menu-button').click();
    await page.getByTestId('sign-out').click();
    await expect(page.getByTestId('sign-in-dialog')).toBeVisible();
  });
});
