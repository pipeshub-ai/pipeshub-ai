/**
 * What a store write needs of the control-plane transaction (CPD-1's `CpTx`
 * satisfies it): the org it writes in, the database session to write through,
 * and a way to join the transaction. No store opens its own transaction.
 */
export interface StoreTx {
  readonly orgId: string;
  readonly session: unknown;
  enlist(write: () => Promise<void>): Promise<void>;
}

export interface Page<T> {
  readonly items: readonly T[];
  readonly next: string | null;
}
