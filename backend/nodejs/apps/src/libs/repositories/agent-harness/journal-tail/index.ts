import type { HarnessLogger } from '../../../../modules/agent-harness/application/harness-log';
import type { JournalTail } from '../../../../modules/agent-harness/ports/journal-tail';
import { pickHarnessStore } from '../stores';
import { MongoJournalTail } from './mongo';
import { PgJournalTail } from './pg';

/** One per process: it serves every follower of every run. */
export const createJournalTail = (logger?: HarnessLogger): JournalTail =>
  pickHarnessStore<JournalTail>('agent-harness/journal-tail', {
    mongo: () => new MongoJournalTail(undefined, logger),
    postgres: () => new PgJournalTail(undefined, logger),
  });
