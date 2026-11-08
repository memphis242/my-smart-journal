/** An error raised by a list row (for example a failed copy), shown in place above the list. */

import { signal } from '@preact/signals';
import type { AppError } from '../api/errors';

export const listNotice = signal<AppError | null>(null);
