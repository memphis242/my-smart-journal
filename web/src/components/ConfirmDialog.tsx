/** The page's own confirmation: says exactly what happens, offers Cancel and the named action. */

import type { ComponentChildren, JSX } from 'preact';
import { useRef, useState } from 'preact/hooks';
import { toAppError, type AppError } from '../api/errors';
import { Dialog } from './Dialog';
import { ErrorNotice } from './ErrorNotice';

export interface ConfirmDialogProps {
  readonly title: string;
  readonly children: ComponentChildren;
  /** Label of the action button, for example "Delete entry". */
  readonly action: string;
  readonly destructive?: boolean;
  readonly onConfirm: () => Promise<void> | void;
  readonly onCancel: () => void;
}

export function ConfirmDialog(props: ConfirmDialogProps): JSX.Element {
  const { title, children, action, destructive = false, onConfirm, onCancel } = props;
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  const confirm = async (): Promise<void> => {
    setPending(true);
    setError(null);
    try {
      await onConfirm();
    } catch (caught) {
      setError(toAppError(caught, action));
      setPending(false);
    }
  };

  return (
    <Dialog label={title} onClose={onCancel} class="dialog-confirm" initialFocus={cancelRef} testId="confirm-dialog">
      <div class="dialog-panel">
        <header class="dialog-head">
          <h2 class="dialog-title">{title}</h2>
        </header>
        <div class="dialog-body confirm-body">{children}</div>
        {error !== null && (
          <div class="dialog-body">
            <ErrorNotice error={error} compact />
          </div>
        )}
        <footer class="dialog-foot">
          <button type="button" class="button" ref={cancelRef} onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            class={destructive ? 'button button-danger' : 'button button-primary'}
            disabled={pending}
            data-testid="confirm-action"
            onClick={() => void confirm()}
          >
            {pending ? `${action}...` : action}
          </button>
        </footer>
      </div>
    </Dialog>
  );
}
