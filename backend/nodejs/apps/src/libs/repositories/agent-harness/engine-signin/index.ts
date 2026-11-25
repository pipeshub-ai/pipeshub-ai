import type { EngineSignInStore } from '../../../../modules/agent-harness/ports/engine-signins';
import { pickHarnessStore } from '../stores';
import { MongoEngineSignIns } from './mongo';
import { PgEngineSignIns } from './pg';

export const createEngineSignIns = (): EngineSignInStore =>
  pickHarnessStore<EngineSignInStore>('agent-harness/engine-signin', {
    mongo: () => new MongoEngineSignIns(),
    postgres: () => new PgEngineSignIns(),
  });
