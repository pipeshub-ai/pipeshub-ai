'use client';

/** Every signed-in user polls service health; another build may narrow this. */
export function useShouldPollServiceHealth(): boolean {
  return true;
}
