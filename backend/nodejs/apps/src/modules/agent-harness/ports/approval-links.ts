/**
 * Writes a wait's outbox entry inside the commit transaction `tx`, due for the sweep `APPROVER_NOTICE_GRACE_MS`
 * after `createdAt` (the commit's own process sends first).
 */
export interface ApproverNoticeOutbox<Tx = unknown> {
  enqueue(
    notice: { id: string; orgId: string; runId: string; createdAt: number },
    tx: Tx,
  ): Promise<void>;
}

/**
 * AP-08: what is sent when a commit makes a run wait for a person (bound by
 * `AGENT_HARNESS_APPROVER_LINK_NOTICES`): `runWaiting` once the commit landed,
 * `sweepDue` on each sweeper tick for what the outbox still holds. Unbound:
 * nothing is sent beyond the Inbox.
 */
export interface WaitingRunNotices {
  runWaiting(run: {
    orgId: string;
    runId: string;
    revision: number;
  }): Promise<void>;
  /** The notices sent in this tick. */
  sweepDue(limit: number): Promise<number>;
}
