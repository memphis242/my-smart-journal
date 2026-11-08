/**
 * Modal dialog on the native <dialog> element: top layer, inert background,
 * Escape routed to `onClose` (which may refuse, for example to confirm
 * discarding changes), focus returned to the opener on close.
 *
 * Render it only while open; mounting opens it and unmounting closes it.
 */

import type { ComponentChildren, JSX, RefObject } from 'preact';
import { useEffect, useLayoutEffect, useRef } from 'preact/hooks';

export interface DialogProps {
  readonly label: string;
  readonly onClose: () => void;
  readonly class?: string;
  /** Close when the backdrop is clicked. Off for dialogs holding edits. */
  readonly closeOnBackdrop?: boolean;
  readonly initialFocus?: RefObject<HTMLElement> | undefined;
  readonly testId?: string;
  readonly onKeyDown?: (event: KeyboardEvent) => void;
  readonly children: ComponentChildren;
}

export function Dialog(props: DialogProps): JSX.Element {
  const { label, onClose, closeOnBackdrop = false, initialFocus, testId, onKeyDown, children } = props;
  const ref = useRef<HTMLDialogElement>(null);
  const pressedOnBackdrop = useRef(false);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useLayoutEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return undefined;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    const target = initialFocus?.current ?? null;
    if (target !== null) target.focus();
    return () => {
      if (dialog.open) dialog.close();
      if (opener?.isConnected === true) opener.focus({ preventScroll: true });
    };
    // Opening is a mount-time action; initialFocus is read once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog === null) return undefined;
    const onCancel = (event: Event): void => {
      event.preventDefault();
      onCloseRef.current();
    };
    dialog.addEventListener('cancel', onCancel);
    return () => {
      dialog.removeEventListener('cancel', onCancel);
    };
  }, []);

  const onPointerDown = (event: PointerEvent): void => {
    pressedOnBackdrop.current = event.target === ref.current;
  };
  const onClick = (event: MouseEvent): void => {
    // Only a press and release both on the backdrop closes; a text selection dragged out of the panel does not.
    if (closeOnBackdrop && pressedOnBackdrop.current && event.target === ref.current) onCloseRef.current();
    pressedOnBackdrop.current = false;
  };

  return (
    <dialog
      ref={ref}
      class={props.class === undefined ? 'dialog' : `dialog ${props.class}`}
      aria-label={label}
      data-testid={testId}
      onPointerDown={onPointerDown}
      onClick={onClick}
      onKeyDown={onKeyDown}
    >
      {children}
    </dialog>
  );
}

export interface DialogHeaderProps {
  readonly title: string;
  readonly onClose: () => void;
  readonly children?: ComponentChildren;
}

export function DialogHeader({ title, onClose, children }: DialogHeaderProps): JSX.Element {
  return (
    <header class="dialog-head">
      <h2 class="dialog-title">{title}</h2>
      <div class="dialog-head-tools">
        {children}
        <button type="button" class="icon-button" title="Close (Esc)" aria-label="Close" onClick={onClose}>
          <svg class="icon icon-16" viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
            <path d="m5.5 5.5 9 9m0-9-9 9" />
          </svg>
        </button>
      </div>
    </header>
  );
}
