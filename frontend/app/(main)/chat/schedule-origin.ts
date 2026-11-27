import { displaySafe } from '@/lib/utils/display-safe';

/**
 * A conversation a schedule's firing started (ADR-SCH-02 D3): GET conversation detail `origin`,
 * `{ kind: 'schedule', scheduleId, firingId, scheduleName? }`. Anything else, or an older server without the field,
 * reads as no origin.
 */
export interface ScheduleOrigin {
  kind: 'schedule';
  scheduleId: string;
  firingId: string;
  /** The schedule's name now; null when the server leaves it out (the schedule was deleted, or an older server). */
  scheduleName: string | null;
}

const nonEmpty = (value: unknown): value is string => typeof value === 'string' && value.trim() !== '';

export function scheduleOriginOf(conversation: unknown): ScheduleOrigin | null {
  if (!conversation || typeof conversation !== 'object') return null;
  const origin = (conversation as { origin?: unknown }).origin;
  if (!origin || typeof origin !== 'object') return null;
  const raw = origin as Record<string, unknown>;
  if (raw.kind !== 'schedule' || !nonEmpty(raw.scheduleId) || !nonEmpty(raw.firingId)) return null;
  return {
    kind: 'schedule',
    scheduleId: raw.scheduleId,
    firingId: raw.firingId,
    // A name a model may have written (ADR-SCH-01 D2): shown display-safe, as on its approval card.
    scheduleName: nonEmpty(raw.scheduleName) ? displaySafe(raw.scheduleName, 'prose') : null,
  };
}
