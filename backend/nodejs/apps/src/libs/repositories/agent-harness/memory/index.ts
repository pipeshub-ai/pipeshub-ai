import type {
  MemoryEraser,
  MemoryStore,
} from '../../../../modules/agent-harness/ports/memory-store';
import { pickHarnessStore } from '../stores';
import { MongoMemoryStore } from './mongo';
import { PgMemoryStore } from './pg';

export const createMemoryStore = (): MemoryStore & MemoryEraser =>
  pickHarnessStore<MemoryStore & MemoryEraser>('agent-harness/memory', {
    mongo: () => new MongoMemoryStore(),
    postgres: () => new PgMemoryStore(),
  });
