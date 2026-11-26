/** The run an inline claimer is told about: ids only, never input or credentials. */
export interface InlineClaimTarget {
  orgId: string;
  runId: string;
}

/**
 * Tells the process that serves inline runs (the query service) to claim a
 * run it can start at once. A hint, not a start command: the claim in Node
 * stays authoritative, and a hint that is lost or refused costs only
 * `T_CLAIM_MS` before the sweeper dispatches the run. Never rejects.
 */
export interface InlineClaimHint {
  hint(target: InlineClaimTarget): Promise<void>;
}
