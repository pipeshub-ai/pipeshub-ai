import type { DataKeyStore } from '../../../../modules/agent-harness/ports/data-keys';
import { pickHarnessStore } from '../stores';
import { MongoDataKeyStore } from './mongo';
import { PgDataKeyStore } from './pg';

export const createDataKeyStore = (): DataKeyStore =>
  pickHarnessStore<DataKeyStore>('agent-harness/data-protection', {
    mongo: () => new MongoDataKeyStore(),
    postgres: () => new PgDataKeyStore(),
  });
