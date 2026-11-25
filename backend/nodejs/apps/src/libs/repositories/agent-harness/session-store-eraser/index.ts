import type { EnvelopeCipher } from '../../../../modules/agent-harness/application/envelope-cipher';
import type { RunKeyDigest } from '../../../../modules/agent-harness/domain/run-key-digest';
import type { SessionStoreEraser } from '../../../../modules/agent-harness/ports/session-erasure';
import { pickHarnessStore } from '../stores';
import { MongoSessionStoreEraser } from './mongo';
import { PgSessionStoreEraser } from './pg';

export const createSessionStoreEraser = (
  cipher: EnvelopeCipher,
  runKeys?: RunKeyDigest,
): SessionStoreEraser =>
  pickHarnessStore<SessionStoreEraser>('agent-harness/session-store-eraser', {
    mongo: () => new MongoSessionStoreEraser(cipher, runKeys),
    postgres: () => new PgSessionStoreEraser(cipher, runKeys),
  });
