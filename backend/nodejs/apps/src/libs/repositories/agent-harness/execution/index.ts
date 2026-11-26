import type { ApproverNoticeOutbox } from '../../../../modules/agent-harness/ports/approval-links';
import type { ConversationWriter } from '../../../../modules/agent-harness/ports/conversation-writer';
import type { DispatchOutbox } from '../../../../modules/agent-harness/ports/dispatch-outbox';
import type {
  ActorRuns,
  ExecutionStore,
  OperatorRuns,
} from '../../../../modules/agent-harness/ports/execution-store';
import type { JournalStore } from '../../../../modules/agent-harness/ports/journal-store';
import type { Tx } from '../../uow';
import { pickHarnessStore } from '../stores';
import { mongoServerTime } from '../time/mongo';
import { pgServerTime } from '../time/pg';
import { MongoExecutionStore, runIdsOfSession } from './mongo';
import { PgExecutionStore, pgRunIdsOfSession } from './pg';

export type HarnessExecutionStore = ExecutionStore & ActorRuns & OperatorRuns;

export interface ExecutionStoreParts {
  conversations: ConversationWriter<Tx>;
  dispatches: DispatchOutbox<Tx>;
  journal: JournalStore<Tx>;
  approverNotices?: ApproverNoticeOutbox<Tx> | null;
}

/** Run state after admission, on the database clock of the selected document store. */
export const createExecutionStore = (
  parts: ExecutionStoreParts,
): HarnessExecutionStore =>
  pickHarnessStore<HarnessExecutionStore>('agent-harness/execution', {
    mongo: () =>
      new MongoExecutionStore(
        parts.conversations,
        parts.dispatches,
        mongoServerTime,
        parts.journal,
        parts.approverNotices ?? null,
      ),
    postgres: () =>
      new PgExecutionStore(
        parts.conversations,
        parts.dispatches,
        pgServerTime,
        parts.journal,
        parts.approverNotices ?? null,
      ),
  });

/** The ids of every run of one conversation. */
export const sessionRunIds = (
  orgId: string,
  sessionId: string,
): Promise<string[]> =>
  pickHarnessStore<{ ids: typeof runIdsOfSession }>('agent-harness/execution', {
    mongo: () => ({ ids: runIdsOfSession }),
    postgres: () => ({ ids: pgRunIdsOfSession }),
  }).ids(orgId, sessionId);
