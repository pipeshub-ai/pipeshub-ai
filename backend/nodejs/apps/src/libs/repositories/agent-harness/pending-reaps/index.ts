import type { PendingReaps } from '../../../../modules/agent-harness/ports/sandbox-reaper';
import { pickHarnessStore } from '../stores';
import { MongoPendingReaps } from './mongo';
import { PgPendingReaps } from './pg';

export const createPendingReaps = (): PendingReaps =>
  pickHarnessStore<PendingReaps>('agent-harness/pending-reaps', {
    mongo: () => new MongoPendingReaps(),
    postgres: () => new PgPendingReaps(),
  });
