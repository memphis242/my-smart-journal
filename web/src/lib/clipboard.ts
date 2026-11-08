/** Clipboard writes with a selection-based fallback and specific errors. */

import { AppError } from '../api/errors';

function legacyCopy(text: string): boolean {
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.className = 'clipboard-scratch';
  document.body.appendChild(area);
  area.select();
  let copied: boolean;
  try {
    // Deprecated, but the only path on plain-HTTP origins without the async clipboard.
    // eslint-disable-next-line @typescript-eslint/no-deprecated
    copied = document.execCommand('copy');
  } catch {
    copied = false;
  }
  area.remove();
  return copied;
}

export async function copyText(text: string): Promise<void> {
  if (typeof navigator.clipboard === 'object' && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (error) {
      if (legacyCopy(text)) return;
      throw new AppError({
        code: 'CLIPBOARD_DENIED',
        message: 'Copy failed: the browser refused clipboard access.',
        hint: 'Allow clipboard access for this site, or select the text and press Ctrl+C.',
        details: [{ label: 'Cause', value: error instanceof Error ? `${error.name}: ${error.message}` : String(error) }],
        cause: error,
      });
    }
  }
  if (legacyCopy(text)) return;
  throw new AppError({
    code: 'CLIPBOARD_UNAVAILABLE',
    message: 'Copy failed: this page has no clipboard access.',
    hint: window.isSecureContext
      ? 'Select the text and press Ctrl+C.'
      : 'Clipboard access needs HTTPS. Open the journal over HTTPS, or select the text and press Ctrl+C.',
  });
}
