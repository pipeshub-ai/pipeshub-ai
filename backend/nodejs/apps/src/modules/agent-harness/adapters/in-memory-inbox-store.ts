import {
  DEFAULT_INBOX_PREFERENCES,
  MAX_MUTED_SCHEDULES,
  type InboxPreferences,
} from '../domain/inbox';
import type { InboxStore } from '../ports/inbox';

const key = (orgId: string, id: string): string => `${orgId}\u0000${id}`;

/** A copy the caller may change without changing the store. */
export const copyPreferences = (prefs: InboxPreferences): InboxPreferences => ({
  mutedSchedules: [...prefs.mutedSchedules],
  pause: prefs.pause === null ? null : { ...prefs.pause },
  runNotices: { ...prefs.runNotices },
});

/** The muted list after one change: newest last, each once, the oldest dropped past the bound. */
export function mutedAfter(
  current: readonly string[],
  scheduleId: string,
  muted: boolean,
): string[] {
  const rest = current.filter((id) => id !== scheduleId);
  const next = muted ? [...rest, scheduleId] : rest;
  return next.slice(Math.max(0, next.length - MAX_MUTED_SCHEDULES));
}

/** `InboxStore` in process memory: tests and a single-process deployment without a document store. */
export class InMemoryInboxStore implements InboxStore {
  private readonly prefs = new Map<
    string,
    InboxPreferences & { orgId: string; userId: string }
  >();
  private readonly claims = new Map<
    string,
    { orgId: string; userId: string }
  >();
  private readonly leases = new Map<
    string,
    { orgId: string; userId: string; sessionId: string; until: number }
  >();

  preferences(orgId: string, userId: string): Promise<InboxPreferences> {
    return Promise.resolve(this.current(orgId, userId));
  }

  private current(orgId: string, userId: string): InboxPreferences {
    return copyPreferences(
      this.prefs.get(key(orgId, userId)) ?? DEFAULT_INBOX_PREFERENCES,
    );
  }

  async setMuted(
    orgId: string,
    userId: string,
    scheduleId: string,
    muted: boolean,
    _now?: number,
  ): Promise<InboxPreferences> {
    const current = await this.preferences(orgId, userId);
    return this.put(orgId, userId, {
      ...current,
      mutedSchedules: mutedAfter(current.mutedSchedules, scheduleId, muted),
    });
  }

  async setPause(
    orgId: string,
    userId: string,
    pause: InboxPreferences['pause'],
    _now?: number,
  ): Promise<InboxPreferences> {
    return this.put(orgId, userId, {
      ...(await this.preferences(orgId, userId)),
      pause,
    });
  }

  async setRunNotices(
    orgId: string,
    userId: string,
    channels: Partial<InboxPreferences['runNotices']>,
    _now?: number,
  ): Promise<InboxPreferences> {
    const current = await this.preferences(orgId, userId);
    return this.put(orgId, userId, {
      ...current,
      runNotices: { ...current.runNotices, ...channels },
    });
  }

  claimRunNotice(
    orgId: string,
    runId: string,
    userId: string,
    _now?: number,
  ): Promise<boolean> {
    const at = key(orgId, runId);
    if (this.claims.has(at)) return Promise.resolve(false);
    this.claims.set(at, { orgId, userId });
    return Promise.resolve(true);
  }

  watch(lease: {
    orgId: string;
    userId: string;
    sessionId: string;
    watcherId: string;
    until: number;
  }): Promise<void> {
    this.leases.set(key(lease.orgId, lease.watcherId), {
      orgId: lease.orgId,
      userId: lease.userId,
      sessionId: lease.sessionId,
      until: lease.until,
    });
    return Promise.resolve();
  }

  unwatch(orgId: string, watcherId: string): Promise<void> {
    this.leases.delete(key(orgId, watcherId));
    return Promise.resolve();
  }

  watching(
    orgId: string,
    userId: string,
    sessionId: string,
    at: number,
  ): Promise<boolean> {
    for (const lease of this.leases.values()) {
      if (
        lease.orgId === orgId &&
        lease.userId === userId &&
        lease.sessionId === sessionId &&
        lease.until > at
      ) {
        return Promise.resolve(true);
      }
    }
    return Promise.resolve(false);
  }

  eraseUser(orgId: string, userId: string): Promise<void> {
    this.prefs.delete(key(orgId, userId));
    for (const [at, claim] of this.claims)
      if (claim.orgId === orgId && claim.userId === userId)
        this.claims.delete(at);
    for (const [at, lease] of this.leases)
      if (lease.orgId === orgId && lease.userId === userId)
        this.leases.delete(at);
    return Promise.resolve();
  }

  eraseOrg(orgId: string): Promise<void> {
    for (const map of [this.prefs, this.claims, this.leases] as Array<
      Map<string, { orgId: string }>
    >) {
      for (const [at, value] of map) if (value.orgId === orgId) map.delete(at);
    }
    return Promise.resolve();
  }

  private put(
    orgId: string,
    userId: string,
    prefs: InboxPreferences,
  ): InboxPreferences {
    this.prefs.set(key(orgId, userId), {
      ...copyPreferences(prefs),
      orgId,
      userId,
    });
    return copyPreferences(prefs);
  }
}
