/**
 * The JournalTail contract on `MongoJournalTail`: a change stream on a replica
 * set, fed by real sealed journal commits (run as execution-store.mongo.itest.ts says).
 */
import 'reflect-metadata';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { MongoJournalStore } from '../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoJournalTail } from '../../../src/libs/repositories/agent-harness/journal-tail/mongo';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { testCipher } from './support/data-protection';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  resetHarnessMongo,
} from './support/harness-mongo';
import { position, turnStarted } from './support/journal-fixtures';
import { describeJournalTailContract } from './support/journal-tail-contract';

describe('JournalTail on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  const journal = new SealedJournalStore(new MongoJournalStore(), testCipher());
  const service = new JournalService(new MongoPayloadStore(), testCipher());

  describeJournalTailContract('mongo change stream', async () => {
    await resetHarnessMongo();
    return {
      tail: new MongoJournalTail(),
      grow: async (orgId, runId) => {
        const prepared = await service.prepare({
          ...position({ orgId, runId, sessionId: `ses-${runId}`, streamId: `ses-${runId}` }),
          writer: 'worker',
          events: [turnStarted(1)],
        });
        return (await inMongoTransaction((session) => journal.append(prepared, session))).lastSeq;
      },
    };
  });
});
