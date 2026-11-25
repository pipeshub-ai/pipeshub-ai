import type { Tx } from '../../../../src/libs/repositories/uow';
/**
 * The QA-15 erasure rig: a seeded world, the erasure worker as production
 * composes it, a folder sandbox broker, and the census that scans every store
 * for what is left of a session. Shared by the census itest and QA-21's
 * erasure races.
 */
import { expect } from 'chai';
import mongoose from 'mongoose';
import { Container } from 'inversify';
import type { Redis } from 'ioredis';
import { testKeyRing, testRunKeys } from './data-protection';
import { FakeChatAttachments } from './chat-attachments';
import { FakeArtifacts, Seeded, World, redis, seed } from './session-erasure-world';
import { Needle, Trace, describeLeak, judgeCensus, needlesOf, scanFolder, scanMongo, scanRedis } from './erasure-census';
import { BROKER_CREDENTIAL, FolderSandboxBroker } from './folder-sandbox-broker';
import {
  AGENT_HARNESS_DATA_KEYS,
  AGENT_HARNESS_ERASURE_WORKER,
  AGENT_HARNESS_SANDBOX_REAPER,
  AGENT_HARNESS_SESSION_ERASURES,
  AgentHarnessDependencies,
  HarnessJournal,
  bindAgentHarness,
} from '../../../../src/modules/agent-harness/composition';
import { HttpSandboxReaper } from '../../../../src/modules/agent-harness/adapters/http/http-sandbox-reaper';
import { auditLinksIdle, MongoAuditChainStore } from '../../../../src/libs/repositories/agent-harness/audit/mongo';
import { MongoAuditAnchorSink } from '../../../../src/libs/repositories/agent-harness/audit-anchor-sink/mongo';
import { AuditVerifier } from '../../../../src/modules/agent-harness/application/audit-anchoring';
import { SessionErasureWorker } from '../../../../src/modules/agent-harness/application/session-erasure';
import { ErasureJobStore } from '../../../../src/modules/agent-harness/ports/session-erasure';
import { HarnessChatSessionRepository } from '../../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';
import { HarnessConversationWriter } from '../../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import { HarnessHistorySource } from '../../../../src/libs/repositories/agent-harness/history-source/mongo';
import { HarnessConversationEraser } from '../../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { HarnessConversationRetention } from '../../../../src/libs/repositories/agent-harness/conversation-retention/mongo';

export interface Rig {
  w: World;
  broker: FolderSandboxBroker;
  endpoint: string;
}

/** The erasure worker as `es.container.ts` composes it, over this world's journal, job store and broker. */
export function composedWorker(
  rig: Rig,
  jobs: ErasureJobStore<Tx> = rig.w.jobs,
): SessionErasureWorker<Tx> {
  const container = new Container();
  const ring = testKeyRing();
  const audit = new MongoAuditChainStore();
  const anchors = new MongoAuditAnchorSink();
  container.bind<HarnessJournal>(AGENT_HARNESS_DATA_KEYS).toConstantValue({
    store: rig.w.journal,
    service: rig.w.journals,
    keys: ring.keys,
    cipher: ring.cipher,
    payloads: rig.w.payloads,
    audit,
    anchors,
    verifier: new AuditVerifier(audit, anchors),
  });
  container.bind(AGENT_HARNESS_SESSION_ERASURES).toConstantValue(jobs);
  container
    .bind(AGENT_HARNESS_SANDBOX_REAPER)
    .toConstantValue(new HttpSandboxReaper(async () => rig.endpoint, async () => BROKER_CREDENTIAL));
  bindAgentHarness(container, {
    sessions: new HarnessChatSessionRepository(),
    conversations: new HarnessConversationWriter(),
    history: new HarnessHistorySource(),
    agentAccess: {},
    rollout: {},
    authority: {},
    runKeys: testRunKeys(),
    erasure: {
      conversations: new HarnessConversationEraser(rig.w.chatAttachments),
      artifacts: rig.w.artifacts,
      liveBus: rig.w.liveBus,
      retention: new HarnessConversationRetention(),
      agentDeletions: { state: () => Promise.reject(new Error('not used by session erasure')) },
    },
  } as unknown as AgentHarnessDependencies);
  return container.get<SessionErasureWorker<Tx>>(AGENT_HARNESS_ERASURE_WORKER);
}

/** Ticks the worker until the session's job is gone or done, moving the clock past each wait. */
export async function drain(
  rig: Rig,
  worker: SessionErasureWorker<Tx>,
  s: Pick<Seeded, 'orgId' | 'sessionId'>,
  jobs: ErasureJobStore<Tx> = rig.w.jobs,
): Promise<'completed' | 'withdrawn'> {
  for (let round = 0; round < 40; round += 1) {
    await worker.tick();
    const job = await jobs.find(s.orgId, s.sessionId);
    if (job === null) return 'withdrawn';
    if (job.status === 'completed') return 'completed';
    rig.w.clock.set(Math.max(rig.w.clock.now() + 10_000, job.nextAttemptAt));
    // The run sweeper ends a cancelled run its worker never acknowledged.
    await rig.w.harness.service.sweep(100);
  }
  throw new Error('erasure did not finish');
}

const artifactTraces = (artifacts: FakeArtifacts, needles: readonly Needle[]): Trace[] =>
  [...artifacts.records.entries()].flatMap(([id, record]) =>
    needles
      .filter((needle) => JSON.stringify(record).includes(needle.value))
      .map((needle) => ({ store: 'artifactRecords', at: id, needle })),
  );

const attachmentTraces = (attachments: FakeChatAttachments, needles: readonly Needle[]): Trace[] =>
  [...attachments.uploads.entries()].flatMap(([id, upload]) =>
    needles
      .filter((needle) => JSON.stringify(upload).includes(needle.value))
      .map((needle) => ({ store: 'chatAttachments', at: id, needle })),
  );

/** Every store this rig writes, scanned for the needles. */
/**
 * Every trace of `needles`, read straight from the stores. Audit entries move from `agentAuditPending` to
 * `agentAuditRecords` when their org's link runs, up to a second after their commit (PERF-E3), as a reader through
 * the store would see them linked: the census links first, so two censuses of an unchanged session agree.
 */
export async function census(rig: Rig, needles: readonly Needle[]): Promise<Trace[]> {
  await auditLinksIdle();
  return [
    ...(await scanMongo(mongoose.connection.db!, needles)),
    ...(redis !== null ? await scanRedis(redis.getClient() as unknown as Redis, redis.keyNamespace, needles) : []),
    ...scanFolder(rig.broker.root, 'sandboxFolders', needles),
    ...artifactTraces(rig.w.artifacts, needles),
    ...attachmentTraces(rig.w.chatAttachments, needles),
  ];
}

export const needlesFor = (s: Seeded): Needle[] => needlesOf([s.sessionId, ...s.runIds], [s.canary]);

export async function expectErasedByCensus(rig: Rig, s: Seeded): Promise<void> {
  const verdict = judgeCensus(await census(rig, needlesFor(s)));
  expect(verdict.leaks.map(describeLeak), 'left behind').to.deep.equal([]);
  expect(verdict.deferred, 'a deferred store was written by this rig').to.deep.equal([]);
}

export async function seeded(rig: Rig, running: boolean, tenant?: { orgId: string; actorId: string }): Promise<Seeded> {
  const s = await seed(rig.w, { running, ...(tenant ? { tenant } : {}) });
  rig.broker.plant(s.orgId, s.sessionId, s.canary);
  return s;
}
