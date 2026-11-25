/**
 * The journal HTTP server's Mongo mode (P9): the services its routes use, over
 * the production Mongo adapters on a replica set, so a lane can restart Node,
 * step a primary down, or restore a session into a fresh database.
 *
 * What survives a restart is what survives in production: everything in Mongo
 * (runs, journal sealed and audited, payloads, workspaces, workspace and org
 * data keys, child runs, erasure jobs). The authority documents (worker
 * credential, attempt and gateway signing keys) live in KV in production; here
 * in JSON files under `JOURNAL_STATE_DIR`.
 */
import { mkdirSync, readFileSync, renameSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import mongoose, { ClientSession } from 'mongoose';
import {
  ConfigDocument,
  ConfigDocumentStore,
} from '../../../../src/modules/agent-harness/ports/config-document-store';
import { configureMongoose } from '../../../../src/libs/services/mongo.service';
import { migrateHarnessIndexes } from '../../../../src/libs/repositories/agent-harness/migrations/mongo';
import { MongoExecutionStore } from '../../../../src/libs/repositories/agent-harness/execution/mongo';
import { MongoRunAdmissionStore } from '../../../../src/libs/repositories/agent-harness/run-admission-store/mongo';
import { MongoJournalStore } from '../../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoPayloadStore } from '../../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { MongoDispatchOutbox } from '../../../../src/libs/repositories/agent-harness/dispatch-outbox/mongo';
import { MongoAuditChainStore } from '../../../../src/libs/repositories/agent-harness/audit/mongo';
import { MongoChildRunStore } from '../../../../src/libs/repositories/agent-harness/child-runs/mongo';
import { MongoBudgetLedger } from '../../../../src/libs/repositories/agent-harness/budget-ledger/mongo';
import { MongoDataKeyStore } from '../../../../src/libs/repositories/agent-harness/data-protection/mongo';
import {
  MongoWorkspaceKeyStore,
  MongoWorkspaceRepository,
  MongoWorkspaceWriterStore,
} from '../../../../src/libs/repositories/agent-harness/workspace/mongo';
import {
  MongoErasureJobStore,
  MongoSessionRunLookup,
} from '../../../../src/libs/repositories/agent-harness/session-erasure/mongo';
import { MongoSessionStoreEraser } from '../../../../src/libs/repositories/agent-harness/session-store-eraser/mongo';
import { inMongoTransaction } from '../../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { mongoServerTime } from '../../../../src/libs/repositories/agent-harness/time/mongo';
import { AuditedJournalStore } from '../../../../src/modules/agent-harness/application/audited-journal-store';
import { SealedJournalStore } from '../../../../src/modules/agent-harness/application/sealed-journal-store';
import { PayloadReferencingJournalStore } from '../../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { SessionWorkspaceKeys } from '../../../../src/modules/agent-harness/application/session-workspace-keys';
import {
  WorkspaceService,
  WorkspaceServiceOptions,
} from '../../../../src/modules/agent-harness/application/workspace-service';
import { ChildRunService } from '../../../../src/modules/agent-harness/application/child-run-service';
import { RunContentService } from '../../../../src/modules/agent-harness/application/run-content-service';
import { createErasureSteps } from '../../../../src/modules/agent-harness/application/session-erasure-steps';
import { SessionErasureRunner } from '../../../../src/modules/agent-harness/application/session-erasure';
import { HarnessChatSessionRepository } from '../../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';
import { HarnessConversationWriter } from '../../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import { HarnessHistorySource } from '../../../../src/libs/repositories/agent-harness/history-source/mongo';
import { HarnessConversationEraser } from '../../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { harnessModels, registerHarnessModels } from './harness-mongo';
import { AgentSessionErasureModel } from '../../../../src/libs/repositories/agent-harness/session-erasure/schema/session-erasure.schema';
import {
  AgentWorkspaceKeyModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceWriterModel,
} from '../../../../src/libs/repositories/agent-harness/workspace/schema/workspace.schema';
import { dataKeyRing, DataKeyRing, testRunKeys } from './data-protection';
import { ExecutionService } from '../../../../src/modules/agent-harness/application/execution-service';
import { RunHistoryContent } from '../../../../src/modules/agent-harness/application/run-history-content';
import { RunInputSeal } from '../../../../src/modules/agent-harness/application/run-input-seal';
import { RunSpec } from '../../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { FakeChatAttachments } from './chat-attachments';

/** A configuration document kept in a file: what a restarted process reads back, as KV gives production. */
export class FileConfigDocumentStore<T> implements ConfigDocumentStore<T> {
  constructor(private readonly path: string) {}

  private load(): { value: T | null; version: number } {
    if (!existsSync(this.path)) return { value: null, version: 0 };
    return JSON.parse(readFileSync(this.path, 'utf-8')) as { value: T | null; version: number };
  }

  async read(): Promise<ConfigDocument<T>> {
    const { value, version } = this.load();
    return { value, revision: value === null ? null : String(version) };
  }

  async replace(expected: string | null, value: T): Promise<boolean> {
    const current = this.load();
    if ((current.value === null ? null : String(current.version)) !== expected) return false;
    const aside = `${this.path}.${String(process.pid)}.tmp`;
    writeFileSync(aside, JSON.stringify({ value, version: current.version + 1 }));
    renameSync(aside, this.path);
    return true;
  }
}

export const fileStores = (dir: string) => {
  mkdirSync(dir, { recursive: true });
  const at = <T>(name: string): FileConfigDocumentStore<T> => new FileConfigDocumentStore<T>(join(dir, `${name}.json`));
  return { at };
};

const SPECS = 'p9JournalServerSpecs';

/** Specs the control route admitted, kept in the database so a restarted server still resolves first claims. */
export class PersistedSpecs {
  private readonly cache = new Map<string, RunSpec>();

  async load(): Promise<void> {
    const rows = await mongoose.connection.db!.collection<{ _id: string; spec: RunSpec }>(SPECS).find({}).toArray();
    for (const row of rows) this.cache.set(row._id, row.spec);
  }

  get(runId: string): RunSpec | undefined {
    return this.cache.get(runId);
  }

  async set(runId: string, spec: RunSpec): Promise<void> {
    this.cache.set(runId, spec);
    await mongoose.connection.db!.collection<{ _id: string; spec: RunSpec }>(SPECS).replaceOne(
      { _id: runId },
      { spec },
      { upsert: true },
    );
  }
}

export interface MongoWorld {
  keys: DataKeyRing;
  journals: JournalService;
  journal: AuditedJournalStore<ClientSession>;
  executionStore: MongoExecutionStore;
  admission: MongoRunAdmissionStore;
  childRuns: MongoChildRunStore;
  payloads: MongoPayloadStore;
  /** Run histories by reference (QA22-F2), as production composes them. */
  history: RunHistoryContent;
  inputs: RunInputSeal;
  workspaceKeys: SessionWorkspaceKeys;
  workspacesFor: (executions: WorkspaceServiceOptions['runs']) => WorkspaceService;
  childrenFor: (workspaces: WorkspaceService) => ChildRunService;
  contentFor: (executions: ConstructorParameters<typeof RunContentService>[0]) => RunContentService;
  specs: PersistedSpecs;
  /** Creates the chat session a run of `sessionId` belongs to, as the conversation module does first. */
  ensureSession(orgId: string, actorId: string, sessionId: string, title: string): Promise<void>;
  /** E7's erasure of one session, run to completion; `sweep` ends runs left cancelling. */
  erase(orgId: string, sessionId: string, executions: Pick<ExecutionService, 'control'>, sweep: () => Promise<unknown>): Promise<string[]>;
}

registerHarnessModels([
  AgentSessionErasureModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceKeyModel,
  AgentWorkspaceWriterModel,
] as unknown as Parameters<typeof registerHarnessModels>[0]);

export async function connectMongoWorld(uri: string, dbName: string): Promise<MongoWorld> {
  // As the app connects (WP88-PF05-2): PF-05's gate measures this server's commits as the app's.
  configureMongoose();
  await mongoose.connect(uri, { dbName });
  await Promise.all(harnessModels().map((model) => model.init()));
  await migrateHarnessIndexes(mongoose.connection.db!);
  // Persisted org data keys under the fixed test KEK: a restarted server unseals what the last one sealed.
  const keys = dataKeyRing({ store: new MongoDataKeyStore() });
  const cipher = keys.cipher;
  const payloads = new MongoPayloadStore();
  const journals = new JournalService(payloads, cipher);
  const history = new RunHistoryContent(cipher, payloads, payloads);
  const inputs = new RunInputSeal(cipher);
  const journal = new AuditedJournalStore(
    new PayloadReferencingJournalStore(new SealedJournalStore(new MongoJournalStore(mongoServerTime), cipher), payloads),
    new MongoAuditChainStore(),
  );
  const dispatches = new MongoDispatchOutbox();
  const writer = new HarnessConversationWriter();
  const executionStore = new MongoExecutionStore(writer, dispatches, mongoServerTime, journal);
  const admission = new MongoRunAdmissionStore({
    sessions: new HarnessChatSessionRepository(),
    history: new HarnessHistorySource(),
    conversations: writer,
    dispatches,
    transactionsAvailable: true,
    time: mongoServerTime,
    journal,
    journals,
    historyContent: history,
    inputs,
    runKeys: testRunKeys(),
  });
  const childRuns = new MongoChildRunStore({
    dispatches,
    time: mongoServerTime,
    journal,
    budgets: new MongoBudgetLedger(mongoServerTime),
    runKeys: testRunKeys(),
    inputs,
  });
  const workspaceKeys = new SessionWorkspaceKeys({ store: new MongoWorkspaceKeyStore(), cipher });
  const specs = new PersistedSpecs();
  await specs.load();
  const jobs = new MongoErasureJobStore(mongoServerTime);
  return {
    keys,
    journals,
    journal,
    executionStore,
    admission,
    childRuns,
    payloads,
    history,
    inputs,
    workspaceKeys,
    workspacesFor: (executions) =>
      new WorkspaceService({
        runs: executions,
        repository: new MongoWorkspaceRepository(),
        cipher,
        keys: workspaceKeys,
        writers: new MongoWorkspaceWriterStore(),
      }),
    childrenFor: (workspaces) => new ChildRunService({
      runs: executionStore,
      children: childRuns,
      journals,
      workspaces,
    }),
    contentFor: (executions) => new RunContentService(executions, cipher, payloads),
    specs,
    ensureSession: async (orgId, actorId, sessionId, title) => {
      if ((await ChatSession.exists({ _id: sessionId })) !== null) return;
      await inMongoTransaction((tx) =>
        new HarnessChatSessionRepository().createDurableSession(
          { orgId, actorId, sessionId, title, model: { chatMode: 'auto' }, now: Date.now() },
          tx,
        ),
      );
    },
    erase: async (orgId, sessionId, executions, sweep) => {
      const steps = createErasureSteps<ClientSession>({
        runs: new MongoSessionRunLookup(),
        executions,
        workspaceKeys,
        liveBus: null,
        // The artifact registry is the query service's (its erasure has its own tests); the lane registers none.
        artifacts: {
          eraseConversation: async () => ({ artifacts: 0, blobs: 0, remaining: false }),
          collectDiscarded: async () => ({ artifacts: 0, blobs: 0, remaining: false }),
        } as never,
        stores: new MongoSessionStoreEraser(cipher, testRunKeys()),
        conversations: new HarnessConversationEraser(new FakeChatAttachments()),
        journal: { store: journal, preparer: journals, transaction: inMongoTransaction },
      });
      const runner = new SessionErasureRunner({
        jobs,
        erasers: steps,
        conversations: new HarnessConversationEraser(new FakeChatAttachments()),
      });
      // As a delete does; the runner erases no live conversation (E7B).
      await ChatSession.updateOne({ _id: sessionId }, { $set: { isDeleted: true } });
      await jobs.enqueue({ orgId, sessionId, reason: 'user_delete', by: 'p_drill' });
      const results: string[] = [];
      for (let round = 0; round < 60; round += 1) {
        const job = await jobs.claimDue('journal-server', 60_000);
        if (job === null) {
          if ((await jobs.find(orgId, sessionId))?.status === 'completed') return results;
          await sweep();
          await new Promise((resolve) => setTimeout(resolve, 1_000));
          continue;
        }
        results.push(await runner.run(job));
      }
      throw new Error(`erasure did not complete: ${results.join(',')}`);
    },
  };
}
