/**
 * Which store the harness found degraded, as this Node process saw it (CH-07, CH-06..09): the admin's view of a
 * dependency outage the runs ride through (they retry, wait or fall back to polling, so nothing fails to show it).
 *
 * A store is degraded while its failures keep coming: from the first failure of a streak until `WINDOW_MS` passes
 * with none. Only the store's name and when the streak began are kept, never an error's text.
 */

export const DEPENDENCIES = ['mongo', 'kv', 'broker', 'blob'] as const;
export type Dependency = (typeof DEPENDENCIES)[number];

/** How long after its last failure a store reads as recovered. */
export const WINDOW_MS = 60_000;

export interface DependencyStatus {
  status: 'ok' | 'degraded';
  /** When the current degraded streak began (ISO time); null while ok. */
  since: string | null;
}

export type DependencyHealthView = Record<Dependency, DependencyStatus>;

interface Streak {
  since: number;
  last: number;
}

export class DependencyHealth {
  private readonly streaks = new Map<Dependency, Streak>();

  constructor(private readonly now: () => number = Date.now) {}

  /** A call to `store` failed as an outage (not a refusal): its streak starts or goes on. */
  report(store: Dependency): void {
    const at = this.now();
    const streak = this.streaks.get(store);
    if (streak !== undefined && at - streak.last <= WINDOW_MS) {
      streak.last = at;
      return;
    }
    this.streaks.set(store, { since: at, last: at });
  }

  view(): DependencyHealthView {
    const at = this.now();
    const status = (store: Dependency): DependencyStatus => {
      const streak = this.streaks.get(store);
      return streak !== undefined && at - streak.last <= WINDOW_MS
        ? { status: 'degraded', since: new Date(streak.since).toISOString() }
        : { status: 'ok', since: null };
    };
    return Object.fromEntries(
      DEPENDENCIES.map((store) => [store, status(store)]),
    ) as DependencyHealthView;
  }
}

/** This process's view: the places that see an outage report to it, the admin route reads it. */
export const dependencyHealth = new DependencyHealth();
