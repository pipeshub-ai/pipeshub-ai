import type { PreparedJournalEvent } from './journal';
import { parseContentRef } from './run-content';

/** Deeper data is not walked; no event schema nests anywhere near this. */
const MAX_DEPTH = 64;

/** Store refs of every `sealed:` content ref anywhere in `data`. */
export function contentRefsIn(data: unknown): string[] {
  const refs = new Set<string>();
  const visit = (value: unknown, depth: number): void => {
    if (typeof value === 'string') {
      const parsed = parseContentRef(value);
      if (parsed !== null) refs.add(parsed.storeRef);
      return;
    }
    if (depth >= MAX_DEPTH || value === null || typeof value !== 'object') {
      return;
    }
    for (const entry of Array.isArray(value)
      ? (value as unknown[])
      : Object.values(value as Record<string, unknown>)) {
      visit(entry, depth + 1);
    }
  };
  visit(data, 0);
  return [...refs];
}

/**
 * The payload store refs a batch of events points at (T5): each claim-check,
 * and every content ref in the data (or, for a claim-checked event, the refs
 * taken from its data when it was prepared).
 */
export function referencedPayloads(
  events: readonly Pick<
    PreparedJournalEvent,
    'data' | 'payloadRef' | 'references'
  >[],
): string[] {
  const refs = new Set<string>();
  for (const event of events) {
    if (event.payloadRef !== undefined) refs.add(event.payloadRef.ref);
    for (const ref of event.data !== undefined
      ? contentRefsIn(event.data)
      : (event.references ?? [])) {
      refs.add(ref);
    }
  }
  return [...refs];
}
