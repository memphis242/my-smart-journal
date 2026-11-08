/** An error in place: plain message and stable code first, the cause one step away. */

import type { JSX } from 'preact';
import type { AppError } from '../api/errors';

export interface ErrorNoticeProps {
  readonly error: AppError;
  readonly compact?: boolean;
  readonly onDismiss?: () => void;
  readonly onRetry?: () => void;
}

export function ErrorNotice({ error, compact = false, onDismiss, onRetry }: ErrorNoticeProps): JSX.Element {
  return (
    <div class={compact ? 'notice notice-error notice-compact' : 'notice notice-error'} role="alert" data-testid="error-notice">
      <div class="notice-main">
        <p class="notice-message">
          <span class="notice-text">{error.message}</span>{' '}
          <code class="error-code" title="Stable error code" data-testid="error-code">
            {error.code}
          </code>
        </p>
        {error.hint !== null && <p class="notice-hint">{error.hint}</p>}
        {error.details.length > 0 && (
          <details class="notice-details">
            <summary>Show details</summary>
            <dl class="detail-list">
              {error.details.map((line) => (
                <div class="detail-row" key={line.label}>
                  <dt>{line.label}</dt>
                  <dd>{line.value}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </div>
      {(onRetry !== undefined || onDismiss !== undefined) && (
        <div class="notice-actions">
          {onRetry !== undefined && (
            <button type="button" class="button button-small" onClick={onRetry}>
              Try again
            </button>
          )}
          {onDismiss !== undefined && (
            <button type="button" class="icon-button icon-button-small" title="Dismiss" aria-label="Dismiss" onClick={onDismiss}>
              <svg class="icon icon-14" viewBox="0 0 20 20" width="14" height="14" aria-hidden="true">
                <path d="m5.5 5.5 9 9m0-9-9 9" />
              </svg>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
