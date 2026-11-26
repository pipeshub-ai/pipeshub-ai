import type { SessionForkStore } from '../../../../modules/agent-harness/ports/session-forks';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { HarnessSessionForkStore } from './mongo';
import { PgHarnessSessionForkStore } from './pg';

export const createSessionForkStore = (): SessionForkStore<Tx> =>
  pickHarnessStore<SessionForkStore<Tx>>('agent-harness/session-forks', {
    mongo: () => new HarnessSessionForkStore(),
    postgres: () => new PgHarnessSessionForkStore(),
  });
