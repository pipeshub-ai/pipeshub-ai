import type {
  PayloadReferences,
  PayloadStore,
  TransactionalPayloadStore,
} from '../../../../modules/agent-harness/ports/payload-store';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { MongoPayloadStore } from './mongo';
import { PgPayloadStore } from './pg';

export type HarnessPayloadStore = PayloadStore &
  PayloadReferences<Tx> &
  TransactionalPayloadStore<Tx>;

export const createPayloadStore = (): HarnessPayloadStore =>
  pickHarnessStore<HarnessPayloadStore>('agent-harness/payload-store', {
    mongo: () => new MongoPayloadStore(),
    postgres: () => new PgPayloadStore(),
  });
