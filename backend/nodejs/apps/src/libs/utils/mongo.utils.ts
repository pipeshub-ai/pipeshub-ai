/** Mongo's unique-index violation, whatever driver wrapper it arrives in. */
export const isDuplicateKeyError = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  (error as { code?: unknown }).code === 11000;
