import type { InboxPreferences } from '../../../../modules/agent-harness/domain/inbox';

/** A person's settings as both stores keep them: one column or field per setting. */
export interface StoredInboxPreferences {
  mutedSchedules: string[] | null;
  pausedUntil: number | string | null;
  pausedUntilResumed: boolean;
  runNoticeEmail: boolean;
  runNoticeSlack: boolean;
}

export function preferencesOf(
  stored: StoredInboxPreferences | null,
): InboxPreferences {
  if (stored === null) {
    return {
      mutedSchedules: [],
      pause: null,
      runNotices: { email: false, slack: false },
    };
  }
  const until = stored.pausedUntil === null ? null : Number(stored.pausedUntil);
  return {
    mutedSchedules: [...(stored.mutedSchedules ?? [])],
    pause: stored.pausedUntilResumed
      ? { untilResumed: true }
      : until !== null
        ? { until }
        : null,
    runNotices: { email: stored.runNoticeEmail, slack: stored.runNoticeSlack },
  };
}

/** The two stored fields of a pause. */
export function pauseFields(pause: InboxPreferences['pause']): {
  pausedUntil: number | null;
  pausedUntilResumed: boolean;
} {
  if (pause === null) return { pausedUntil: null, pausedUntilResumed: false };
  return 'untilResumed' in pause
    ? { pausedUntil: null, pausedUntilResumed: true }
    : { pausedUntil: pause.until, pausedUntilResumed: false };
}
