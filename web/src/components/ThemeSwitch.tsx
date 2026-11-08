/** Circular switch at the top right. It shows the mode it switches TO: a sun while dark, a moon while light. */

import type { JSX } from 'preact';
import { setTheme, theme } from '../lib/theme';
import { Icon } from './Icon';

export function ThemeSwitch(): JSX.Element {
  const next = theme.value === 'dark' ? 'light' : 'dark';
  const label = next === 'light' ? 'Switch to light theme' : 'Switch to dark theme';
  return (
    <button
      type="button"
      class="theme-switch"
      title={label}
      aria-label={label}
      data-testid="theme-switch"
      data-next-theme={next}
      onClick={() => {
        setTheme(next);
      }}
    >
      <Icon name={next === 'light' ? 'sun' : 'moon'} size={18} />
    </button>
  );
}
