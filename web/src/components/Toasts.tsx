/**
 * Transient confirmations at the bottom of the window. The container is a
 * manual popover, so it sits in the top layer above any open modal dialog;
 * re-showing it on each new toast keeps it above the most recent dialog.
 */

import type { JSX } from 'preact';
import { useLayoutEffect, useRef } from 'preact/hooks';
import { dismissToast, toasts } from '../state/toasts';

export function Toasts(): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const items = toasts.value;
  const newest = items.at(-1)?.id ?? null;

  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) return;
    if (element.matches(':popover-open')) element.hidePopover();
    if (newest !== null) element.showPopover();
  }, [newest]);

  return (
    <div ref={ref} class="toasts" popover="manual" role="status" aria-live="polite">
      {items.map((toast) => (
        <div class="toast" key={toast.id} data-testid="toast">
          <span class="toast-text">{toast.message}</span>
          {toast.action !== null && (
            <button
              type="button"
              class="toast-action"
              onClick={() => {
                toast.action?.run();
                dismissToast(toast.id);
              }}
            >
              {toast.action.label}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
