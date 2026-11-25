import type { BudgetUsage } from '../domain/budget-ledger';

/**
 * What production sees of each chat path (DUR3 condition f): today's loop
 * (`legacy`) and the durable harness (`durable`), side by side on the same
 * series, so a rollout or a rollback can be judged by error rate, answer
 * rate, latency, cost per turn and resumes.
 */
export type ChatPath = 'legacy' | 'durable';

/** An agent's chat, or the built-in assistant's (`/chat/stream` modes). */
export type ChatSurface = 'agent' | 'assistant';

/**
 * How one turn ended. `disconnected` is legacy's only: the browser went away
 * mid-stream (a durable run carries on without it and ends as one of the others).
 */
export type ChatTurnOutcome =
  'answered' | 'needs_input' | 'cancelled' | 'failed' | 'disconnected';

/** How a worker came to hold a durable run: first claim, or a resume after a lost lease. */
export type ClaimSource = 'queued' | 'expired_lease';

export interface ChatPathMetrics {
  turnEnded(
    path: ChatPath,
    surface: ChatSurface,
    outcome: ChatTurnOutcome,
    durationS: number,
  ): void;
  /**
   * A worker took the run. `firstClaimWaitS`: on its first claim only, seconds from when it could first be claimed
   * (admission, or a later `notBefore`) to the claim: the claim latency (OB-04).
   */
  claimed(from: ClaimSource, firstClaimWaitS?: number): void;
  /** A worker command the store had already applied, sent again (a duplicate the journal absorbed). */
  commitReplayed(): void;
  /** One model call's metered charge on a durable run's budget account. */
  modelCharged(usd: number, tokens: number, usage?: BudgetUsage): void;
  /**
   * An ended run's own calls' usage, as its budget account summed it (C5.05, SLO #11): its cache-read share of input
   * is one sample, so a regression shows per run, not only as the fleet's token ratio.
   */
  runCacheRead(usage: BudgetUsage): void;
  /** A crash-looping run the worker quarantined (OB-05). */
  runQuarantined(): void;
  /** SEC-D-13: a worker's commit asked a person `count` approvals or questions (`interaction.requested`). */
  approvalsRequested(orgId: string, count: number): void;
}

export const noChatPathMetrics: ChatPathMetrics = {
  turnEnded: () => undefined,
  claimed: () => undefined,
  commitReplayed: () => undefined,
  modelCharged: () => undefined,
  runCacheRead: () => undefined,
  runQuarantined: () => undefined,
  approvalsRequested: () => undefined,
};
