import type { ChangeEvent } from '../contracts/generated';

/**
 * How a holder at revision `asOf` takes a change event (02-lld §8.1): an event
 * it has already applied is a duplicate (delivery is at-least-once, so
 * handlers are idempotent by revision); the event right after `asOf` is the
 * next one; anything else means events were missed, and the holder must drop
 * what it has for the org rather than replay.
 */
export type ChangeStep = 'duplicate' | 'next' | 'gap';

export function changeStep(
  asOf: number,
  event: Pick<ChangeEvent, 'revision' | 'previous'>,
): ChangeStep {
  if (event.revision <= asOf) return 'duplicate';
  return event.previous === asOf ? 'next' : 'gap';
}

/** The key a snapshot entry depends on for one touched object. */
export const touchKey = (kind: string, id: string): string => `${kind}/${id}`;
