import type { JournalStore } from '../../../../modules/agent-harness/ports/journal-store';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { MongoJournalStore } from './mongo';
import { PgJournalStore } from './pg';

export const createJournalStore = (): JournalStore<Tx> =>
  pickHarnessStore<JournalStore<Tx>>('agent-harness/journal', {
    mongo: () => new MongoJournalStore(),
    postgres: () => new PgJournalStore(),
  });
