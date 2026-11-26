import type { Tx } from '../../../../src/libs/repositories/uow';
/**
 * One session with data in every store a session writes to, the stores it is
 * erased from, and the erasure runner over them, on a MongoDB replica set
 * (and Redis for the LiveBus when REDIS_IT_URL is set). Shared by the E7
 * itests and the QA-15 census.
 */
import 'reflect-metadata';
import { MongoSandboxSnapshotIndex } from '../../../../src/libs/repositories/agent-harness/sandbox-snapshots/mongo';
import mongoose, { Model, Types } from 'mongoose';
import { randomUUID } from 'node:crypto';
import {
  mongoBackend,
  mongoTimeFor,
  registerHarnessModels,
} from './harness-mongo';
import {
  ContractClock,
  ContractHarness,
  contractClock,
  createContractHarness,
} from './execution-store-contract';
import { storedManifest } from './workspace-repository-contract';
import { MongoMemoryStore } from '../../../../src/libs/repositories/agent-harness/memory/mongo';
import { AgentMemoryModel } from '../../../../src/libs/repositories/agent-harness/memory/schema/memory.schema';
import { AgentSessionForkModel } from '../../../../src/libs/repositories/agent-harness/session-forks/schema/session-forks.schema';
import { NextRunEraser } from '../../../../src/modules/agent-harness/domain/next-runs';
import { memoryFixture } from './memory-store-contract';
import { testCipher, testRunKeys } from './data-protection';
import { FakeChatAttachments } from './chat-attachments';
import { HARNESS_ENABLED } from './rollout';
import { bulkyOutcome, workerEvent } from './journal-fixtures';
import {
  MongoErasureJobStore,
  MongoSessionRunLookup,
} from '../../../../src/libs/repositories/agent-harness/session-erasure/mongo';
import { MongoSessionStoreEraser } from '../../../../src/libs/repositories/agent-harness/session-store-eraser/mongo';
import {
  MongoWorkspaceKeyStore,
  MongoWorkspaceRepository,
  MongoWorkspaceWriterStore,
} from '../../../../src/libs/repositories/agent-harness/workspace/mongo';
import {
  AgentCommandReceiptModel,
  AgentNextRunModel,
  AgentRunModel,
} from '../../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import {
  AgentJournalPayloadModel,
  SessionEventModel,
} from '../../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { MongoJournalStore } from '../../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoPayloadStore } from '../../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { MongoAuditChainStore } from '../../../../src/libs/repositories/agent-harness/audit/mongo';
import { inMongoDurableTransaction } from '../../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { InMemoryLiveBus } from '../../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import { RedisLiveBus, liveStreamKey } from '../../../../src/modules/agent-harness/adapters/redis/redis-live-bus';
import { AuditedJournalStore } from '../../../../src/modules/agent-harness/application/audited-journal-store';
import { SealedJournalStore } from '../../../../src/modules/agent-harness/application/sealed-journal-store';
import { PayloadReferencingJournalStore } from '../../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { sealRunContent } from '../../../../src/modules/agent-harness/application/run-content-service';
import { SessionWorkspaceKeys } from '../../../../src/modules/agent-harness/application/session-workspace-keys';
import { SessionErasureRunner } from '../../../../src/modules/agent-harness/application/session-erasure';
import { createErasureSteps } from '../../../../src/modules/agent-harness/application/session-erasure-steps';
import { formatContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { sessionAppend } from '../../../../src/modules/agent-harness/domain/session-participants';
import { ArtifactEraser, SessionDataEraser } from '../../../../src/modules/agent-harness/ports/session-erasure';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema';
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema';
import { HarnessConversationEraser } from '../../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { getRedisProvider } from '../../../../src/libs/services/redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from '../../../../src/libs/services/redis/connectionConfig';

export const SENTINEL = 'SENTINEL-session-content';

registerHarnessModels([AgentMemoryModel, AgentSessionForkModel] as unknown as Array<Model<never>>);
export const INLINE_MAX = 2_000;

/** The artifact registry as erasure sees it, in memory (Python's side has its own tests). */
export class FakeArtifacts implements ArtifactEraser {
  readonly records = new Map<string, { orgId: string; sessionId: string; blob: string }>();
  fail = false;

  add(orgId: string, sessionId: string, count: number): void {
    for (let i = 0; i < count; i += 1) {
      this.records.set(randomUUID(), { orgId, sessionId, blob: randomUUID() });
    }
  }

  of(orgId: string, sessionId: string): number {
    return [...this.records.values()].filter((r) => r.orgId === orgId && r.sessionId === sessionId).length;
  }

  eraseConversation(orgId: string, sessionId: string) {
    if (this.fail) return Promise.reject(new Error('query service down'));
    let artifacts = 0;
    for (const [id, record] of this.records) {
      if (record.orgId === orgId && record.sessionId === sessionId) {
        this.records.delete(id);
        artifacts += 1;
      }
    }
    return Promise.resolve({ artifacts, blobs: artifacts, remaining: false });
  }

  collectDiscarded() {
    return Promise.resolve({ artifacts: 0, blobs: 0, remaining: false });
  }
}

export const redisUrl = process.env.REDIS_IT_URL ? new URL(process.env.REDIS_IT_URL) : null;
export const redis = redisUrl
  ? getRedisProvider(redisConnectionConfigFromHostPort({ host: redisUrl.hostname, port: Number(redisUrl.port || 6379) }))
  : null;

export interface World {
  clock: ContractClock;
  harness: ContractHarness;
  journal: AuditedJournalStore<Tx>;
  sealed: SealedJournalStore<Tx>;
  journals: JournalService;
  payloads: MongoPayloadStore;
  jobs: MongoErasureJobStore;
  keys: SessionWorkspaceKeys;
  liveBus: RedisLiveBus | InMemoryLiveBus;
  artifacts: FakeArtifacts;
  /** The query service's chat attachment uploads (QA15-F4). */
  chatAttachments: FakeChatAttachments;
  steps: SessionDataEraser<Tx>[];
  conversations: HarnessConversationEraser;
}

export function world(): World {
  const clock = contractClock();
  const backend = mongoBackend({ clock });
  const harness = createContractHarness(backend, clock);
  // Harness v2: admissions, claims and cancels are journaled and audited, as in production.
  harness.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  const cipher = testCipher();
  const payloads = new MongoPayloadStore();
  const sealed = new SealedJournalStore(new MongoJournalStore(mongoTimeFor(clock)), cipher);
  const journal = new AuditedJournalStore(
    new PayloadReferencingJournalStore(sealed, payloads),
    new MongoAuditChainStore(),
  );
  const journals = new JournalService(payloads, cipher, INLINE_MAX);
  const keys = new SessionWorkspaceKeys({ store: new MongoWorkspaceKeyStore(), cipher });
  const liveBus = redis !== null ? new RedisLiveBus(redis) : new InMemoryLiveBus();
  const artifacts = new FakeArtifacts();
  const chatAttachments = new FakeChatAttachments();
  const steps = createErasureSteps<Tx>({
    runs: new MongoSessionRunLookup(),
    executions: harness.service,
    workspaceKeys: keys,
    liveBus,
    artifacts,
    memories: new MongoMemoryStore(),
    nextRuns: backend.admissionStore as unknown as NextRunEraser,
    stores: new MongoSessionStoreEraser(cipher, testRunKeys()),
    conversations: new HarnessConversationEraser(chatAttachments),
    journal: { store: journal, preparer: journals, transaction: inMongoDurableTransaction },
  });
  return {
    clock,
    harness,
    journal,
    sealed,
    journals,
    payloads,
    jobs: new MongoErasureJobStore(mongoTimeFor(clock)),
    keys,
    liveBus,
    artifacts,
    chatAttachments,
    steps,
    conversations: new HarnessConversationEraser(chatAttachments),
  };
}

export interface Seeded {
  orgId: string;
  sessionId: string;
  /** Planted in every plaintext copy of the session's content. */
  canary: string;
  runIds: string[];
  /** A run still going (claimed), when asked for. */
  liveRunId: string | null;
  citationIds: string[];
  /** The pre-E7 (unowned) payload its events reference. */
  legacyPayload: string;
}

export const append = async (w: World, batch: Parameters<JournalService['prepare']>[0]) =>
  inMongoDurableTransaction(async (tx) => w.journal.append(await w.journals.prepare(batch), tx));

export async function publishLive(w: World, runId: string, text: string = SENTINEL): Promise<void> {
  if (w.liveBus instanceof RedisLiveBus) {
    const key = liveStreamKey(runId, redis!.keyNamespace);
    await redis!.getClient().xadd(key, '*', 'v', '1', 'k', 'text_delta', 'f', '1', 'a', '0', 'n', '0', 'd', JSON.stringify({ text }));
    await redis!.getClient().expire(key, 600);
  } else {
    w.liveBus.publish({ runId, fence: 1, modelAttempt: 0, n: 0, kind: 'text_delta', data: { text } } as never);
  }
}

/** One session with data in every store a session writes to (C-14b). */
export async function seed(w: World, options: { running: boolean; tenant?: { orgId: string; actorId: string } }): Promise<Seeded> {
  const tenant = options.tenant ?? w.harness.tenant;
  // Its own canary, so a census tells this session's content from another's.
  const canary = `${SENTINEL}:${randomUUID()}`;
  const input = { query: canary, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } };
  // The caller's key is user text too (QA15-F3): erasure must leave none of it.
  const first = await w.harness.admit({ ...tenant, input, idempotencyKey: canary });
  const sessionId = first.sessionId;
  const orgId = tenant.orgId;
  await w.harness.service.control({
    orgId,
    runId: first.run.runId,
    expectedRevision: first.run.revision,
    action: 'cancel',
    commandId: `seed-cancel-${first.run.runId}`,
    dispatchCommandId: `seed-cancel-${first.run.runId}:dispatch`,
  });
  // Follow-up suggestions are model text drawn from the conversation (CLEANUP-1), stored on the run once it ended.
  await AgentRunModel.updateOne({ orgId, runId: first.run.runId }, { $set: { followups: [{ text: canary }] } });
  const runIds = [first.run.runId];
  let liveRunId: string | null = null;
  if (options.running) {
    const second = await w.harness.admit({ ...tenant, sessionId, input });
    await w.harness.claim(second.run, `worker-${second.run.runId}`, 1_000);
    runIds.push(second.run.runId);
    liveRunId = second.run.runId;
  }
  const writer = { orgId, sessionId, streamId: sessionId, runId: first.run.runId, attemptId: 'a1', fence: 1, writer: 'worker' as const };
  // Sealed content the run wrote, referenced from a claim-checked event (owned).
  const content = await sealRunContent(testCipher(), w.payloads, { orgId, sessionId, runId: first.run.runId }, Buffer.from(canary.repeat(10)));
  const bulky = bulkyOutcome(INLINE_MAX + 500);
  (bulky.data['receipt'] as Record<string, unknown>)['contentRef'] = { ref: content.ref };
  await append(w, { ...writer, events: [bulky] });
  // A payload stored before owners existed, referenced from an inline event.
  const legacyStoreRef = await w.payloads.put(orgId, `sha256:legacy-${randomUUID()}`, Buffer.from(canary));
  await append(w, {
    ...writer,
    events: [workerEvent('tool.outcome', { invocationId: 'inv-2', status: 'ok', receipt: { body: { ref: formatContentRef({ writerRunId: first.run.runId, storeRef: legacyStoreRef }) } }, labels: { integrity: 'external' } })],
  });
  // A participant event on the session stream (SS1), as a shared session has.
  await append(w, sessionAppend(orgId, sessionId, [
    { type: 'session.participant_changed', schemaVersion: 1, critical: false, visibility: 'shared', data: { by: `p_${tenant.actorId}`, subject: 'p_collaborator', role: 'collaborator', prevRole: 'none' } },
  ]));
  await mongoose.connection.collection('agentEvents').insertOne({ orgId, sessionId, runId: first.run.runId, eventSeq: 9_999, commitSeq: 9_999, type: 'run.output', data: { text: canary }, createdAt: 1 });
  await AgentCommandReceiptModel.create({ orgId, runId: first.run.runId, commandId: `c-${randomUUID()}`, payloadHash: 'h', receipt: { text: canary }, createdAt: 1 });
  // OWD-12: an engine sandbox snapshot the session's run stored, as the journal stack records it.
  await new MongoSandboxSnapshotIndex().record([
    { orgId, sessionId, runId: first.run.runId, objectId: `snap-${sessionId}`, takenAt: Date.now(), region: 'default' },
  ]);
  const repository = new MongoWorkspaceRepository();
  for (const workspaceId of [`ws:${sessionId}`, `ws:${sessionId}:skills:abc`]) {
    await repository.append({ manifest: { ...storedManifest(workspaceId, 1, orgId), runId: first.run.runId }, expectedRevision: 0 });
  }
  // P6.4: a child's fork, its keyed merge into the session's workspace, and a writer lease.
  const fork = `ws:${sessionId}:fork:child-1`;
  await repository.append({
    manifest: { ...storedManifest(fork, 1, orgId), runId: first.run.runId },
    expectedRevision: 0,
    forkOf: { workspaceId: `ws:${sessionId}`, revision: 1 },
  });
  await repository.append({ manifest: { ...storedManifest(fork, 2, orgId), runId: first.run.runId }, expectedRevision: 1 });
  await repository.append({
    manifest: { ...storedManifest(`ws:${sessionId}`, 2, orgId), runId: first.run.runId },
    expectedRevision: 1,
    mergedFork: {
      workspaceId: fork,
      mergeBase: 2,
      record: { key: 'child-1', into: `ws:${sessionId}`, base: 1, theirs: 2, ours: 1, revision: 2, status: 'merged' },
    },
  });
  await new MongoWorkspaceWriterStore().acquire(
    { orgId, sessionId, workspaceId: `ws:${sessionId}`, runId: first.run.runId, expiresAt: Date.now() + 600_000 },
    Date.now(),
  );
  await w.keys.issue(orgId, sessionId);
  for (const runId of runIds) await publishLive(w, runId, canary);
  w.artifacts.add(orgId, sessionId, 2);
  // P7.3: memories the session's runs proposed, of each scope a run can write.
  const memories = new MongoMemoryStore();
  for (const scope of ['session', 'user'] as const) {
    await memories.propose(memoryFixture({ orgId, sessionId, runId: first.run.runId, scope, ownerId: tenant.actorId, content: canary }));
  }
  // SS2: a collaborator's message waiting for the running task; SS3: a fork request naming the session.
  await AgentNextRunModel.create({
    _id: randomUUID(),
    orgId,
    sessionId,
    actorId: 'collaborator',
    runId: randomUUID(),
    behindRunId: runIds.at(-1)!,
    status: 'pending',
    command: { input: { query: canary } },
    createdAt: 1,
  });
  await AgentSessionForkModel.create({
    _id: randomUUID(),
    orgId,
    sourceSessionId: sessionId,
    actorId: tenant.actorId,
    forkSessionId: new Types.ObjectId().toString(),
    result: null,
    createdAt: 1,
    completedAt: null,
  });
  const citations = await Citation.create([0, 1].map((i) => ({
    content: `${canary} ${String(i)}`,
    chunkIndex: i,
    citationType: 'vectordb|document',
    metadata: { orgId, mimeType: 'text/plain', recordId: `rec-${String(i)}`, recordName: 'r', origin: 'UPLOAD', extension: 'txt' },
  })));
  const citationIds = citations.map((c) => String(c._id));
  await ChatSessionMessage.create({
    sessionId: new Types.ObjectId(sessionId),
    orgId: new Types.ObjectId(orgId),
    seq: 100,
    messageType: 'bot_response',
    content: canary,
    citations: citations.map((c) => ({ citationId: c._id })),
  });
  // A chat attachment the owner uploaded and asked about (QA15-F4).
  const upload = new Types.ObjectId().toHexString();
  w.chatAttachments.upload(upload, { orgId, ownerId: tenant.actorId, content: canary });
  await ChatSessionMessage.create({
    sessionId: new Types.ObjectId(sessionId),
    orgId: new Types.ObjectId(orgId),
    seq: 101,
    messageType: 'user_query',
    content: canary,
    attachments: [{ recordId: upload, recordName: `${canary}.txt`, mimeType: 'text/plain' }],
  });
  // A scheduled run's inbox notice links to the conversation (CLEANUP-3).
  await Notifications.create({
    orgId: new Types.ObjectId(orgId),
    assignedTo: new Types.ObjectId(),
    type: 'AGENT_SCHEDULE_APPROVAL_WAITING',
    originService: 'AI Service',
    severity: 'warning',
    status: 'unread',
    title: 'A scheduled run is waiting for your approval',
    message: 'paused before a write',
    redirectLink: `/chat/?conversationId=${sessionId}`,
    payload: { runId: first.run.runId, conversationId: sessionId },
    isDeleted: false,
  });
  for (const legacy of ['conversations', 'agentconversations']) {
    await mongoose.connection.collection(legacy).insertOne({ _id: new Types.ObjectId(sessionId), orgId: new Types.ObjectId(orgId), messages: [{ content: canary }] });
  }
  await ChatSession.updateOne({ _id: sessionId }, { $set: { isShared: true, sharedWith: [{ userId: new Types.ObjectId(), accessLevel: 'write' }] } });
  return { orgId, sessionId, canary, runIds, liveRunId, citationIds, legacyPayload: legacyStoreRef.replace(/^mongo:/, '') };
}

export const ownedPayloads = (s: Seeded) => AgentJournalPayloadModel.countDocuments({ orgId: s.orgId, 'owner.sessionId': s.sessionId });

export async function liveDeltas(w: World, runId: string): Promise<number> {
  return (await w.liveBus.read(runId)).deltas.length;
}

/** Runs the job to completion, ending the session's live runs the way the sweeper does. */
export async function eraseFully(w: World, s: Pick<Seeded, 'orgId' | 'sessionId'>, runner = new SessionErasureRunner({
  jobs: w.jobs,
  erasers: w.steps,
  conversations: w.conversations,
})): Promise<string[]> {
  const results: string[] = [];
  for (let round = 0; round < 30; round += 1) {
    const job = await w.jobs.claimDue('it', 60_000);
    if (job === null) {
      const pending = await w.jobs.find(s.orgId, s.sessionId);
      if (pending?.status === 'completed') return results;
      w.clock.set(w.clock.now() + 10_000);
      await w.harness.service.sweep(100);
      continue;
    }
    results.push(await runner.run(job));
  }
  throw new Error(`erasure did not complete: ${results.join(',')}`);
}


export const typesOf = async (s: Seeded) =>
  (await SessionEventModel.find({ orgId: s.orgId, streamId: s.sessionId }).sort({ seq: 1 }).lean()).map((e) => e.type);

/** As a delete does: the conversation is marked deleted, then its erasure enqueued (the runner erases no live one, E7B). */
export const markDeleted = (s: Pick<Seeded, 'sessionId'>) =>
  ChatSession.updateOne({ _id: s.sessionId }, { $set: { isDeleted: true, erasureRequestedAt: Date.now() } });

export const enqueue = async (w: World, s: Seeded) => {
  await markDeleted(s);
  return w.jobs.enqueue({ orgId: s.orgId, sessionId: s.sessionId, reason: 'user_delete', by: `p_${w.harness.tenant.actorId}` });
};

