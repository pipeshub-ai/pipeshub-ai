/**
 * SS3 on a MongoDB replica set: the fork contract against the real
 * conversation store, journal (sealed, audited) and workspace store; the
 * acceptance case through the history the fork's first durable run is given;
 * and erasure independence (EI-1, EI-2): erasing either side of a fork, with
 * E7's real steps, leaves every store of the other intact. Run as
 * execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { ClientSession, Model, Types } from 'mongoose';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../src/modules/enterprise_search/schema/citation.schema';
import { HarnessSessionForkStore } from '../../../src/libs/repositories/agent-harness/session-forks/mongo';
import { HarnessHistorySource } from '../../../src/libs/repositories/agent-harness/history-source/mongo';
import { HarnessConversationEraser } from '../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { AgentSessionForkModel } from '../../../src/libs/repositories/agent-harness/session-forks/schema/session-forks.schema';
import {
  AgentWorkspaceKeyModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceWriterModel,
} from '../../../src/libs/repositories/agent-harness/workspace/schema/workspace.schema';
import {
  MongoWorkspaceKeyStore,
  MongoWorkspaceRepository,
  MongoWorkspaceWriterStore,
} from '../../../src/libs/repositories/agent-harness/workspace/mongo';
import { AgentSessionErasureModel } from '../../../src/libs/repositories/agent-harness/session-erasure/schema/session-erasure.schema';
import {
  MongoErasureJobStore,
  MongoSessionRunLookup,
} from '../../../src/libs/repositories/agent-harness/session-erasure/mongo';
import { MongoSessionStoreEraser } from '../../../src/libs/repositories/agent-harness/session-store-eraser/mongo';
import { MongoJournalStore } from '../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { AgentWorkspaceHeadModel, AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { SessionEventModel } from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { InMemoryLiveBus } from '../../../src/modules/agent-harness/adapters/in-memory-live-bus';
import { InMemoryWorkspaceObjects } from '../../../src/modules/agent-harness/adapters/in-memory-workspace-objects';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { PayloadReferencingJournalStore } from '../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SessionParticipantJournal } from '../../../src/modules/agent-harness/application/session-participant-journal';
import { SessionWorkspaceKeys } from '../../../src/modules/agent-harness/application/session-workspace-keys';
import { WorkspaceService } from '../../../src/modules/agent-harness/application/workspace-service';
import { SessionErasureRunner } from '../../../src/modules/agent-harness/application/session-erasure';
import { createErasureSteps } from '../../../src/modules/agent-harness/application/session-erasure-steps';
import { FOREIGN_TURN_STUB } from '../../../src/modules/agent-harness/domain/session-audience';
import { ForkCitationData } from '../../../src/modules/agent-harness/domain/session-forks';
import { sessionWorkspaceId } from '../../../src/modules/agent-harness/domain/workspace';
import { SandboxFolderEraser } from '../../../src/modules/agent-harness/ports/sandbox-reaper';
import { SessionParticipantRecorder } from '../../../src/modules/agent-harness/ports/session-participants';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  mongoTimeFor,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { HARNESS_ENABLED } from './support/rollout';
import { testCipher } from './support/data-protection';
import { recordCitation } from './support/citations';
import {
  aliceAndBob,
  allEvents,
  describeSessionForkContract,
  ForkBackend,
  forkAs,
  forkWorld,
  ForkWorld,
  marker,
  readFile,
  seedWorkspace,
  SENTINEL,
  StoredMessage,
  workspaceFiles,
} from './support/session-fork-contract';
import { FakeChatAttachments } from './support/chat-attachments';

registerHarnessModels([
  Citation,
  AgentSessionForkModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceKeyModel,
  AgentWorkspaceWriterModel,
  AgentSessionErasureModel,
] as unknown as Array<Model<never>>);

const INLINE_MAX = 2_000;
const oid = (id: string): Types.ObjectId => new Types.ObjectId(id);
const NOT_COPIED = new Set(['_id', 'sessionId', 'orgId', 'seq', 'createdAt', 'updatedAt']);

interface MongoForkBackend extends ForkBackend<ClientSession> {
  journals: JournalService;
  payloads: MongoPayloadStore;
}

function mongoForkBackend(): MongoForkBackend {
  const cipher = testCipher();
  const payloads = new MongoPayloadStore();
  const sealed = new SealedJournalStore(new MongoJournalStore(mongoTimeFor()), cipher);
  const journal = new AuditedJournalStore(new PayloadReferencingJournalStore(sealed, payloads), new MongoAuditChainStore());
  const journals = new JournalService(payloads, cipher, INLINE_MAX);
  const recorder = new SessionParticipantJournal(journal, journals);
  const failingRecorder: SessionParticipantRecorder<ClientSession> = {
    participantsChanged: (change, tx) => recorder.participantsChanged(change, tx),
    forked: async (orgId, sessionId, fork, tx) => {
      // Written first, so the rollback of the whole transaction is what is tested.
      await recorder.forked(orgId, sessionId, fork, tx);
      throw new Error('journal unavailable');
    },
  };
  const workspaces = new MongoWorkspaceRepository();
  const keys = new SessionWorkspaceKeys({ store: new MongoWorkspaceKeyStore(), cipher });
  return {
    store: new HarnessSessionForkStore(),
    transaction: inMongoTransaction,
    journal,
    journals,
    payloads,
    recorder,
    failingRecorder,
    cipher,
    workspaces,
    keys,
    objects: new InMemoryWorkspaceObjects(),
    workspaceService: new WorkspaceService({
      runs: { get: () => Promise.resolve(null) },
      repository: workspaces,
      cipher,
      keys,
      writers: new MongoWorkspaceWriterStore(),
    }),
    newId: () => new Types.ObjectId().toString(),
    seed: async (conversation) => {
      const sessionId = new Types.ObjectId();
      const isAgent = conversation.agentKey !== undefined;
      await ChatSession.create({
        _id: sessionId,
        orgId: oid(conversation.orgId),
        userId: oid(conversation.ownerId),
        initiator: oid(conversation.ownerId),
        title: conversation.title,
        isShared: conversation.shares.length > 0,
        sharedWith: conversation.shares.map(([userId, accessLevel]) => ({ userId: oid(userId), accessLevel })),
        lastActivityAt: 1,
        modelInfo: { modelName: 'm' },
        projectId: new Types.ObjectId(),
        ...(isAgent ? { sessionType: 'agent', agentKey: conversation.agentKey } : { sessionType: 'chat' }),
        ...(conversation.durable === true ? { harnessVersion: 'durable-v1' } : {}),
      });
      let seq = 0;
      for (const message of conversation.messages) {
        const citations = message.citations?.length ? await Citation.create(message.citations.map((data) => ({ ...data }))) : [];
        seq += 1;
        await ChatSessionMessage.create({
          sessionId,
          orgId: oid(conversation.orgId),
          seq,
          messageType: message.messageType,
          content: message.content,
          ...(message.senderId !== null ? { senderId: oid(message.senderId) } : {}),
          citations: citations.map((citation) => ({ citationId: citation._id, relevanceScore: 0.5 })),
          ...(message.extra ?? {}),
          ...(message.label !== undefined ? { label: message.label } : {}),
        });
      }
      await ChatSession.updateOne({ _id: sessionId }, { $set: { nextSeq: seq } });
      return sessionId.toString();
    },
    unshare: async (sessionId, userId) => {
      await ChatSession.updateOne({ _id: sessionId }, { $pull: { sharedWith: { userId: oid(userId) } } });
    },
    conversation: async (sessionId) => {
      const session = await ChatSession.findById(sessionId).select('+sessionType +harnessVersion').lean();
      if (session === null) return null;
      return {
        ownerId: String(session.userId),
        isShared: session.isShared === true,
        shares: (session.sharedWith ?? []).length,
        sessionType: session.sessionType as 'chat' | 'agent',
        agentKey: session.agentKey ?? null,
        title: session.title ?? '',
        projectId: session.projectId ? String(session.projectId) : null,
        durable: session.harnessVersion === 'durable-v1',
      };
    },
    messages: async (sessionId) =>
      (
        await ChatSessionMessage.find({ sessionId: oid(sessionId) })
          .sort({ seq: 1 })
          .populate({ path: 'citations.citationId', model: 'citation' })
          .lean<Array<Record<string, unknown>>>()
      ).map(
        (message): StoredMessage => ({
          messageType: String(message['messageType']),
          content: String(message['content']),
          senderId: message['senderId'] ? String(message['senderId'] as Types.ObjectId | string) : null,
          foreignTurn: message['foreignTurn'] === true,
          copied: message['copied'] === true,
          citations: ((message['citations'] as Array<{ citationId: Record<string, unknown> | null }>) ?? []).map((link) => ({
            citationId: String((link.citationId?.['_id'] as Types.ObjectId | undefined) ?? ''),
            data: link.citationId === null ? null : (link.citationId as unknown as ForkCitationData),
          })),
          fields: Object.entries(message)
            .filter(([field, value]) => !NOT_COPIED.has(field) && !(Array.isArray(value) && value.length === 0))
            .map(([field]) => field),
        }),
      ),
    citationIds: async () => new Set((await Citation.find({}, { _id: 1 }).lean()).map((c) => String(c._id))),
  };
}

class RecordingFolders implements SandboxFolderEraser {
  readonly erased: string[] = [];

  eraseSession(_orgId: string, sessionId: string): Promise<number> {
    this.erased.push(sessionId);
    return Promise.resolve(0);
  }
}

describe('SS3 continue privately on a MongoDB replica set', function () {
  this.timeout(180_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeSessionForkContract('MongoDB', mongoForkBackend);

  it('AT-1 (model history): the fork\'s first durable run is given Bob\'s turn and stubs, nothing of Alice\'s', async () => {
    const world = forkWorld(mongoForkBackend());
    const s = await aliceAndBob(world);
    const outcome = await forkAs(world, s, s.bob);
    const history = await inMongoTransaction((tx) => new HarnessHistorySource().read({ orgId: s.orgId, sessionId: outcome.sessionId }, tx));
    const turns = JSON.stringify(history.messages);
    expect(turns).to.include('BOB question two');
    expect(turns).to.include(FOREIGN_TURN_STUB);
    for (const hidden of [SENTINEL, 'rec-R"', 'rec-R3', 'art-alice', 'art-b2', 'BOB-REASONING', 'BOB-ATTACHMENT']) {
      expect(turns, hidden).to.not.include(hidden);
    }
    expect(history.ledger).to.equal(null);
    expect(history.lastSeq).to.equal(6);
  });

  describe('erasure independence (EI-1, EI-2)', () => {
    interface Erasure {
      world: ForkWorld<ClientSession>;
      backend: MongoForkBackend;
      folders: RecordingFolders;
      erase(orgId: string, sessionId: string): Promise<void>;
    }

    function erasure(): Erasure {
      const backend = mongoForkBackend();
      const world = forkWorld<ClientSession>(backend);
      const folders = new RecordingFolders();
      const jobs = new MongoErasureJobStore(mongoTimeFor());
      const steps = createErasureSteps<ClientSession>({
        runs: new MongoSessionRunLookup(),
        executions: { control: () => Promise.reject(new Error('no live run to cancel')) } as never,
        workspaceKeys: backend.keys,
        liveBus: new InMemoryLiveBus(),
        sandboxFolders: folders,
        artifacts: world.registry,
        stores: new MongoSessionStoreEraser(backend.cipher),
        conversations: new HarnessConversationEraser(new FakeChatAttachments()),
        journal: { store: backend.journal, preparer: backend.journals, transaction: inMongoTransaction },
      });
      const runner = new SessionErasureRunner({
        jobs,
        erasers: steps,
        conversations: new HarnessConversationEraser(new FakeChatAttachments()),
      });
      return {
        world,
        backend,
        folders,
        erase: async (orgId, sessionId) => {
          // As a delete does; the runner erases no live conversation (E7B).
          await ChatSession.updateOne({ _id: sessionId }, { $set: { isDeleted: true } });
          await jobs.enqueue({ orgId, sessionId, reason: 'user_delete', by: 'p_eraser' });
          for (let round = 0; round < 20; round += 1) {
            const job = await jobs.claimDue('it', 60_000);
            if (job === null) {
              if ((await jobs.find(orgId, sessionId))?.status === 'completed') return;
              continue;
            }
            await runner.run(job);
          }
          expect.fail('erasure did not complete');
        },
      };
    }

    /** A shared source with data in every store a session writes to, and Alice's (owner's) fork of it. */
    async function forked(e: Erasure) {
      const s = await aliceAndBob(e.world);
      // A run that ran and ended in the source: runs, receipts, payloads and run events.
      const clock = contractClock();
      const harness = createContractHarness(mongoBackend({ clock }), clock);
      harness.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
      const admitted = await harness.admit({ orgId: s.orgId, actorId: s.alice, sessionId: s.sessionId, input: { query: SENTINEL, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } } });
      await harness.service.control({ orgId: s.orgId, runId: admitted.run.runId, expectedRevision: admitted.run.revision, action: 'cancel', commandId: 'c-1', dispatchCommandId: 'c-1:d' });
      await seedWorkspace(e.backend, s.orgId, s.sessionId, [{ path: 'notes.md', text: 'workspace notes' }]);
      await e.backend.payloads.put(s.orgId, `sha256:${SENTINEL}`, Buffer.from(SENTINEL), { sessionId: s.sessionId, runId: admitted.run.runId });
      const fork = await forkAs(e.world, s, s.alice);
      expect(fork.counts).to.deep.include({ files: 1, artifacts: 1, citations: 1 });
      return { s, fork, runId: admitted.run.runId };
    }

    async function snapshot(e: Erasure, orgId: string, sessionId: string) {
      const messages = await e.backend.messages(sessionId);
      const workspace = await workspaceFiles(e.backend, orgId, sessionId);
      return {
        conversation: await e.backend.conversation(sessionId),
        messages,
        citations: messages.flatMap((m) => m.citations.map((c) => c.citationId)),
        events: (await allEvents(e.backend, orgId, sessionId)).map((event) => [event.seq, event.type, event.erased === true, event.data]),
        workspace,
        files: workspace === null ? [] : await Promise.all(workspace.files.map((entry) => readFile(e.backend, orgId, sessionId, entry))),
        artifacts: e.world.registry.of(orgId, sessionId).sort(),
      };
    }

    async function expectIntact(e: Erasure, orgId: string, sessionId: string, before: Awaited<ReturnType<typeof snapshot>>) {
      const now = await snapshot(e, orgId, sessionId);
      expect(now).to.deep.equal(before);
      expect(now.conversation).to.not.equal(null);
      expect(await Citation.countDocuments({ _id: { $in: before.citations } })).to.equal(before.citations.length);
      // Every artifact an answer shows is still there, and is this session's own.
      for (const [, artifactId] of JSON.stringify(now.messages).matchAll(/record:([^)]+)\)/g)) {
        expect(e.world.registry.artifacts.get(artifactId!)?.conversationId, artifactId).to.equal(sessionId);
      }
    }

    it('EI-1: erasing the source leaves every store of the fork intact', async () => {
      const e = erasure();
      const { s, fork } = await forked(e);
      const before = await snapshot(e, s.orgId, fork.sessionId);
      const sourceCitations = (await e.backend.messages(s.sessionId)).flatMap((m) => m.citations.map((c) => c.citationId));
      expect(sourceCitations).to.have.length(3);
      expect(before.files).to.deep.equal(['workspace notes']);
      expect(before.citations).to.have.length(1);
      expect(before.artifacts).to.have.length(1);
      await e.erase(s.orgId, s.sessionId);
      // The source is gone, store by store.
      expect(await ChatSession.countDocuments({ _id: s.sessionId })).to.equal(0);
      expect(await AgentWorkspaceManifestModel.countDocuments({ orgId: s.orgId, workspaceId: sessionWorkspaceId(s.sessionId) })).to.equal(0);
      const shredded = await e.backend.keys.issue(s.orgId, s.sessionId).catch((error: { code?: string }) => error.code);
      expect(shredded).to.equal('WORKSPACE_KEY_DESTROYED');
      expect(e.world.registry.of(s.orgId, s.sessionId)).to.deep.equal([]);
      expect(await Citation.countDocuments({ _id: { $in: sourceCitations } })).to.equal(0);
      expect(await AgentSessionForkModel.countDocuments({ orgId: s.orgId })).to.equal(0);
      const sourceEvents = await SessionEventModel.find({ orgId: s.orgId, streamId: s.sessionId }).lean();
      expect(sourceEvents.filter((event) => event.type !== 'session.erased').every((event) => event.erased === true)).to.equal(true);
      expect(e.folders.erased).to.deep.equal([s.sessionId]);
      // The fork is whole: conversation, messages, its own citations, journal, workspace (opens with its key), artifacts.
      await expectIntact(e, s.orgId, fork.sessionId, before);
      expect(await AgentWorkspaceHeadModel.countDocuments({ orgId: s.orgId, workspaceId: sessionWorkspaceId(fork.sessionId) })).to.equal(1);
      expect(await AgentRunModel.countDocuments({ orgId: s.orgId, sessionId: fork.sessionId })).to.equal(0);
    });

    it('EI-2: erasing the fork leaves every store of the source intact', async () => {
      const e = erasure();
      const { s, fork, runId } = await forked(e);
      const before = await snapshot(e, s.orgId, s.sessionId);
      expect(before.events.at(-1)?.[1]).to.equal('session.forked');
      expect(before.artifacts).to.have.length(3);
      const runsBefore = await AgentRunModel.find({ orgId: s.orgId, sessionId: s.sessionId }).lean();
      const payloadsBefore = await ownedPayloads(s.orgId, s.sessionId);
      expect(payloadsBefore).to.be.greaterThan(0);
      await e.erase(s.orgId, fork.sessionId);
      expect(await ChatSession.countDocuments({ _id: fork.sessionId })).to.equal(0);
      expect(await AgentWorkspaceManifestModel.countDocuments({ orgId: s.orgId, workspaceId: sessionWorkspaceId(fork.sessionId) })).to.equal(0);
      expect(e.world.registry.of(s.orgId, fork.sessionId)).to.deep.equal([]);
      expect(await AgentSessionForkModel.countDocuments({ orgId: s.orgId })).to.equal(0);
      expect(e.folders.erased).to.deep.equal([fork.sessionId]);
      await expectIntact(e, s.orgId, s.sessionId, before);
      expect(await AgentRunModel.find({ orgId: s.orgId, sessionId: s.sessionId }).lean()).to.deep.equal(runsBefore);
      expect(runsBefore.map((run) => run.runId)).to.deep.equal([runId]);
      expect(await ownedPayloads(s.orgId, s.sessionId)).to.equal(payloadsBefore);
    });

    it('MU-1/MU-4 guard: the fork never points at the source\'s citations or artifacts', async () => {
      const e = erasure();
      const { s, fork } = await forked(e);
      const source = await e.backend.messages(s.sessionId);
      const forkMessages = await e.backend.messages(fork.sessionId);
      const sourceCitations = new Set(source.flatMap((m) => m.citations.map((c) => c.citationId)));
      expect(forkMessages.flatMap((m) => m.citations.map((c) => c.citationId)).some((id) => sourceCitations.has(id))).to.equal(false);
      const sourceArtifacts = new Set(e.world.registry.of(s.orgId, s.sessionId));
      expect(JSON.stringify(forkMessages)).to.include('record:copy-');
      for (const id of sourceArtifacts) expect(JSON.stringify(forkMessages)).to.not.include(`record:${id}`);
    });
  });

  it('a citation whose document is gone is not copied (nothing to copy)', async () => {
    const world = forkWorld(mongoForkBackend());
    const s = await aliceAndBob(world);
    const [answer] = await ChatSessionMessage.find({ sessionId: oid(s.sessionId), messageType: 'bot_response', senderId: oid(s.bob) }).lean();
    await Citation.deleteMany({ _id: { $in: (answer!.citations ?? []).map((c) => c.citationId) } });
    const outcome = await forkAs(world, s, s.bob);
    expect(outcome.counts).to.deep.include({ citations: 0, citationsHidden: 0 });
    expect(marker('x', 1)).to.include('record:x');
    expect((recordCitation('a', 1) as { chunkIndex: number }).chunkIndex).to.equal(1);
  });
});

const ownedPayloads = (orgId: string, sessionId: string): Promise<number> =>
  mongoose.connection.collection('agentJournalPayloads').countDocuments({ orgId, 'owner.sessionId': sessionId });
