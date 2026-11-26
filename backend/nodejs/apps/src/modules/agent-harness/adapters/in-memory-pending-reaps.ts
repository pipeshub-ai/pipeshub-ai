import { pendingReapId } from '../domain/pending-reaps';
import { PendingReap, PendingReaps } from '../ports/sandbox-reaper';

interface Entry extends PendingReap {
  nextAttemptAt: number;
  lastErrorType: string;
}

export class InMemoryPendingReaps implements PendingReaps {
  private readonly entries = new Map<string, Entry>();

  constructor(private readonly clock: () => number = Date.now) {}

  now(): Promise<number> {
    return Promise.resolve(this.clock());
  }

  defer(
    labels: Readonly<Record<string, string>>,
    at: number,
    errorType: string,
  ): Promise<void> {
    const id = pendingReapId(labels);
    if (!this.entries.has(id)) {
      this.entries.set(id, {
        id,
        labels: { ...labels },
        failures: 1,
        nextAttemptAt: at,
        lastErrorType: errorType,
      });
    }
    return Promise.resolve();
  }

  due(now: number, limit: number): Promise<PendingReap[]> {
    return Promise.resolve(
      [...this.entries.values()]
        .filter((entry) => entry.nextAttemptAt <= now)
        .sort((a, b) => a.nextAttemptAt - b.nextAttemptAt)
        .slice(0, limit)
        .map(({ id, labels, failures }) => ({
          id,
          labels: { ...labels },
          failures,
        })),
    );
  }

  done(id: string): Promise<void> {
    this.entries.delete(id);
    return Promise.resolve();
  }

  later(id: string, at: number, errorType: string): Promise<void> {
    const entry = this.entries.get(id);
    if (entry !== undefined) {
      this.entries.set(id, {
        ...entry,
        failures: entry.failures + 1,
        nextAttemptAt: at,
        lastErrorType: errorType,
      });
    }
    return Promise.resolve();
  }
}
