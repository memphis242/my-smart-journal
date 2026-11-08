/**
 * Anchored drop-down on the native Popover API: top layer, light dismiss and
 * Escape come from the browser. On wide screens it is placed under its
 * trigger; on narrow screens CSS turns it into a bottom sheet.
 */

import type { ComponentChildren, JSX, RefObject } from 'preact';
import { useEffect, useRef } from 'preact/hooks';

export interface PopoverProps {
  readonly id: string;
  readonly anchor: RefObject<HTMLElement>;
  readonly label: string;
  readonly align?: 'start' | 'end';
  readonly class?: string;
  readonly onOpenChange?: (open: boolean) => void;
  readonly children: ComponentChildren;
}

const MARGIN = 8;

function place(popover: HTMLElement, anchor: HTMLElement, align: 'start' | 'end'): void {
  if (!window.matchMedia('(min-width: 600px)').matches) {
    popover.style.removeProperty('top');
    popover.style.removeProperty('left');
    return;
  }
  const rect = anchor.getBoundingClientRect();
  const width = popover.offsetWidth;
  const height = popover.offsetHeight;
  let left = align === 'end' ? rect.right - width : rect.left;
  left = Math.max(MARGIN, Math.min(left, window.innerWidth - width - MARGIN));
  let top = rect.bottom + 4;
  if (top + height > window.innerHeight - MARGIN) top = Math.max(MARGIN, rect.top - height - 4);
  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

export function Popover({ id, anchor, label, align = 'start', class: className, onOpenChange, children }: PopoverProps): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;

  useEffect(() => {
    const popover = ref.current;
    if (popover === null) return undefined;
    const reposition = (): void => {
      if (anchor.current !== null && popover.matches(':popover-open')) place(popover, anchor.current, align);
    };
    const onToggle = (event: Event): void => {
      const open = (event as ToggleEvent).newState === 'open';
      if (open) reposition();
      onOpenChangeRef.current?.(open);
    };
    popover.addEventListener('toggle', onToggle);
    window.addEventListener('resize', reposition);
    return () => {
      popover.removeEventListener('toggle', onToggle);
      window.removeEventListener('resize', reposition);
    };
  }, [anchor, align]);

  return (
    <div
      ref={ref}
      id={id}
      popover="auto"
      class={className === undefined ? 'popover' : `popover ${className}`}
      role="dialog"
      aria-label={label}
    >
      {children}
    </div>
  );
}

export function hidePopover(id: string): void {
  const element = document.getElementById(id);
  if (element?.matches(':popover-open') === true) element.hidePopover();
}
