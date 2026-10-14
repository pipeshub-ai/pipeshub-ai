'use client';

/** Every signed-in user polls service health; the EE build narrows this. */
export function useShouldPollServiceHealth(): boolean {
  return true;
}
