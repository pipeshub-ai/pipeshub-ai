/**
 * The run store's current state as an operator's dashboard reads it (OB-04, OB-05): counts only, never an org,
 * session or run id.
 */
export interface RunStateSnapshot {
  /** Runs in each status that is not terminal; a status with none may be absent. */
  byStatus: Readonly<Record<string, number>>;
  /** Sessions (conversations) with a root run that has not ended. */
  activeSessions: number;
  /** `running` runs whose lease has expired: their worker is gone and the sweeper has not re-queued them yet. */
  leaseExpired: number;
  /** Seconds since the oldest `running` run was admitted; `null` when none runs. */
  oldestRunningAgeS: number | null;
  /** Seconds the longest-waiting `waiting_approval` run has waited for a person (ADR-OPS-01 D10); `null` when none waits. */
  oldestApprovalWaitS?: number | null;
}

export interface RunStateReader {
  snapshot(): Promise<RunStateSnapshot>;
}
