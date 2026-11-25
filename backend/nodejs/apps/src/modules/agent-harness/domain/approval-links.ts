/**
 * What the commit that makes a run wait records for the notices sent about it
 * (AP-08, WP56-OUTBOX): the channels a notice may take and the outbox entry
 * of one wait. Who is told, and what the notice carries, is the bound
 * `WaitingRunNotices`'s.
 */

/** Where a notice about a waiting run is sent. */
export type ApprovalLinkChannel = 'slack' | 'email';

/** The commit's own process sends first; a sweep takes a wait's notices only after this. */
export const APPROVER_NOTICE_GRACE_MS = 30_000;

/** WP56-OUTBOX: the outbox entry of one wait: the run and the revision the commit that made it wait wrote. */
export const approverNoticeId = (
  orgId: string,
  runId: string,
  revision: number,
): string => `${orgId}:${runId}:${String(revision)}`;
