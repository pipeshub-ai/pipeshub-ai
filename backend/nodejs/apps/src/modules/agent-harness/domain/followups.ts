import { z } from 'zod/v3';

/** A suggested next question under a completed answer (UX5). */
export interface Followup {
  text: string;
}

export const MAX_FOLLOWUPS = 3;
export const MAX_FOLLOWUP_CHARS = 160;
/** How long a reader may wait for them after the run ended. */
export const MAX_FOLLOWUPS_WAIT_MS = 15_000;

/** Code points, as the worker counts them (Python `len`), not UTF-16 units. */
const followupText = z.string().refine(
  (text) => {
    const length = Array.from(text).length;
    return length >= 1 && length <= MAX_FOLLOWUP_CHARS;
  },
  `1 to ${String(MAX_FOLLOWUP_CHARS)} characters`,
);

export const followupItemsSchema = z
  .array(z.object({ text: followupText }).strict())
  .min(1)
  .max(MAX_FOLLOWUPS);

/** The `items` of a `followups` activity frame; null when they are not 1–3 bounded texts. */
export function followupsOf(data: unknown): Followup[] | null {
  const items: unknown =
    typeof data === 'object' && data !== null
      ? (data as Record<string, unknown>)['items']
      : undefined;
  const parsed = followupItemsSchema.safeParse(items);
  return parsed.success ? parsed.data.map(({ text }) => ({ text })) : null;
}

/** What storing a run's follow-ups did; the first write wins. */
export type FollowupsWrite =
  | { outcome: 'stored' | 'kept'; followups: Followup[] }
  | { outcome: 'not_completed' };
