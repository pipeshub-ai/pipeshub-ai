import { DispatchRecord } from '../domain/execution';

/**
 * Durable intent to wake a worker. Records are written inside the caller's
 * transaction `tx`, so a dispatch exists if and only if its state change does.
 */
export interface DispatchOutbox<Tx = unknown> {
  enqueue(records: DispatchRecord[], tx: Tx): Promise<void>;
}
