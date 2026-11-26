import type {
  ErasureJobStore,
  SessionRunLookup,
} from '../../../../modules/agent-harness/ports/session-erasure';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { MongoErasureJobStore, MongoSessionRunLookup } from './mongo';
import { PgErasureJobStore, PgSessionRunLookup } from './pg';

export const createErasureJobStore = (): ErasureJobStore<Tx> =>
  pickHarnessStore<ErasureJobStore<Tx>>('agent-harness/session-erasure', {
    mongo: () => new MongoErasureJobStore(),
    postgres: () => new PgErasureJobStore(),
  });

/** A session's runs, with the `/usage` read of the runs a person started. */
export type HarnessSessionRunLookup = Required<SessionRunLookup> &
  Pick<PgSessionRunLookup, 'ownRootRunsOf'>;

export const createSessionRunLookup = (): HarnessSessionRunLookup =>
  pickHarnessStore<HarnessSessionRunLookup>('agent-harness/session-erasure', {
    mongo: () => new MongoSessionRunLookup(),
    postgres: () => new PgSessionRunLookup(),
  });
