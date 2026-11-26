import { DispatchRecord } from '../domain/execution';

/** A run some claimer may now take: ids and the transition only, never input. */
export type RunReadyNotice = Pick<DispatchRecord, 'orgId' | 'runId' | 'kind'>;

/**
 * Tells idle claimers that runs became claimable (NFR-48: notify, never
 * poll, between a user's action and its first effect). Published only after
 * the transaction that made them claimable commits; a lost notice costs one
 * poll interval, never the run, because the outbox row and the queue stay
 * the truth. Never rejects: an adapter logs its own failure.
 * Adapters: Redis pub/sub on `[<namespace>:]harness:run-ready`
 * (`app/harness/run_ready.py` subscribes) and in-memory.
 */
export interface RunReadyNotifier {
  notify(notices: readonly RunReadyNotice[]): Promise<void>;
}

export const noRunReady: RunReadyNotifier = { notify: () => Promise.resolve() };
