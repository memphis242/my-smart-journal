/** Sign-in: exchanges the access token for a session cookie (POST /session). */

import type { JSX } from 'preact';
import { useRef, useState } from 'preact/hooks';
import { toAppError, type AppError } from '../api/errors';
import { api, auth, authReason, bumpData, isMock } from '../state/app';
import { pokeQueue } from '../state/queue';
import { Dialog } from './Dialog';
import { ErrorNotice } from './ErrorNotice';

export function SignInDialog(): JSX.Element {
  const [token, setToken] = useState('');
  const [reveal, setReveal] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<AppError | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const submit = async (event: Event): Promise<void> => {
    event.preventDefault();
    const value = token.trim();
    if (value === '') {
      input.current?.focus();
      return;
    }
    setPending(true);
    setError(null);
    try {
      await api().signIn(value);
      setToken('');
      authReason.value = null;
      auth.value = 'signed-in';
      bumpData();
      pokeQueue();
    } catch (caught) {
      setError(toAppError(caught, 'Sign in'));
      setPending(false);
      input.current?.select();
    }
  };

  return (
    <Dialog
      label="Sign in"
      // Signing in is required; Escape does not dismiss it.
      onClose={() => undefined}
      class="dialog-signin"
      initialFocus={input}
      testId="sign-in-dialog"
    >
      <form class="dialog-panel" onSubmit={(event) => void submit(event)}>
        <header class="dialog-head">
          <h2 class="dialog-title">Sign in</h2>
        </header>
        <div class="dialog-body">
          {authReason.value?.code === 'TOKEN_REISSUE_REQUIRED' ? (
            <div class="notice notice-attention" data-testid="token-reissue">
              <div class="notice-main">
                <p class="notice-message">
                  <strong>The access token was replaced on the server,</strong> so earlier sessions and tokens no longer work.{' '}
                  <code class="error-code">TOKEN_REISSUE_REQUIRED</code>
                </p>
                <p class="notice-hint">
                  Create a new token on the server with <code>msj create-token</code>, then paste it here.
                </p>
              </div>
            </div>
          ) : (
            authReason.value !== null && <p class="lede">Your session ended; sign in again to continue.</p>
          )}
          <p class="lede">
            Paste the access token printed by <code>msj create-token</code>. The browser keeps a session for 30 days; the
            token itself is not stored.
          </p>
          <div class="field">
            <label class="field-label" for="sign-in-token">
              Access token
            </label>
            <div class="field-row">
              <input
                id="sign-in-token"
                ref={input}
                class="input input-mono"
                type={reveal ? 'text' : 'password'}
                autocomplete="current-password"
                spellcheck={false}
                value={token}
                data-testid="sign-in-token"
                onInput={(event) => {
                  setToken(event.currentTarget.value);
                }}
              />
              <button
                type="button"
                class="button"
                title={reveal ? 'Hide the token' : 'Show the token'}
                onClick={() => {
                  setReveal(!reveal);
                }}
              >
                {reveal ? 'Hide' : 'Show'}
              </button>
            </div>
            {isMock && <p class="field-hint">Mock mode: any token works except "wrong".</p>}
          </div>
          {error !== null && <ErrorNotice error={error} />}
        </div>
        <footer class="dialog-foot">
          <button type="submit" class="button button-primary" disabled={pending || token.trim() === ''} data-testid="sign-in-submit">
            {pending ? 'Signing in' : 'Sign in'}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
