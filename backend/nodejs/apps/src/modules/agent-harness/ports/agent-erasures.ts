/** An agent whose memories are to be erased once its delete is known to have committed (H4, H5). */
export interface PendingAgentErasure {
  orgId: string;
  agentKey: string;
  /**
   * The query service answered the delete with a 200 (H4's entries, written
   * only after it). Unconfirmed: written before the delete, whose answer was
   * never seen; the sweeper asks whether the agent is deleted before erasing.
   */
  confirmed: boolean;
  /** Failed attempts so far, the deletion's own included. */
  failures: number;
  requestedAt: number;
  nextAttemptAt: number;
  lastErrorType: string | null;
}

/**
 * Agent erasures still to do, one entry per (org, agent), however often it is
 * asked for. The deletion path writes an unconfirmed entry before it asks the
 * query service to delete the agent (only it decides who may), so an
 * interrupted request still leaves the intent; the erasure it stands for is
 * idempotent, so retrying a done one is harmless.
 */
export interface AgentErasureQueue {
  /**
   * Before the delete: an unconfirmed entry, due `afterMs` from now. An
   * unconfirmed entry already there is requested again from now (a newer
   * delete of the same agent); a confirmed one is left as it is.
   */
  intend(orgId: string, agentKey: string, afterMs: number): Promise<void>;
  /**
   * The agent was deleted and the erasure failed: the entry is confirmed and
   * due at once. A confirmed entry keeps its own times and failures.
   */
  enqueue(orgId: string, agentKey: string, errorType: string): Promise<void>;
  /** Entries due at `now`, oldest due first, at most `limit`. */
  due(now: number, limit: number): Promise<PendingAgentErasure[]>;
  /** Failed again: due at `at`, one more failure counted. */
  retryLater(
    entry: PendingAgentErasure,
    at: number,
    errorType: string,
  ): Promise<void>;
  /** Not answered yet (the agent not deleted so far): due at `at`, no failure counted. */
  postpone(entry: PendingAgentErasure, at: number): Promise<void>;
  /** Done: the entry is removed. */
  settle(orgId: string, agentKey: string): Promise<void>;
  /**
   * The agent was not deleted: the entry is removed only if it is still this
   * unconfirmed intent (not requested again since, nor confirmed). True when
   * it was removed.
   */
  withdraw(entry: PendingAgentErasure): Promise<boolean>;
}

/**
 * What the query service holds about an agent, as the entry's org may learn
 * it: `deleted` only for the agent's tombstone; another org's agent reads as
 * `absent`, like an unknown key.
 */
export type AgentDeletionState = 'deleted' | 'live' | 'absent';

export const AGENT_DELETION_STATES: readonly AgentDeletionState[] = [
  'deleted',
  'live',
  'absent',
];

/** Erases what an agent owns once it is deleted (C-14 AGENT_DATA_STORES); answers how many items it changed. */
export interface AgentDataEraser {
  eraseAgent(orgId: string, agentKey: string): Promise<number>;
}

/** Whether an agent's delete committed (H5): the query service's tombstone, read with a service token. */
export interface AgentDeletionLookup {
  /** Throws when the query service cannot answer; never guesses. */
  state(orgId: string, agentKey: string): Promise<AgentDeletionState>;
}
