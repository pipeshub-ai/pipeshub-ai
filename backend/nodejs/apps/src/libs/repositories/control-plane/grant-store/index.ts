import type { GrantStore } from '../../../../modules/control-plane/ports/grant-store';
import { pickHarnessStore } from '../../agent-harness/stores';
import { MongoGrantStore } from './mongo';
import { PgGrantStore } from './pg';

export const createGrantStore = (): GrantStore =>
  pickHarnessStore<GrantStore>('control-plane/grant-store', {
    mongo: () => new MongoGrantStore(),
    postgres: () => new PgGrantStore(),
  });
