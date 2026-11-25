/**
 * The sandbox broker's reap (P5.1 `/v1/reap`), as the run sweeper uses it
 * (E1): every sandbox whose labels include `labels` is removed. Resolves to
 * how many were; rejects when the broker could not be asked.
 */
export interface SandboxReaper {
  reap(labels: Readonly<Record<string, string>>): Promise<number>;
}

export interface PendingReap {
  id: string;
  labels: Record<string, string>;
  failures: number;
}

/**
 * Reaps the broker refused (CLEANUP-G6): the run they were for has ended, so
 * no later attempt reaps its sandboxes; they are asked for again, with
 * backoff, until the broker answers. One entry per label set.
 */
export interface PendingReaps {
  now(): Promise<number>;
  /** Records a refused reap, due at `at`; a label set already waiting keeps its entry. */
  defer(
    labels: Readonly<Record<string, string>>,
    at: number,
    errorType: string,
  ): Promise<void>;
  /** Entries due at `now`, oldest due first, at most `limit`. */
  due(now: number, limit: number): Promise<PendingReap[]>;
  done(id: string): Promise<void>;
  later(id: string, at: number, errorType: string): Promise<void>;
}

/**
 * The broker's persistent folders of one session (`/v1/erase-session`, E1):
 * an engine run keeps its session's workspace on the broker's data volume
 * between sandboxes, so erasing the session removes it. Resolves to how many
 * folders went; rejects when the broker could not be asked (the erasure step
 * is retried, never skipped).
 */
export interface SandboxFolderEraser {
  eraseSession(orgId: string, sessionId: string): Promise<number>;
}
