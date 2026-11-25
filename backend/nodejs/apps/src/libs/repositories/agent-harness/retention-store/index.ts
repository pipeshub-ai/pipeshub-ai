import type { RetentionStore } from '../../../../modules/agent-harness/ports/session-retention';
import { pickHarnessStore } from '../stores';
import { MongoRetentionStore } from './mongo';
import { PgRetentionStore } from './pg';

export const createRetentionStore = (): RetentionStore =>
  pickHarnessStore<RetentionStore>('agent-harness/retention-store', {
    mongo: () => new MongoRetentionStore(),
    postgres: () => new PgRetentionStore(),
  });
