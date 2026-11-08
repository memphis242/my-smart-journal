import { useEffect, useState } from 'preact/hooks';

/** Tracks a media query, for layouts that render different controls (not just restyle them). */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = (): void => {
      setMatches(list.matches);
    };
    onChange();
    list.addEventListener('change', onChange);
    return () => {
      list.removeEventListener('change', onChange);
    };
  }, [query]);
  return matches;
}

/** True at widths where the desktop layout applies. */
export const WIDE = '(min-width: 760px)';
