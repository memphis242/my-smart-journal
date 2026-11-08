/**
 * Asserts an internal invariant: a condition this code controls, never user
 * input or server data. Checked in development and test builds; production
 * builds drop the check (Vite replaces `import.meta.env.DEV` with `false`).
 */
export function assert(condition: unknown, message: string): asserts condition {
  if (import.meta.env.DEV && !condition) {
    throw new Error(`Assertion failed: ${message}`);
  }
}

/** Marks a switch branch as unreachable; the compiler proves exhaustiveness. */
export function unreachable(value: never, context: string): never {
  throw new Error(`Unreachable: ${context}: ${JSON.stringify(value)}`);
}
