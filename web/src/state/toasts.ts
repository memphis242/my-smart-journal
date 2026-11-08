/** Short confirmations at the bottom of the window. Errors are shown in place, not here. */

import { signal } from '@preact/signals';

export interface ToastAction {
  readonly label: string;
  readonly run: () => void;
}

export interface Toast {
  readonly id: number;
  readonly message: string;
  readonly action: ToastAction | null;
}

export const toasts = signal<readonly Toast[]>([]);

let nextId = 1;
const TOAST_MS = 4500;

export function dismissToast(id: number): void {
  toasts.value = toasts.value.filter((toast) => toast.id !== id);
}

export function toast(message: string, action: ToastAction | null = null): void {
  const id = nextId++;
  // At most two at once: a new one pushes out the oldest.
  toasts.value = [...toasts.value.slice(-1), { id, message, action }];
  window.setTimeout(() => {
    dismissToast(id);
  }, action === null ? TOAST_MS : TOAST_MS * 1.6);
}
