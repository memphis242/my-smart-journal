/**
 * The whole user journey against the real backend and worker. Tests run in
 * order and build on each other's data; a per-run tag keeps this run's
 * entries apart from earlier runs and from the seeded ones.
 */
import { readFileSync } from 'node:fs';
import type { APIRequestContext, Page } from '@playwright/test';
import { bearer, expect, signInQuietly, test, token } from './fixtures';

test.describe.configure({ mode: 'serial' });

const RUN = Date.now().toString(36);
const TAG = `e2e-${RUN}`;
const WORD = `zephyrine${RUN}`;

interface Summary {
  readonly id: string;
  readonly title: string | null;
  readonly status: string;
  readonly source: string;
  readonly text: string;
  readonly revision: number;
}

async function ours(request: APIRequestContext): Promise<readonly Summary[]> {
  const page = (await bearer(request, `/api/v1/entries?tag=${TAG}&limit=50&order=asc`)) as { items: Summary[] };
  return page.items;
}

async function open(page: Page, query = ''): Promise<void> {
  await signInQuietly(page);
  await page.goto(`/${query}`);
  await expect(page.getByTestId('entries-page')).toBeVisible();
}

function row(page: Page, id: string): ReturnType<Page['locator']> {
  return page.locator(`[data-row-id="${id}"]`);
}

async function addEntry(page: Page, title: string, fill: () => Promise<void>): Promise<void> {
  await page.getByTestId('new-entry').click();
  const editor = page.getByTestId('entry-editor');
  await fill();
  await editor.getByTestId('entry-title').fill(title);
  await editor.getByTestId('tag-input').fill(`${TAG},`);
  await editor.getByTestId('entry-submit').click();
  await expect(editor).toBeHidden();
}

test('signs in with the access token', async ({ page }) => {
  await page.goto('/');
  const dialog = page.getByTestId('sign-in-dialog');
  await expect(dialog).toBeVisible();
  await page.getByTestId('sign-in-token').fill('not-the-token');
  await page.getByTestId('sign-in-submit').click();
  await expect(dialog.getByTestId('error-code')).toHaveText('BAD_TOKEN');
  await page.getByTestId('sign-in-token').fill(token());
  await page.getByTestId('sign-in-submit').click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId('entry-row').first()).toBeVisible();
});

test('adds a written entry, a recorded one and an uploaded recording', async ({ page, request }) => {
  await open(page);
  await addEntry(page, `Text ${RUN}`, async () => {
    await page.getByTestId('entry-text-input').fill(`Integration note ${WORD}: paired on the retry queue and wrote down what is still open.`);
  });
  await addEntry(page, `Voice ${RUN}`, async () => {
    await page.getByTestId('mode-record').click();
    await page.getByTestId('recorder-start').click();
    await expect(page.getByTestId('recorder-clock')).toBeVisible();
    await page.waitForTimeout(1500);
    await page.getByTestId('recorder-stop').click();
    await expect(page.locator('.take-audio')).toBeVisible();
  });
  await addEntry(page, `File ${RUN}`, async () => {
    await page.getByTestId('mode-record').click();
    const chooser = page.waitForEvent('filechooser');
    await page.getByTestId('audio-choose').click();
    await (await chooser).setFiles(process.env.MSJ_E2E_AUDIO ?? '');
    await expect(page.locator('.take-audio')).toBeVisible();
  });
  const added = await ours(request);
  expect(added.map((entry) => entry.source)).toEqual(['text', 'audio', 'audio']);
  for (const entry of added) await expect(row(page, entry.id)).toHaveAttribute('data-status', 'queued');
});

test('Run now: the worker picks the batch up and every entry finishes', async ({ page, request }) => {
  await open(page);
  await page.getByTestId('run-batch').click();
  for (const entry of await ours(request)) {
    await expect(row(page, entry.id)).toHaveAttribute('data-status', 'done', { timeout: 40_000 });
  }
  const audio = (await ours(request)).filter((entry) => entry.source === 'audio');
  expect(audio.every((entry) => entry.text.trim() !== '')).toBe(true);
});

test('the modal steps to older and newer entries by bar and by key', async ({ page, request }) => {
  await open(page);
  const [text] = await ours(request);
  if (text === undefined) throw new Error('the written entry is missing');
  await row(page, text.id).click();
  const sheet = page.getByTestId('entry-modal').locator('.entry-sheet');
  await expect(sheet).toHaveAttribute('data-entry-id', text.id);
  await page.getByTestId('nav-newer').click();
  await expect(sheet).not.toHaveAttribute('data-entry-id', text.id);
  await page.keyboard.press('ArrowLeft');
  await expect(sheet).toHaveAttribute('data-entry-id', text.id);
  await page.getByTestId('nav-older').click();
  await expect(sheet).not.toHaveAttribute('data-entry-id', text.id);
  await page.keyboard.press('ArrowRight');
  await expect(sheet).toHaveAttribute('data-entry-id', text.id);
});

test('edits an entry; changed text is processed again', async ({ page, request }) => {
  await open(page);
  const [text] = await ours(request);
  if (text === undefined) throw new Error('the written entry is missing');
  await row(page, text.id).click();
  await page.getByTestId('modal-edit').click();
  const editor = page.getByTestId('entry-editor');
  await editor.getByTestId('entry-title').fill(`Edited ${RUN}`);
  await editor.getByTestId('entry-text-input').press('End');
  await editor.getByTestId('entry-text-input').pressSequentially(' The index review is still pending.');
  await editor.getByTestId('entry-submit').click();
  await expect(editor).toBeHidden();
  await expect(page.getByTestId('modal-title')).toHaveText(`Edited ${RUN}`);
  const edited = (await ours(request)).find((entry) => entry.id === text.id);
  expect(edited?.revision).toBe(2);
  await page.keyboard.press('Escape');
  await page.getByTestId('run-batch').click();
  await expect(row(page, text.id)).toHaveAttribute('data-status', 'done', { timeout: 40_000 });
});

test('filters by date range, tag, project and skill', async ({ page, request }) => {
  await open(page, '?range=all');
  const total = ((await bearer(request, '/api/v1/entries?limit=1')) as { total: number }).total;
  await expect(page.getByTestId('entry-row').first()).toBeVisible();
  await expect.poll(() => page.getByTestId('entry-row').count()).toBe(Math.min(total, 100));

  await page.getByTestId('range-today').click();
  for (const entry of await ours(request)) await expect(row(page, entry.id)).toBeVisible();

  // A custom range that holds only seeded entries.
  await open(page, '?range=custom&from=2000-01-01&to=2000-12-31');
  await expect(page.getByTestId('empty-state')).toBeVisible();

  await open(page, '?range=all');
  await page.getByTestId('filter-tags').click();
  await page.locator('#facet-tags [data-testid=facet-option]', { hasText: TAG }).click();
  await page.keyboard.press('Escape');
  await expect.poll(() => page.getByTestId('entry-row').count()).toBe(3);
  await page.getByTestId('clear-filters').click();

  // The fake extraction finds no projects or skills: the lists say so, and a project filter matches nothing.
  await page.getByTestId('filter-projects').click();
  await expect(page.locator('#facet-projects .facet-empty')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByTestId('filter-skills').click();
  await expect(page.locator('#facet-skills .facet-empty')).toBeVisible();
  await page.keyboard.press('Escape');
  await open(page, '?range=all&project=No%20such%20project');
  await expect(page.getByTestId('empty-state')).toBeVisible();
  await open(page, '?range=all&skill=No%20such%20skill');
  await expect(page.getByTestId('empty-state')).toBeVisible();
});

test('keyword search streams all five tiers', async ({ page }) => {
  await open(page, '?range=all');
  await page.getByTestId('search-input').fill(WORD);
  const results = page.getByTestId('search-results');
  await expect(results.locator('[data-testid=tier-progress] [data-state=done]')).toHaveCount(5);
  await expect(results.getByTestId('tier-group').first()).toBeVisible();
  await page.getByTestId('search-input').fill('retry queue backoff');
  await expect(results.locator('[data-testid=tier-progress] [data-state=done]')).toHaveCount(5);
  expect(await results.getByTestId('tier-group').count()).toBeGreaterThan(0);
  // Fake embeddings answer the semantic tier rather than reporting it unavailable.
  await expect(results.locator('[data-tier="5"]').first()).toHaveAttribute('data-state', 'done');
});

test('relevance ranking feeds a plain-text export that copies and downloads', async ({ page }) => {
  // The audio entries hold the fake transcript, the written one the retry-queue note: this query matches all three.
  await open(page, `?range=all&tag=${TAG}&mode=relevance&q=${encodeURIComponent('fake transcript retry queue')}`);
  const results = page.getByTestId('relevance-results');
  await expect.poll(() => results.getByTestId('entry-row').count()).toBeGreaterThanOrEqual(2);
  await results.getByTestId('row-select').nth(0).click();
  await results.getByTestId('row-select').nth(1).click();
  await page.getByTestId('open-export').click();
  const preview = page.getByTestId('export-preview');
  await expect(preview).not.toHaveValue('');
  const body = await preview.inputValue();
  expect(body).toMatch(/^\d{4}-\d{2}-\d{2} {2}/m);
  expect(body).not.toMatch(/^#|\*\*|^- /m);
  await page.getByTestId('export-copy').click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(body);
  const download = page.waitForEvent('download');
  await page.getByTestId('export-download').click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/^journal-export-\d{4}-\d{2}-\d{2}\.txt$/);
  expect(readFileSync(await file.path(), 'utf8')).toBe(body);
});

test('compiles the run entries; the worker finishes the compilation', async ({ page }) => {
  await open(page, `?range=all&tag=${TAG}`);
  await expect.poll(() => page.getByTestId('entry-row').count()).toBe(3);
  await page.getByTestId('list-header').getByRole('button', { name: 'Select all shown' }).click();
  await page.getByTestId('open-compile').click();
  await page.getByTestId('compile-purpose').fill(`integration ${RUN}`);
  await page.getByTestId('compile-submit').click();
  await expect(page.getByTestId('compile-dialog')).toBeHidden();
  await page.getByTestId('nav-compilations').click();
  const newest = page.getByTestId('compilation-row').first();
  await expect(newest).toHaveAttribute('data-status', 'done', { timeout: 40_000 });
  await newest.click();
  await expect(page.getByTestId('compilation-dialog')).toBeVisible();
});

test('deleting a compiled entry warns about its compilations and removes them', async ({ page, request }) => {
  const before = ((await bearer(request, '/api/v1/compilations')) as readonly unknown[]).length;
  await open(page, `?range=all&tag=${TAG}`);
  const [text] = await ours(request);
  if (text === undefined) throw new Error('the written entry is missing');
  await row(page, text.id).click();
  await page.getByTestId('modal-delete').click();
  const confirm = page.getByTestId('confirm-dialog');
  await expect(confirm.getByTestId('delete-compilations')).toBeVisible();
  await confirm.getByTestId('confirm-action').click();
  await expect(confirm).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(row(page, text.id)).toHaveCount(0);
  expect(((await bearer(request, '/api/v1/compilations')) as readonly unknown[]).length).toBe(before - 1);
});

test('signs out', async ({ page }) => {
  await open(page);
  await page.getByTestId('app-menu-button').click();
  await page.getByTestId('sign-out').click();
  await expect(page.getByTestId('sign-in-dialog')).toBeVisible();
  expect((await page.request.get('/api/v1/session')).status()).toBe(401);
});
