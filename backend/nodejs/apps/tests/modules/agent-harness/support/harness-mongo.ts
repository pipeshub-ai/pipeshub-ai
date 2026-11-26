import type { Tx } from '../../../../src/libs/repositories/uow';
/**
 * Shared setup for the harness tests that need a real MongoDB replica set
 * (`*.mongo.itest.ts`; run instructions in execution-store.mongo.itest.ts).
 */
import type { ApproverNoticeOutbox } from '../../../../src/modules/agent-harness/ports/approval-links';
import { AgentSandboxSnapshotModel } from '../../../../src/libs/repositories/agent-harness/sandbox-snapshots/schema/sandbox-snapshots.schema';
import { RunHistoryContent } from '../../../../src/modules/agent-harness/application/run-history-content';
import { RunInputSeal } from '../../../../src/modules/agent-harness/application/run-input-seal';
import { migrateHarnessIndexes } from '../../../../src/libs/repositories/agent-harness/migrations/mongo';
import { MongoChildRunStore } from '../../../../src/libs/repositories/agent-harness/child-runs/mongo';
import { MongoBudgetLedger } from '../../../../src/libs/repositories/agent-harness/budget-ledger/mongo';
import {
  AgentBudgetAccountModel,
  AgentBudgetReservationModel,
} from '../../../../src/libs/repositories/agent-harness/budget-ledger/schema/budget-ledger.schema';
import mongoose, { Model } from 'mongoose';
import { MongoExecutionStore } from '../../../../src/libs/repositories/agent-harness/execution/mongo';
import { MongoRunAdmissionStore } from '../../../../src/libs/repositories/agent-harness/run-admission-store/mongo';
import {
  AgentAdmissionQuotaModel,
  AgentAdmissionReceiptModel,
  AgentCommandReceiptModel,
  AgentEventCounterModel,
  AgentEventModel,
  AgentHarnessLeaseModel,
  AgentNextRunModel,
  AgentRunModel,
} from '../../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { MongoDispatchOutbox } from '../../../../src/libs/repositories/agent-harness/dispatch-outbox/mongo';
import {
  AgentJournalPayloadModel,
  SessionEventModel,
  SessionStreamCounterModel,
} from '../../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { MongoJournalStore } from '../../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoPayloadStore } from '../../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { AgentWorkspaceHeadModel } from '../../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import {
  fixedMongoTime,
  MongoTime,
  mongoServerTime,
} from '../../../../src/libs/repositories/agent-harness/time/mongo';
import { DispatchOutbox } from '../../../../src/modules/agent-harness/ports/dispatch-outbox';
import { ConversationWriter } from '../../../../src/modules/agent-harness/ports/conversation-writer';
import { StoreClock } from '../../../../src/modules/agent-harness/domain/execution';
import { OutboxEvent } from '../../../../src/libs/repositories/outbox/schema/outbox.schema';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { HarnessChatSessionRepository } from '../../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';
import { HarnessConversationWriter } from '../../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import { HarnessHistorySource } from '../../../../src/libs/repositories/agent-harness/history-source/mongo';
import { ContractBackend, TranscriptProbe } from './execution-store-contract';
import { AdmissionQuotas } from '../../../../src/modules/agent-harness/domain/admission-quotas';
import { SealedJournalStore } from '../../../../src/modules/agent-harness/application/sealed-journal-store';
import { AgentHarnessDataKeyModel } from '../../../../src/libs/repositories/agent-harness/data-protection/schema/data-protection.schema';
import { testCipher, testRunKeys } from './data-protection';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { SessionImportContent } from '../../../../src/modules/agent-harness/application/session-import-content';
import { HarnessLogger } from '../../../../src/modules/agent-harness/application/harness-log';
import { AdmissionQuotaMetrics } from '../../../../src/libs/repositories/agent-harness/admission-quota-ledger/mongo';
import { AuditedJournalStore } from '../../../../src/modules/agent-harness/application/audited-journal-store';
import { PayloadReferencingJournalStore } from '../../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import {
  auditLinksIdle,
  MongoAuditChainStore,
} from '../../../../src/libs/repositories/agent-harness/audit/mongo';
import {
  AgentAuditAnchorModel,
  AgentAuditHeadModel,
  AgentAuditPendingModel,
  AgentAuditRecordModel,
} from '../../../../src/libs/repositories/agent-harness/audit/schema/audit.schema';

import { DEPLOYMENT_HEAD_ID, MongoHeadRevisions } from '../../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';

export const harnessMongoUri = (): string => {
  const uri =
    process.env.MONGO_IT_URI ?? process.env.AGENT_HARNESS_TEST_MONGO_URI;
  if (!uri) {
    throw new Error(
      'MONGO_IT_URI (a replica-set URI) must be set for the harness Mongo tests',
    );
  }
  return uri;
};

const baseModels: Array<Model<never>> = [
  ChatSession,
  ChatSessionMessage,
  AgentRunModel,
  AgentEventModel,
  AgentEventCounterModel,
  AgentCommandReceiptModel,
  AgentAdmissionReceiptModel,
  AgentHarnessLeaseModel,
  AgentWorkspaceHeadModel,
  SessionEventModel,
  SessionStreamCounterModel,
  AgentJournalPayloadModel,
  AgentAdmissionQuotaModel,
  AgentHarnessDataKeyModel,
  AgentAuditRecordModel,
  AgentAuditPendingModel,
  AgentAuditHeadModel,
  AgentAuditAnchorModel,
  OutboxEvent,
  AgentBudgetAccountModel,
  AgentBudgetReservationModel,
  AgentNextRunModel,
  CpOrgHeadModel,
  AgentSandboxSnapshotModel,
] as unknown as Array<Model<never>>;

const registered: Array<Model<never>> = [...baseModels];

/** Models a test file adds to init and reset alongside the base set. */
export const registerHarnessModels = (models: Array<Model<never>>): void => {
  for (const model of models) {
    if (!registered.includes(model)) registered.push(model);
  }
};

export const harnessModels = (): ReadonlyArray<Model<never>> => registered;

// A hang guard, not a bound: the hook builds every harness index in a new database, on a MongoDB the lanes
// share and that builds three indexes at a time whoever asked.
const CONNECT_HOOK_TIMEOUT_MS = 300_000;

/**
 * A `before` hook of its own (`before(connectHarnessMongo)`): it raises that hook's timeout and the suite's
 * tests keep theirs. Called inside another hook it would run under that hook's timeout, so it refuses.
 */
export async function connectHarnessMongo(
  this: Mocha.Context | undefined,
): Promise<void> {
  if (!this) {
    throw new Error('pass connectHarnessMongo to before(): it sets its own hook timeout');
  }
  this.timeout(CONNECT_HOOK_TIMEOUT_MS);
  await mongoose.connect(harnessMongoUri(), {
    dbName: `agent_harness_it_${Date.now()}_${process.pid}`,
    // What commandsDuring (commit-store-commands) counts, as the rig does.
    monitorCommands: true,
  });
  await Promise.all(registered.map((model) => model.init()));
  // The harness schemas opt out of autoIndex: their indexes come from the migrations.
  await migrateHarnessIndexes(mongoose.connection.db!);
}

export const resetHarnessMongo = async (): Promise<void> => {
  // A link still running from the test before would write its records after the reset.
  await auditLinksIdle();
  await Promise.all(registered.map((model) => model.deleteMany({} as never)));
};

export const disconnectHarnessMongo = async (): Promise<void> => {
  await auditLinksIdle();
  if (mongoose.connection.readyState === mongoose.ConnectionStates.connected) {
    await mongoose.connection.dropDatabase();
  }
  await mongoose.disconnect();
};

const dispatchPayload = (value: string): Record<string, unknown> =>
  (JSON.parse(value) as { payload: Record<string, unknown> }).payload;

export interface MongoBackendOptions {
  /** Plays DB time; omitted, the stores read `$$NOW` as in production. */
  clock?: StoreClock;
  dispatches?: DispatchOutbox<Tx>;
  conversations?: ConversationWriter<Tx>;
  quotas?: AdmissionQuotas;
  /** Where admission keeps an imported history (T3); inline only when omitted. */
  importContent?: SessionImportContent;
  /** Appends mark the payloads they cite as referenced, as production's journal does (T5). */
  payloadReferences?: boolean;
  /** Where admission claim-checks the events it prepares; the journal's own bound when omitted. */
  journalInlineMaxBytes?: number;
  /** A history part's size (QA22-F2); production's when omitted. */
  historyPartBytes?: number;
  /** Where admission and the audit chain log what they could not finish (PERF-C). */
  logger?: HarnessLogger;
  /** Counts the org window places admission gives back (PERF-C). */
  quotaMetrics?: AdmissionQuotaMetrics;
  /** `HARNESS_RUN_HISTORY_BY_REFERENCE`: false writes the history inline, as before PERF-B; it is read either way. */
  historyByReference?: boolean;
  /** WP56-OUTBOX: where a commit that makes a run wait writes its approver notices; none when omitted. */
  approverNotices?: ApproverNoticeOutbox<Tx>;
}

export const mongoTimeFor = (clock?: StoreClock): MongoTime =>
  clock ? fixedMongoTime(() => clock.now()) : mongoServerTime;

/**
 * The real conversation writer, able to fail its next transcript write
 * (L-11), and a read of what it wrote into the run's session.
 */
class ProbedConversationWriter extends HarnessConversationWriter {
  failNext = false;

  override async saveRunTranscript(
    ...args: Parameters<HarnessConversationWriter['saveRunTranscript']>
  ): Promise<boolean> {
    if (this.failNext) {
      this.failNext = false;
      // Fail after a partial write, so a rollback that misses it would show.
      await super.saveRunTranscript(...args);
      throw new Error('conversation write failed');
    }
    return super.saveRunTranscript(...args);
  }
}

const probe = (writer: ProbedConversationWriter): TranscriptProbe => ({
  failNextWrite: () => {
    writer.failNext = true;
  },
  deleteConversation: async (run) => {
    await ChatSession.updateOne({ _id: run.sessionId }, { $set: { isDeleted: true } });
  },
  written: async (run) =>
    (
      await ChatSessionMessage.find({ sessionId: run.sessionId })
        .sort({ seq: 1 })
        .lean()
    )
      .filter((message) => message.messageType !== 'user_query')
      .map((message) => message as unknown as Record<string, unknown>),
});

/** A head row moved as a committed control-plane write moves it. */
const moveHead = async (id: string): Promise<void> => {
  await CpOrgHeadModel.updateOne(
    { _id: id },
    { $inc: { revision: 1 }, $set: { updatedAt: new Date(), lease: null } },
    { upsert: true },
  );
};

export const mongoBackend = ({
  clock,
  dispatches = new MongoDispatchOutbox(),
  conversations,
  quotas,
  importContent,
  payloadReferences = false,
  journalInlineMaxBytes,
  historyPartBytes,
  logger,
  quotaMetrics,
  historyByReference = true,
  approverNotices,
}: MongoBackendOptions = {}): ContractBackend => {
  const time = mongoTimeFor(clock);
  const payloads = new MongoPayloadStore();
  // By reference, as production composes it: a claim reads what admission stored.
  const history = new RunHistoryContent(testCipher(), payloads, payloads, historyPartBytes);
  // Sealed as production stores it (LCFIX): every Mongo contract case reads the query back through the seal.
  const inputs = new RunInputSeal(testCipher());
  const sealed = new SealedJournalStore(new MongoJournalStore(time), testCipher());
  // Audited and sealed as in production (O-1c, O-2a): every Mongo contract
  // case runs encrypted and chains its audit records.
  const journal = new AuditedJournalStore(
    payloadReferences ? new PayloadReferencingJournalStore(sealed, payloads) : sealed,
    new MongoAuditChainStore(logger),
  );
  const probed =
    conversations === undefined ? new ProbedConversationWriter() : null;
  const writer = conversations ?? (probed as ProbedConversationWriter);
  const budgets = new MongoBudgetLedger(time);
  return {
    ...(probed !== null ? { transcripts: probe(probed) } : {}),
    executionStore: new MongoExecutionStore(writer, dispatches, time, journal, approverNotices ?? null),
    journal,
    payloads,
    history,
    inputs,
    deletePayload: async (_orgId, ref) => {
      await AgentJournalPayloadModel.deleteOne({
        _id: ref.replace(/^mongo:/, ''),
      });
    },
    admissionStore: new MongoRunAdmissionStore({
      sessions: new HarnessChatSessionRepository(),
      history: new HarnessHistorySource(),
      conversations: writer,
      dispatches,
      transactionsAvailable: true,
      time,
      journal,
      quotas,
      journals: new JournalService(payloads, testCipher(), journalInlineMaxBytes),
      importContent,
      ...(historyByReference ? { historyContent: history } : {}),
      inputs,
      runKeys: testRunKeys(),
      ...(logger !== undefined ? { logger } : {}),
      ...(quotaMetrics !== undefined ? { quotaMetrics } : {}),
    }),
    dispatches: async (orgId, runId) =>
      (
        await OutboxEvent.find({ orderingKey: runId })
          .sort({ createdAt: 1, _id: 1 })
          .lean()
      )
        .map((row) => dispatchPayload(row.value))
        .filter((payload) => payload.orgId === orgId)
        .map((payload) => ({
          commandId: payload.commandId as string,
          kind: payload.kind as string,
        })),
    childRuns: new MongoChildRunStore({ dispatches, time, journal, budgets, runKeys: testRunKeys(), inputs }),
    budgets,
    reset: resetHarnessMongo,
    controlPlane: {
      heads: (orgId) => new MongoHeadRevisions().heads(orgId),
      commit: (orgId) => moveHead(orgId),
      moveDeployment: () => moveHead(DEPLOYMENT_HEAD_ID),
    },
  };
};
