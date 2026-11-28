import type { ErasureBackfillStore } from '../../../../modules/agent-harness/ports/erasure-backfill';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { MongoErasureBackfillStore } from './mongo';
import { PgErasureBackfillStore } from './pg';

export const createErasureBackfillStore = (): ErasureBackfillStore<Tx> =>
  pickHarnessStore<ErasureBackfillStore<Tx>>('agent-harness/erasure-backfill', {
    mongo: () => new MongoErasureBackfillStore(),
    postgres: () => new PgErasureBackfillStore(),
  });
