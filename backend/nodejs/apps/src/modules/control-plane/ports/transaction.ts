import type { ActorRef, ConfigChange } from '../domain/config-change';

export interface WriteContext {
  readonly orgId: string;
  readonly actor: ActorRef;
  readonly approvalRef?: string;
}

/** The value of an external effect, readable once the transaction has committed. */
export interface Applied<R> {
  readonly value: R;
}

/** One control-plane write in progress. Stores take it; no store opens its own transaction. */
export interface CpTx {
  readonly orgId: string;
  /** The org head revision this write commits as. */
  readonly headRevision: number;
  /** The adapter's database session (a Mongo `ClientSession`, or undefined) for stores that write through it. */
  readonly session: unknown;
  /** Runs `write` so that it commits or rolls back with this transaction. */
  enlist(write: () => Promise<void>): Promise<void>;
  /**
   * A write to a store that cannot join the transaction (the configuration
   * KV store, the vault, another service). It runs after the audit entry is
   * staged and before the commit; a `ControlPlaneRetryableError` from it
   * plans the whole write again.
   */
  external<R>(effect: () => Promise<R>): Applied<R>;
}

export interface Committed<T> {
  readonly result: T;
  readonly headRevision: number;
}

export type Work<T> = (
  tx: CpTx,
) => Promise<{ result: T; change: ConfigChange }>;

/**
 * Makes one control-plane write atomic with the org head revision, its
 * `config.changed` audit entry and its change event (03-interfaces §2.1).
 * `work` may run more than once, so it must read what it needs inside.
 */
export interface ControlPlaneTransaction {
  run<T>(ctx: WriteContext, work: Work<T>): Promise<Committed<T>>;
}
