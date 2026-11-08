/**
 * One line-glyph set: 20-unit grid, 1.5 stroke, round caps. Icons are
 * decorative (aria-hidden); their buttons carry the accessible name.
 */

import type { JSX } from 'preact';

export type IconName =
  | 'audio'
  | 'check'
  | 'chevron-down'
  | 'chevron-left'
  | 'chevron-right'
  | 'close'
  | 'compile'
  | 'copy'
  | 'download'
  | 'edit'
  | 'export'
  | 'filter'
  | 'mic'
  | 'moon'
  | 'more'
  | 'open'
  | 'plus'
  | 'retry'
  | 'search'
  | 'sign-out'
  | 'sort-asc'
  | 'sort-desc'
  | 'stop'
  | 'sun'
  | 'trash'
  | 'upload'
  | 'width-reset';

const PATHS: Readonly<Record<IconName, JSX.Element>> = {
  audio: <path d="M4 9v2M7 6.5v7M10 4v12M13 6.5v7M16 9v2" />,
  check: <path d="M4.5 10.5 8 14l7.5-8" />,
  'chevron-down': <path d="m5.5 8 4.5 4.5L14.5 8" />,
  'chevron-left': <path d="M12.5 4.5 7 10l5.5 5.5" />,
  'chevron-right': <path d="M7.5 4.5 13 10l-5.5 5.5" />,
  close: <path d="m5.5 5.5 9 9m0-9-9 9" />,
  compile: (
    <>
      <path d="M10 3.5 16.5 7 10 10.5 3.5 7z" />
      <path d="M3.5 10.5 10 14l6.5-3.5" />
      <path d="M3.5 13.75 10 17.25l6.5-3.5" />
    </>
  ),
  copy: (
    <>
      <rect x="7" y="7" width="9" height="9.5" rx="1.25" />
      <path d="M13 7V5.25C13 4.56 12.44 4 11.75 4h-6.5C4.56 4 4 4.56 4 5.25v6.5c0 .69.56 1.25 1.25 1.25H7" />
    </>
  ),
  download: <path d="M10 3.5v9M6.25 9 10 12.75 13.75 9M4 16.25h12" />,
  edit: (
    <>
      <path d="M12.75 4.25 15.75 7.25 7.5 15.5 4 16l.5-3.5z" />
      <path d="m11 6 3 3" />
    </>
  ),
  export: (
    <>
      <path d="M10 12.5V3.5M6.75 6.75 10 3.5l3.25 3.25" />
      <path d="M6.5 9.5H5v7h10v-7h-1.5" />
    </>
  ),
  filter: <path d="M3.5 4.5h13l-5 6.25v4.5l-3 1.5v-6z" />,
  mic: (
    <>
      <rect x="7.5" y="3" width="5" height="9" rx="2.5" />
      <path d="M5 9.75a5 5 0 0 0 10 0M10 14.75v2.5" />
    </>
  ),
  moon: <path d="M15.75 12.25A6.25 6.25 0 0 1 7.75 4.25a6.25 6.25 0 1 0 8 8z" />,
  more: (
    <>
      <circle cx="5" cy="10" r="0.6" />
      <circle cx="10" cy="10" r="0.6" />
      <circle cx="15" cy="10" r="0.6" />
    </>
  ),
  open: (
    <>
      <path d="M8.5 5H5.25C4.56 5 4 5.56 4 6.25v8.5C4 15.44 4.56 16 5.25 16h8.5c.69 0 1.25-.56 1.25-1.25V11.5" />
      <path d="M11.5 4h4.5v4.5M16 4l-6.5 6.5" />
    </>
  ),
  plus: <path d="M10 4.5v11M4.5 10h11" />,
  retry: (
    <>
      <path d="M15.25 10a5.25 5.25 0 1 1-1.54-3.71" />
      <path d="M15.25 3.75v3.5h-3.5" />
    </>
  ),
  search: (
    <>
      <circle cx="8.75" cy="8.75" r="4.75" />
      <path d="m12.25 12.25 4.25 4.25" />
    </>
  ),
  'sign-out': (
    <>
      <path d="M8 4.5H5.5C4.67 4.5 4 5.17 4 6v8c0 .83.67 1.5 1.5 1.5H8" />
      <path d="M11.75 13.5 15.25 10l-3.5-3.5M15.25 10H8" />
    </>
  ),
  'sort-asc': <path d="M10 15.5v-11M6.25 8.25 10 4.5l3.75 3.75" />,
  'sort-desc': <path d="M10 4.5v11M6.25 11.75 10 15.5l3.75-3.75" />,
  stop: <rect x="5.5" y="5.5" width="9" height="9" rx="1" />,
  sun: (
    <>
      <circle cx="10" cy="10" r="3.25" />
      <path d="M10 2.75v1.5M10 15.75v1.5M2.75 10h1.5M15.75 10h1.5M4.87 4.87l1.06 1.06M14.07 14.07l1.06 1.06M4.87 15.13l1.06-1.06M14.07 5.93l1.06-1.06" />
    </>
  ),
  trash: (
    <>
      <path d="M4.5 6h11M8 6V4.25h4V6" />
      <path d="m6 6 .75 10h6.5L14 6" />
    </>
  ),
  upload: <path d="M10 13V4M6.25 7.75 10 4l3.75 3.75M4 16.25h12" />,
  'width-reset': (
    <>
      <path d="M3.5 5v10M16.5 5v10" />
      <path d="M6.5 10h7M8.5 8l-2 2 2 2M11.5 8l2 2-2 2" />
    </>
  ),
};

export interface IconProps {
  readonly name: IconName;
  readonly size?: 14 | 16 | 18 | 20 | 24 | 28;
  readonly class?: string;
}

export function Icon({ name, size = 16, class: className }: IconProps): JSX.Element {
  return (
    <svg
      class={className === undefined ? `icon icon-${size}` : `icon icon-${size} ${className}`}
      viewBox="0 0 20 20"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
