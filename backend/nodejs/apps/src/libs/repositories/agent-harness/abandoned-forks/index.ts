import type { AbandonedForks } from '../../../../modules/agent-harness/ports/session-forks';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { MongoAbandonedForks } from './mongo';
import { PgAbandonedForks } from './pg';

export const createAbandonedForks = (): AbandonedForks<Tx> =>
  pickHarnessStore<AbandonedForks<Tx>>('agent-harness/abandoned-forks', {
    mongo: () => new MongoAbandonedForks(),
    postgres: () => new PgAbandonedForks(),
  });
