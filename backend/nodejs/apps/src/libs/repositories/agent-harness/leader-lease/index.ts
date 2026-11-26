import type { LeaderLease } from '../../../../modules/agent-harness/ports/leader-lease';
import { pickHarnessStore } from '../stores';
import { MongoLeaderLease } from './mongo';
import { PgLeaderLease } from './pg';

export const createLeaderLease = (): LeaderLease =>
  pickHarnessStore<LeaderLease>('agent-harness/leader-lease', {
    mongo: () => new MongoLeaderLease(),
    postgres: () => new PgLeaderLease(),
  });
