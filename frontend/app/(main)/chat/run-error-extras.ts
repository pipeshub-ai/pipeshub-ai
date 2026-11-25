import type { NextActionKind } from '@/lib/contracts/harness/generated/core/enums';

/**
 * Run endings an edition's own rules end runs with (its overlay of `vectors/run-error-codes.json`), by code: the
 * words in the reader's language and the next step. Editions plug in via config.ts; this default adds none.
 */
export type RunErrorExtras = Readonly<Record<string, { text: () => string; next: NextActionKind }>>;

export const runErrorExtras: RunErrorExtras = Object.freeze({});
