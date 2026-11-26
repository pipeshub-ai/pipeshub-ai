import type { DispatchOutbox } from '../../../../modules/agent-harness/ports/dispatch-outbox';
import type { RunReadyNotifier } from '../../../../modules/agent-harness/ports/run-ready';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { MongoDispatchOutbox } from './mongo';
import { PgDispatchOutbox } from './pg';

export const createDispatchOutbox = (
  ready?: RunReadyNotifier,
): DispatchOutbox<Tx> =>
  pickHarnessStore<DispatchOutbox<Tx>>('agent-harness/dispatch-outbox', {
    mongo: () => new MongoDispatchOutbox(ready),
    postgres: () => new PgDispatchOutbox(ready),
  });
