import { existsSync } from 'node:fs';
import path from 'node:path';

/**
 * Root for generated output (bundles, caches, test results, screenshots), kept
 * outside the source tree. MSJ_WEB_BUILD_ROOT picks it; without it, the shared
 * /workspace/build tree is used when present, and the web directory (whose
 * dist*, test-results and .vite folders are gitignored) otherwise.
 */
export function buildRoot(): string {
  const configured = process.env.MSJ_WEB_BUILD_ROOT;
  if (configured !== undefined && configured !== '') return path.resolve(configured);
  const shared = '/workspace/build';
  return existsSync(shared) ? path.join(shared, 'my-smart-journal-web-ui') : path.resolve(import.meta.dirname, '..');
}
