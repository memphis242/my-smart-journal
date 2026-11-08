/** What signing out removes from this browser and from memory. */

import { clearDrafts } from '../lib/drafts';
import { clearDetails, closeEntry } from './entry-modal';
import { resetFacets } from './facets';
import { listNotice } from './notices';
import { resetQueue } from './queue';
import { clearSelection } from './selection';
import { savedScroll } from './view';

/** Drafts, selection, cached entries and queue state; per-viewer conveniences (theme, widths) stay. */
export function forgetLocalSession(): void {
  closeEntry();
  clearDrafts();
  clearSelection();
  clearDetails();
  savedScroll.clear();
  listNotice.value = null;
  resetFacets();
  resetQueue();
}
