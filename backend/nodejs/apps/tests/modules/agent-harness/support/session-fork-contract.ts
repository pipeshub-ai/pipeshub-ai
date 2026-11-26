/**
 * SS3 behaviour every fork store must share ("continue privately"): what a
 * fork copies and as whom, the copies being copies, the transaction, the
 * refusals and idempotency, and the acceptance case (the fork shows only what
 * the forker may see). Runs against the in-memory store
 * (`session-forks.test.ts`), MongoDB (`session-forks.mongo.itest.ts`) and
 * PostgreSQL (`session-forks.pg.itest.ts`).
 */
import { expect } from 'chai';
import { Types } from 'mongoose';
import { HarnessConflictError, HarnessNotFoundError, HarnessUnavailableError } from '../../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { FOREIGN_TURN_STUB } from '../../../../src/modules/agent-harness/domain/session-audience';
import { ForkCitationData, ForkOutcome } from '../../../../src/modules/agent-harness/domain/session-forks';
import {
  manifestContentHash,
  manifestSealContext,
  sessionWorkspaceId,
  WorkspaceError,
  WorkspaceFileEntry,
} from '../../../../src/modules/agent-harness/domain/workspace';
import {
  decodeSealedId,
  encodeSealedId,
  ObjectUnreadableError,
  openObject,
  sealObject,
} from '../../../../src/modules/agent-harness/domain/workspace-sealing';
import { InMemoryArtifactReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-artifact-access';
import { InMemoryRecordReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { EnvelopeCipher } from '../../../../src/modules/agent-harness/application/envelope-cipher';
import { HarnessLogger } from '../../../../src/modules/agent-harness/application/harness-log';
import { SessionForkService } from '../../../../src/modules/agent-harness/application/session-fork-service';
import { SessionWorkspaceKeys } from '../../../../src/modules/agent-harness/application/session-workspace-keys';
import { SharedArtifactService } from '../../../../src/modules/agent-harness/application/shared-artifacts';
import { SharedCitationService } from '../../../../src/modules/agent-harness/application/shared-citations';
import { WorkspaceObjectResealer } from '../../../../src/modules/agent-harness/application/workspace-object-resealer';
import { WorkspaceService } from '../../../../src/modules/agent-harness/application/workspace-service';
import { JournalStore } from '../../../../src/modules/agent-harness/ports/journal-store';
import { SessionRunLookup } from '../../../../src/modules/agent-harness/ports/session-erasure';
import {
  ArtifactCopier,
  ArtifactCopyOutcome,
  ArtifactCopyRequest,
  SessionForkStore,
} from '../../../../src/modules/agent-harness/ports/session-forks';
import { SessionParticipantRecorder } from '../../../../src/modules/agent-harness/ports/session-participants';
import { WorkspaceObjectBytes } from '../../../../src/modules/agent-harness/ports/workspace-objects';
import { WorkspaceRepository } from '../../../../src/modules/agent-harness/ports/workspace-store';
import { UNAVAILABLE_ARTIFACT_MARKER } from '../../../../src/modules/agent-harness/projection/artifact-access';
import { recordCitation } from './citations';
import type { TurnLabel } from '../../../../src/modules/agent-harness/domain/turn-labels';

export const SENTINEL = 'ALICE-PRIVATE';

/** The artifact registry as the fork and erasure see it: copies are new records with new blobs. */
export class FakeArtifactRegistry implements ArtifactCopier {
  readonly artifacts = new Map<string, { orgId: string; conversationId: string; owner: string; blob: string; content: string }>();
  readonly requests: ArtifactCopyRequest[] = [];
  /** Mutation hook (MU-1): answer the original instead of a copy. */
  shareInsteadOfCopy = false;
  fail = false;

  add(orgId: string, conversationId: string, owner: string, artifactId: string, content: string): void {
    this.artifacts.set(artifactId, { orgId, conversationId, owner, blob: `blob-${artifactId}`, content });
  }

  of(orgId: string, conversationId: string): string[] {
    return [...this.artifacts].filter(([, a]) => a.orgId === orgId && a.conversationId === conversationId).map(([id]) => id);
  }

  copy(request: ArtifactCopyRequest): Promise<ArtifactCopyOutcome[]> {
    if (this.fail) return Promise.reject(new Error('query service down'));
    this.requests.push(structuredClone(request) as ArtifactCopyRequest);
    return Promise.resolve(
      request.artifacts.map((ref) => {
        const source = this.artifacts.get(ref.artifactId);
        if (source === undefined || source.orgId !== request.orgId || source.conversationId !== request.fromConversationId) {
          return { ...ref, copy: null, reason: 'not_found' };
        }
        if (this.shareInsteadOfCopy) {
          return { ...ref, copy: { artifactId: ref.artifactId, documentId: source.blob, version: ref.version ?? 1 } };
        }
        const copyId = `copy-${request.toConversationId}-${ref.artifactId}-${String(ref.version ?? 'cur')}`;
        if (!this.artifacts.has(copyId)) {
          this.artifacts.set(copyId, { orgId: request.orgId, conversationId: request.toConversationId, owner: request.actorId, blob: `blob-${copyId}`, content: source.content });
        }
        return { ...ref, copy: { artifactId: copyId, documentId: `blob-${copyId}`, version: 1 } };
      }),
    );
  }

  eraseConversation(orgId: string, sessionId: string) {
    let artifacts = 0;
    for (const id of this.of(orgId, sessionId)) {
      this.artifacts.delete(id);
      artifacts += 1;
    }
    return Promise.resolve({ artifacts, blobs: artifacts, remaining: false });
  }

  collectDiscarded() {
    return Promise.resolve({ artifacts: 0, blobs: 0, remaining: false });
  }
}

/** A source run lookup the test steers: an active run, or none. */
export class SteeredRuns implements SessionRunLookup {
  readonly active = new Set<string>();

  runsOf(_orgId: string, sessionId: string): Promise<Array<Pick<HarnessRun, 'runId' | 'status' | 'revision' | 'harnessMajor'>>> {
    return Promise.resolve(
      this.active.has(sessionId)
        ? [{ runId: `run-${sessionId}`, status: 'running', revision: 2, harnessMajor: 2 }]
        : [{ runId: `done-${sessionId}`, status: 'completed', revision: 4, harnessMajor: 2 }],
    );
  }
}

export interface SeedMessage {
  messageType: string;
  /** null: the owner's. */
  senderId: string | null;
  content: string;
  citations?: ForkCitationData[];
  /** Tool rows, reasoning, parts and the like a source message may hold. */
  extra?: Record<string, unknown>;
  /** A durable run's answer: its turn's label (P2.5a). */
  label?: TurnLabel;
}

export interface SeedConversation {
  orgId: string;
  ownerId: string;
  shares: Array<[userId: string, accessLevel: 'read' | 'write']>;
  agentKey?: string;
  title: string;
  /** The conversation runs on the durable harness (`harnessVersion: durable-v1`). */
  durable?: boolean;
  messages: SeedMessage[];
}

export interface StoredConversation {
  ownerId: string;
  isShared: boolean;
  shares: number;
  sessionType: 'chat' | 'agent';
  agentKey: string | null;
  title: string;
  projectId: string | null;
  /** Pinned to the durable harness, with an empty ledger the next admission imports from. */
  durable: boolean;
}

export interface StoredMessage {
  messageType: string;
  content: string;
  senderId: string | null;
  foreignTurn: boolean;
  /** Copied from the source, naming none of its runs (FORKFIX); a stub is not. */
  copied: boolean;
  citations: Array<{ citationId: string; data: ForkCitationData | null }>;
  /** The fields the stored message holds (so a test can see nothing else was copied). */
  fields: string[];
}

export interface ForkBackend<Tx> {
  store: SessionForkStore<Tx>;
  transaction: <T>(work: (tx: Tx) => Promise<T>) => Promise<T>;
  journal: JournalStore<Tx>;
  recorder: SessionParticipantRecorder<Tx>;
  /** A recorder that fails inside the transaction (after writing, where the store rolls writes back). */
  failingRecorder: SessionParticipantRecorder<Tx>;
  cipher: EnvelopeCipher;
  workspaces: WorkspaceRepository;
  keys: SessionWorkspaceKeys;
  objects: WorkspaceObjectBytes;
  workspaceService: WorkspaceService;
  newId: () => string;
  seed(conversation: SeedConversation): Promise<string>;
  unshare(sessionId: string, userId: string): Promise<void>;
  conversation(sessionId: string): Promise<StoredConversation | null>;
  messages(sessionId: string): Promise<StoredMessage[]>;
  /** Every citation document's id that exists now. */
  citationIds(): Promise<Set<string>>;
  /**
   * A fork request run as its route runs it, with the request's organisation bound (ADR-SEC-02): a store that scopes
   * by the bound organisation (PostgreSQL) needs it. Absent: the request runs as it is.
   */
  asOrg?<T>(orgId: string, work: () => Promise<T>): Promise<T>;
}

export const marker = (artifactId: string, version: number, name = `${artifactId}.png`): string =>
  `::artifact[${name}](record:${artifactId}){image/png|doc-${artifactId}|${artifactId}|IMAGE|${String(version)}}`;

const bearer = (userId: string): string => `Bearer ${userId}`;

type Line = [level: string, message: string, meta: Record<string, unknown>];

export function recordingLogger(): { logger: HarnessLogger; lines: Line[] } {
  const lines: Line[] = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  return { lines, logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') } };
}

export interface ForkWorld<Tx> {
  backend: ForkBackend<Tx>;
  records: InMemoryRecordReadAccess;
  artifactAccess: InMemoryArtifactReadAccess;
  registry: FakeArtifactRegistry;
  runs: SteeredRuns;
  lines: Line[];
  service(overrides?: Partial<ConstructorParameters<typeof SessionForkService<Tx>>[0]>): SessionForkService<Tx>;
}

export function forkWorld<Tx>(backend: ForkBackend<Tx>): ForkWorld<Tx> {
  const records = new InMemoryRecordReadAccess();
  const artifactAccess = new InMemoryArtifactReadAccess();
  const registry = new FakeArtifactRegistry();
  const runs = new SteeredRuns();
  const { logger, lines } = recordingLogger();
  return {
    backend,
    records,
    artifactAccess,
    registry,
    runs,
    lines,
    service: (overrides = {}) => {
      const service = new SessionForkService<Tx>({
        store: backend.store,
        runs,
        journal: backend.journal,
        recorder: backend.recorder,
        citations: new SharedCitationService(records),
        artifacts: new SharedArtifactService(artifactAccess),
        copier: registry,
        workspaces: {
          service: backend.workspaceService,
          objects: new WorkspaceObjectResealer(backend.keys, backend.objects),
        },
        transaction: backend.transaction,
        newSessionId: backend.newId,
        logger,
        ...overrides,
      });
      const { asOrg } = backend;
      if (asOrg !== undefined) {
        const fork = service.fork.bind(service);
        service.fork = (request) => asOrg(request.orgId, () => fork(request));
      }
      return service;
    },
  };
}

/** Files written as a worker writes them: each object sealed with the session key, the file list with the org key. */
export async function seedWorkspace<Tx>(
  backend: ForkBackend<Tx>,
  orgId: string,
  sessionId: string,
  files: Array<{ path: string; text: string; mode?: number }>,
): Promise<void> {
  const issued = await backend.keys.issue(orgId, sessionId);
  const scope = { tenantId: orgId, partition: sessionWorkspaceId(sessionId) };
  const entries = new Map<string, WorkspaceFileEntry>();
  for (const file of files) {
    const sealed = sealObject(scope, { keyId: issued.keyId, key: issued.key }, Buffer.from(file.text));
    const inner = await backend.objects.put(orgId, sealed.stored);
    entries.set(file.path, {
      path: file.path,
      objectId: encodeSealedId(inner),
      contentHash: sealed.contentHash,
      sizeBytes: sealed.sizeBytes,
      mimeType: 'text/plain',
      ...(file.mode !== undefined ? { mode: file.mode } : {}),
    });
  }
  const workspaceId = sessionWorkspaceId(sessionId);
  const sorted = [...entries.values()].sort((a, b) => (a.path < b.path ? -1 : 1));
  await backend.workspaces.append({
    expectedRevision: 0,
    manifest: {
      orgId,
      workspaceId,
      revision: 1,
      parentRevision: null,
      contentHash: manifestContentHash(entries),
      fileCount: entries.size,
      files: await backend.cipher.seal(orgId, 'workspace.manifest', manifestSealContext(workspaceId, 1), Buffer.from(JSON.stringify(sorted))),
      reason: 'turn_end',
      runId: `run-${sessionId}`,
      createdAt: 1,
    },
  });
}

export async function workspaceFiles<Tx>(
  backend: ForkBackend<Tx>,
  orgId: string,
  sessionId: string,
): Promise<{ revision: number; files: WorkspaceFileEntry[] } | null> {
  const workspaceId = sessionWorkspaceId(sessionId);
  const head = await backend.workspaces.head(orgId, workspaceId);
  if (head === null) return null;
  const stored = (await backend.workspaces.manifest(orgId, workspaceId, head.revision))!;
  const files = JSON.parse(
    (await backend.cipher.open(orgId, 'workspace.manifest', manifestSealContext(workspaceId, head.revision), stored.files)).toString('utf8'),
  ) as WorkspaceFileEntry[];
  return { revision: head.revision, files };
}

/** A file's plaintext as the session's own key opens it (what the session's worker would read). */
export async function readFile<Tx>(
  backend: ForkBackend<Tx>,
  orgId: string,
  sessionId: string,
  entry: WorkspaceFileEntry,
  asSessionId = sessionId,
): Promise<string> {
  const issued = await backend.keys.issue(orgId, asSessionId);
  const stored = await backend.objects.get(orgId, decodeSealedId(entry.objectId));
  return openObject(
    { tenantId: orgId, partition: sessionWorkspaceId(asSessionId) },
    { keyId: issued.keyId, key: issued.key },
    stored,
    entry,
  ).toString('utf8');
}

export const allEvents = async <Tx>(backend: ForkBackend<Tx>, orgId: string, streamId: string): Promise<JournalEvent[]> =>
  backend.journal.readStream(orgId, streamId, 0, 1_000);

/** The shared session of the acceptance case (AT-1): Alice owns it, Bob collaborates, Carol views. */
export interface Scenario {
  orgId: string;
  alice: string;
  bob: string;
  carol: string;
  sessionId: string;
}

export async function aliceAndBob<Tx>(world: ForkWorld<Tx>, agentKey?: string): Promise<Scenario> {
  const id = (): string => new Types.ObjectId().toString();
  const [orgId, alice, bob, carol] = [id(), id(), id(), id()];
  // Stored citations carry their org (erasure pins its delete to it).
  const cited = (recordId: string, chunkIndex: number): ForkCitationData => {
    const citation = recordCitation(recordId, chunkIndex) as unknown as ForkCitationData;
    return { ...citation, metadata: { ...citation.metadata, orgId } };
  };
  const sessionId = await world.backend.seed({
    orgId,
    ownerId: alice,
    shares: [
      [bob, 'write'],
      [carol, 'read'],
    ],
    ...(agentKey !== undefined ? { agentKey } : {}),
    title: 'Quarterly plan',
    messages: [
      { messageType: 'user_query', senderId: null, content: `${SENTINEL} question one` },
      {
        messageType: 'bot_response',
        senderId: null,
        content: `${SENTINEL} answer one ${marker('art-alice', 1)}`,
        citations: [cited('rec-R', 0)],
        extra: { tools: [{ toolName: 'search', toolResult: SENTINEL }] },
      },
      { messageType: 'tool_call', senderId: null, content: `${SENTINEL} tool row` },
      { messageType: 'user_query', senderId: bob, content: 'BOB question two' },
      {
        messageType: 'bot_response',
        senderId: bob,
        content: `BOB answer two ${marker('art-b1', 2)} and ${marker('art-b2', 1)} ::download_conversation_task[x.csv](https://files.test/x)`,
        citations: [cited('rec-R2', 0), cited('rec-R3', 1)],
        extra: {
          reasoning: [{ content: 'BOB-REASONING' }],
          parts: [{ type: 'text', content: 'BOB-PART', runId: 'run-bob-two' }],
          runId: 'run-bob-two',
          referenceData: [{ name: 'BOB-REF', id: '1' }],
          attachments: [{ recordId: 'att-1', recordName: 'BOB-ATTACHMENT' }],
          feedback: [{ isHelpful: true }],
          confidence: 'High',
        },
      },
      { messageType: 'user_query', senderId: null, content: `${SENTINEL} question three` },
      { messageType: 'bot_response', senderId: null, content: `${SENTINEL} answer three` },
    ],
  });
  world.registry.add(orgId, sessionId, alice, 'art-alice', `${SENTINEL} artifact`);
  world.registry.add(orgId, sessionId, bob, 'art-b1', 'B1 bytes');
  world.registry.add(orgId, sessionId, bob, 'art-b2', 'B2 bytes');
  world.records.grant(bearer(bob), 'rec-R2');
  world.records.grant(bearer(alice), 'rec-R', 'rec-R2', 'rec-R3');
  world.artifactAccess.grant(bearer(bob), 'art-b1');
  return { orgId, alice, bob, carol, sessionId };
}

export const forkAs = async <Tx>(
  world: ForkWorld<Tx>,
  s: Scenario,
  actorId: string,
  requestKey = 'key-1',
  overrides: Parameters<ForkWorld<Tx>['service']>[0] = {},
): Promise<ForkOutcome> =>
  world.service(overrides).fork({
    orgId: s.orgId,
    sessionId: s.sessionId,
    actorId,
    requestKey,
    authorization: bearer(actorId),
    requestId: 'req-1',
  });

const refusalOf = async (work: Promise<unknown>): Promise<unknown> => {
  try {
    await work;
  } catch (error) {
    return error;
  }
  return expect.fail('expected a refusal');
};

const STUB_PAIR = [
  { messageType: 'user_query', content: FOREIGN_TURN_STUB, foreignTurn: true },
  { messageType: 'bot_response', content: FOREIGN_TURN_STUB, foreignTurn: true },
];

const COPIED_FIELDS = new Set(['messageType', 'content', 'citations', 'contentFormat', 'status', 'confidence', 'followUpQuestions', 'modelInfo', 'appliedFilters', 'metadata', 'foreignTurn', 'copied']);

export function describeSessionForkContract<Tx>(name: string, createBackend: () => ForkBackend<Tx>): void {
  describe(`continue privately (SS3): ${name}`, function () {
    this.timeout(60_000);
    let world: ForkWorld<Tx>;

    beforeEach(() => {
      world = forkWorld(createBackend());
    });

    it('FK-3, AT-1: Bob forks Alice\'s shared session: his turns copied, hers stubbed, nothing of hers anywhere', async () => {
      const s = await aliceAndBob(world);
      const outcome = await forkAs(world, s, s.bob);
      expect(outcome).to.deep.include({ fromSessionId: s.sessionId, role: 'collaborator', replayed: false });
      expect(outcome.sessionId).to.not.equal(s.sessionId);
      const conversation = await world.backend.conversation(outcome.sessionId);
      expect(conversation).to.deep.equal({ ownerId: s.bob, isShared: false, shares: 0, sessionType: 'chat', agentKey: null, title: 'Quarterly plan (copy)', projectId: null, durable: false });
      const messages = await world.backend.messages(outcome.sessionId);
      expect(messages.map(({ messageType, content, foreignTurn }) => ({ messageType, content: foreignTurn ? content : messageType, foreignTurn }))).to.deep.equal([
        ...STUB_PAIR,
        { messageType: 'user_query', content: 'user_query', foreignTurn: false },
        { messageType: 'bot_response', content: 'bot_response', foreignTurn: false },
        ...STUB_PAIR,
      ]);
      expect(messages.every((message) => message.senderId === null)).to.equal(true);
      expect(messages[2]!.content).to.equal('BOB question two');
      for (const message of messages) {
        for (const field of message.fields) expect(COPIED_FIELDS.has(field), field).to.equal(true);
      }
      expect(messages[3]!.fields).to.include('confidence');
      // FORKFIX: a copied turn names none of the source's runs, so the fork never asks a run route about one.
      expect(messages.map((m) => m.copied)).to.deep.equal([false, false, true, true, false, false]);
      // AT-1: no trace of Alice's turns or of what Bob may not see, in any store the fork wrote.
      const dump = JSON.stringify({ conversation, messages, events: await allEvents(world.backend, s.orgId, outcome.sessionId) });
      for (const hidden of [SENTINEL, 'rec-R"', 'rec-R3', 'art-alice', 'art-b2', 'BOB-REASONING', 'BOB-PART', 'BOB-REF', 'BOB-ATTACHMENT', 'files.test/x', 'run-bob-two']) {
        expect(dump, hidden).to.not.include(hidden);
      }
      expect(world.registry.of(s.orgId, outcome.sessionId).map((id) => world.registry.artifacts.get(id)!.content)).to.deep.equal(['B1 bytes']);
      // The source is as it was.
      expect((await world.backend.conversation(s.sessionId))?.shares).to.equal(2);
      expect(await world.backend.messages(s.sessionId)).to.have.length(7);
    });

    it('FK-4: a non-owner\'s citations are checked with their credential; readable ones copied as new documents', async () => {
      const s = await aliceAndBob(world);
      const outcome = await forkAs(world, s, s.bob);
      const answer = (await world.backend.messages(outcome.sessionId))[3]!;
      expect(answer.citations.map((c) => (c.data?.metadata as { recordId?: string }).recordId)).to.deep.equal(['rec-R2']);
      expect(world.records.queries.map((q) => q.authorization)).to.deep.equal([bearer(s.bob)]);
      const sourceIds = new Set((await world.backend.messages(s.sessionId)).flatMap((m) => m.citations.map((c) => c.citationId)));
      expect(sourceIds.has(answer.citations[0]!.citationId)).to.equal(false);
      expect(outcome.counts).to.deep.include({ messages: 2, stubs: 4, citations: 1, citationsHidden: 1 });
    });

    it('FK-4: the owner\'s own citations are copied unchecked, as their conversation shows them', async () => {
      const s = await aliceAndBob(world);
      const outcome = await forkAs(world, s, s.alice);
      const messages = await world.backend.messages(outcome.sessionId);
      expect(messages.map((m) => (m.foreignTurn ? 'stub' : m.messageType))).to.deep.equal(['user_query', 'bot_response', 'stub', 'stub', 'user_query', 'bot_response']);
      expect(messages[1]!.citations.map((c) => (c.data?.metadata as { recordId?: string }).recordId)).to.deep.equal(['rec-R']);
      expect(world.records.queries).to.deep.equal([]);
      expect(JSON.stringify(messages)).to.not.include('BOB');
    });

    it('FK-5: visible artifacts of own answers are copied as the forker and markers rewritten; the rest become placeholders', async () => {
      const s = await aliceAndBob(world);
      const outcome = await forkAs(world, s, s.bob);
      expect(world.artifactAccess.queries).to.deep.equal([{ authorization: bearer(s.bob), artifactIds: ['art-b1', 'art-b2'] }]);
      expect(world.registry.requests).to.deep.equal([
        { orgId: s.orgId, fromConversationId: s.sessionId, toConversationId: outcome.sessionId, actorId: s.bob, artifacts: [{ artifactId: 'art-b1', version: 2 }] },
      ]);
      const answer = (await world.backend.messages(outcome.sessionId))[3]!;
      const copyId = `copy-${outcome.sessionId}-art-b1-2`;
      expect(answer.content).to.equal(
        `BOB answer two ::artifact[art-b1.png](record:${copyId}){image/png|blob-${copyId}|${copyId}|IMAGE|1} and ${UNAVAILABLE_ARTIFACT_MARKER} ${UNAVAILABLE_ARTIFACT_MARKER}`,
      );
      expect(outcome.counts).to.deep.include({ artifacts: 1, artifactsHidden: 1 });
    });

    it('FK-6: the owner\'s fork has the source head\'s files under its own key; nobody else\'s fork has a workspace', async () => {
      const s = await aliceAndBob(world);
      await seedWorkspace(world.backend, s.orgId, s.sessionId, [
        { path: 'report.md', text: 'quarterly report' },
        { path: 'bin/run.sh', text: '#!/bin/sh\necho hi\n', mode: 0o100755 },
      ]);
      const owner = await forkAs(world, s, s.alice);
      expect(owner.counts.files).to.equal(2);
      const source = (await workspaceFiles(world.backend, s.orgId, s.sessionId))!;
      const forked = (await workspaceFiles(world.backend, s.orgId, owner.sessionId))!;
      expect(forked.revision).to.equal(1);
      expect(forked.files.map(({ path, sizeBytes, mode }) => ({ path, sizeBytes, mode }))).to.deep.equal(
        source.files.map(({ path, sizeBytes, mode }) => ({ path, sizeBytes, mode })),
      );
      const [sourceKey, forkKey] = await Promise.all([world.backend.keys.issue(s.orgId, s.sessionId), world.backend.keys.issue(s.orgId, owner.sessionId)]);
      expect(forkKey.keyId).to.not.equal(sourceKey.keyId);
      for (const [index, entry] of forked.files.entries()) {
        const original = source.files[index]!;
        expect(entry.objectId).to.not.equal(original.objectId);
        expect(entry.contentHash).to.not.equal(original.contentHash);
        expect(await readFile(world.backend, s.orgId, owner.sessionId, entry)).to.equal(await readFile(world.backend, s.orgId, s.sessionId, original));
        const wrongKey = await refusalOf(readFile(world.backend, s.orgId, owner.sessionId, entry, s.sessionId));
        expect(wrongKey).to.be.instanceOf(ObjectUnreadableError);
      }
      const collaborator = await forkAs(world, s, s.bob);
      expect(collaborator.counts.files).to.equal(0);
      expect(await workspaceFiles(world.backend, s.orgId, collaborator.sessionId)).to.equal(null);
    });

    it('FK-6: a source without a workspace gives the owner\'s fork none', async () => {
      const s = await aliceAndBob(world);
      const outcome = await forkAs(world, s, s.alice);
      expect(outcome.counts.files).to.equal(0);
      expect(await workspaceFiles(world.backend, s.orgId, outcome.sessionId)).to.equal(null);
    });

    it('FK-7: session.forked is the fork\'s first event and the source stream\'s next one, in one transaction', async () => {
      const s = await aliceAndBob(world);
      const before = await allEvents(world.backend, s.orgId, s.sessionId);
      const outcome = await forkAs(world, s, s.bob);
      expect(outcome.atSeq).to.equal(before.length);
      const [first, ...rest] = await allEvents(world.backend, s.orgId, outcome.sessionId);
      expect(rest).to.deep.equal([]);
      expect(first).to.deep.include({ seq: 1, type: 'session.forked', writer: 'node', visibility: 'shared', critical: false });
      expect(first!.data).to.deep.equal({ by: `p_${s.bob}`, fromSessionId: s.sessionId, atSeq: before.length });
      const after = await allEvents(world.backend, s.orgId, s.sessionId);
      expect(after.slice(0, before.length)).to.deep.equal(before);
      expect(after.slice(before.length).map((e) => [e.seq, e.type, e.visibility, e.data])).to.deep.equal([
        [before.length + 1, 'session.forked', 'audit', { by: `p_${s.bob}`, fromSessionId: s.sessionId, atSeq: before.length, intoSessionId: outcome.sessionId }],
      ]);
    });

    it('FK-8: a failure inside the transaction leaves nothing of the fork; the same request then completes', async () => {
      const s = await aliceAndBob(world);
      const failed = await refusalOf(forkAs(world, s, s.bob, 'key-1', { recorder: world.backend.failingRecorder }));
      expect((failed as Error).message).to.equal('journal unavailable');
      const record = await world.backend.store.find({ orgId: s.orgId, sourceSessionId: s.sessionId, actorId: s.bob, requestKey: 'key-1' });
      expect(record?.result).to.equal(null);
      const pending = record!.forkSessionId;
      expect(await world.backend.conversation(pending)).to.equal(null);
      expect(await world.backend.messages(pending)).to.deep.equal([]);
      expect(await allEvents(world.backend, s.orgId, pending)).to.deep.equal([]);
      expect((await allEvents(world.backend, s.orgId, s.sessionId)).some((e) => e.type === 'session.forked')).to.equal(false);
      const citationsBefore = (await world.backend.citationIds()).size;
      const retried = await forkAs(world, s, s.bob, 'key-1');
      expect(retried.sessionId).to.equal(pending);
      expect(retried.replayed).to.equal(false);
      expect((await world.backend.citationIds()).size).to.equal(citationsBefore + 1);
      // The artifact copied by the failed try is reused, not copied again.
      expect(world.registry.of(s.orgId, pending)).to.have.length(1);
    });

    it('FK-9: the same request answers the same fork and writes nothing more; another key makes another fork', async () => {
      const s = await aliceAndBob(world);
      const first = await forkAs(world, s, s.bob, 'key-1');
      const messages = await world.backend.messages(first.sessionId);
      const events = await allEvents(world.backend, s.orgId, s.sessionId);
      const again = await forkAs(world, s, s.bob, 'key-1');
      expect(again).to.deep.equal({ ...first, replayed: true });
      expect(await world.backend.messages(first.sessionId)).to.deep.equal(messages);
      expect(await allEvents(world.backend, s.orgId, s.sessionId)).to.deep.equal(events);
      expect(world.registry.requests).to.have.length(1);
      const other = await forkAs(world, s, s.bob, 'key-2');
      expect(other.sessionId).to.not.equal(first.sessionId);
      const carols = await forkAs(world, s, s.carol, 'key-1');
      expect(carols.sessionId).to.not.equal(first.sessionId);
    });

    it('FK-10: a run active in the source refuses the fork (RUN_ACTIVE), nothing written', async () => {
      const s = await aliceAndBob(world);
      const events = await allEvents(world.backend, s.orgId, s.sessionId);
      world.runs.active.add(s.sessionId);
      const refused = await refusalOf(forkAs(world, s, s.bob));
      expect(refused).to.be.instanceOf(HarnessConflictError);
      expect((refused as HarnessConflictError).code).to.equal('RUN_ACTIVE');
      expect(await world.backend.store.find({ orgId: s.orgId, sourceSessionId: s.sessionId, actorId: s.bob, requestKey: 'key-1' })).to.equal(null);
      expect(await allEvents(world.backend, s.orgId, s.sessionId)).to.deep.equal(events);
      expect(world.registry.requests).to.deep.equal([]);
      world.runs.active.delete(s.sessionId);
      expect((await forkAs(world, s, s.bob)).replayed).to.equal(false);
    });

    it('FK-11: only a participant forks: a stranger and a revoked share get not found; a viewer gets every turn as a stub', async () => {
      const s = await aliceAndBob(world);
      const stranger = new Types.ObjectId().toString();
      expect(await refusalOf(forkAs(world, s, stranger))).to.be.instanceOf(HarnessNotFoundError);
      const viewer = await forkAs(world, s, s.carol);
      expect(viewer.role).to.equal('viewer');
      expect((await world.backend.messages(viewer.sessionId)).map((m) => [m.messageType, m.content])).to.deep.equal(
        STUB_PAIR.map((m) => [m.messageType, m.content]),
      );
      expect(world.registry.requests).to.deep.equal([]);
      await world.backend.unshare(s.sessionId, s.bob);
      expect(await refusalOf(forkAs(world, s, s.bob))).to.be.instanceOf(HarnessNotFoundError);
      expect(world.lines.filter(([level]) => level === 'warn').map(([, , meta]) => meta['reason'])).to.deep.equal(['not_found', 'not_found']);
    });

    it('FK-11: the route\'s kind is honoured: a chat id asked as an agent conversation is not found', async () => {
      const s = await aliceAndBob(world, 'agent-1');
      const asChat = await refusalOf(world.service().fork({ orgId: s.orgId, sessionId: s.sessionId, actorId: s.bob, requestKey: 'k', authorization: bearer(s.bob) }));
      expect(asChat).to.be.instanceOf(HarnessNotFoundError);
      const outcome = await world.service().fork({ orgId: s.orgId, sessionId: s.sessionId, actorId: s.bob, agentKey: 'agent-1', requestKey: 'k', authorization: bearer(s.bob) });
      expect(await world.backend.conversation(outcome.sessionId)).to.deep.include({ sessionType: 'agent', agentKey: 'agent-1' });
    });

    it('SLASH2 /fork: a durable conversation forks into a durable one; a legacy one does not', async () => {
      const id = (): string => new Types.ObjectId().toString();
      const [orgId, owner] = [id(), id()];
      for (const durable of [true, false]) {
        const sessionId = await world.backend.seed({
          orgId,
          ownerId: owner,
          shares: [],
          title: 'durable?',
          durable,
          messages: [
            { messageType: 'user_query', senderId: null, content: 'q' },
            { messageType: 'bot_response', senderId: null, content: 'a' },
          ],
        });
        const outcome = await world.service().fork({ orgId, sessionId, actorId: owner, requestKey: `k-${String(durable)}`, authorization: bearer(owner) });
        expect((await world.backend.conversation(outcome.sessionId))?.durable, `durable ${String(durable)}`).to.equal(durable);
        expect(await world.backend.messages(outcome.sessionId)).to.have.length(2);
      }
    });

    it('FK-12: no copier while there are artifacts to copy, or no workspace service for an owner: 503, nothing written', async () => {
      const s = await aliceAndBob(world);
      expect(await refusalOf(forkAs(world, s, s.bob, 'k-a', { copier: null }))).to.be.instanceOf(HarnessUnavailableError);
      expect(await refusalOf(forkAs(world, s, s.alice, 'k-b', { workspaces: null }))).to.be.instanceOf(HarnessUnavailableError);
      // A viewer has nothing to copy: no copier needed.
      expect((await forkAs(world, s, s.carol, 'k-c', { copier: null, workspaces: null })).role).to.equal('viewer');
      expect((await allEvents(world.backend, s.orgId, s.sessionId)).filter((e) => e.type === 'session.forked')).to.have.length(1);
    });

    it('FK-14: a workspace over the fork limit is refused (WORKSPACE_TOO_LARGE), nothing written', async () => {
      const s = await aliceAndBob(world);
      await seedWorkspace(world.backend, s.orgId, s.sessionId, [{ path: 'a.txt', text: 'a' }]);
      const workspaceId = sessionWorkspaceId(s.sessionId);
      const head = (await world.backend.workspaces.head(s.orgId, workspaceId))!;
      const stored = (await world.backend.workspaces.manifest(s.orgId, workspaceId, head.revision))!;
      const files = JSON.parse((await world.backend.cipher.open(s.orgId, 'workspace.manifest', manifestSealContext(workspaceId, 1), stored.files)).toString()) as WorkspaceFileEntry[];
      const huge = new Map([...files, { ...files[0]!, path: 'big.bin', sizeBytes: 600 * 1024 * 1024 }].map((f) => [f.path, f]));
      await world.backend.workspaces.append({
        expectedRevision: 1,
        manifest: {
          ...stored,
          revision: 2,
          parentRevision: 1,
          contentHash: manifestContentHash(huge),
          fileCount: huge.size,
          files: await world.backend.cipher.seal(s.orgId, 'workspace.manifest', manifestSealContext(workspaceId, 2), Buffer.from(JSON.stringify([...huge.values()]))),
        },
      });
      const refused = await refusalOf(forkAs(world, s, s.alice));
      expect(refused).to.be.instanceOf(WorkspaceError);
      expect((refused as WorkspaceError).code).to.equal('WORKSPACE_TOO_LARGE');
      expect((await allEvents(world.backend, s.orgId, s.sessionId)).some((e) => e.type === 'session.forked')).to.equal(false);
      expect(world.lines.some(([level, , meta]) => level === 'warn' && meta['reason'] === 'workspace_too_large')).to.equal(true);
    });

    describe('turns whose label passes for the forker (P2.5a)', () => {
      const indexed = (recordId: string) => ({ kind: 'indexed' as const, recordId });
      const labelOf = (...ids: string[]): TurnLabel => ({ complete: true, sources: ids.map(indexed) });
      const id = (): string => new Types.ObjectId().toString();

      /** Alice owns it; her turns: A (from rec-A), B (from rec-B), C (unlabelled); then Bob's own turn (from rec-X). */
      const labelled = async (): Promise<Scenario> => {
        const [orgId, alice, bob, carol] = [id(), id(), id(), id()];
        const cited = (recordId: string): ForkCitationData => {
          const citation = recordCitation(recordId, 0) as unknown as ForkCitationData;
          return { ...citation, metadata: { ...citation.metadata, orgId } };
        };
        const sessionId = await world.backend.seed({
          orgId,
          ownerId: alice,
          shares: [[bob, 'write']],
          title: 'Labels',
          messages: [
            { messageType: 'user_query', senderId: null, content: 'ALICE-A question' },
            { messageType: 'bot_response', senderId: null, content: 'ALICE-A answer', citations: [cited('rec-A')], label: labelOf('rec-A') },
            { messageType: 'user_query', senderId: null, content: 'ALICE-B question' },
            { messageType: 'bot_response', senderId: null, content: 'ALICE-B answer', label: labelOf('rec-A', 'rec-B') },
            { messageType: 'user_query', senderId: null, content: 'ALICE-C question' },
            { messageType: 'bot_response', senderId: null, content: 'ALICE-C answer' },
            { messageType: 'user_query', senderId: bob, content: 'BOB question' },
            { messageType: 'bot_response', senderId: bob, content: 'BOB answer', citations: [cited('rec-X')], label: labelOf('rec-X') },
          ],
        });
        world.records.grant(bearer(bob), 'rec-A', 'rec-X');
        world.records.grant(bearer(alice), 'rec-A', 'rec-B', 'rec-X');
        return { orgId, alice, bob, carol, sessionId };
      };

      it('S3-2: Bob\'s fork copies Alice\'s turn he may read, checked with his credential; the rest stay stubs', async () => {
        const s = await labelled();
        const outcome = await forkAs(world, s, s.bob);
        const messages = await world.backend.messages(outcome.sessionId);
        expect(messages.map((m) => (m.foreignTurn ? 'stub' : m.content))).to.deep.equal([
          'ALICE-A question',
          'ALICE-A answer',
          'stub',
          'stub',
          'BOB question',
          'BOB answer',
        ]);
        expect(messages[1]!.citations.map((c) => (c.data?.metadata as { recordId?: string }).recordId)).to.deep.equal(['rec-A']);
        expect(messages.every((m) => m.senderId === null)).to.equal(true);
        expect(world.records.queries.every((q) => q.authorization === bearer(s.bob))).to.equal(true);
        const dump = JSON.stringify(messages);
        for (const hidden of ['ALICE-B', 'ALICE-C', 'rec-B']) expect(dump, hidden).to.not.include(hidden);
        expect(outcome.counts).to.deep.include({ messages: 4, stubs: 2, citations: 2, citationsHidden: 0 });
      });

      it('S3-3: Alice\'s fork copies Bob\'s turn she may read, checked with her credential; her own turns as before', async () => {
        const s = await labelled();
        const outcome = await forkAs(world, s, s.alice);
        const messages = await world.backend.messages(outcome.sessionId);
        expect(messages.map((m) => (m.foreignTurn ? 'stub' : m.content))).to.deep.equal([
          'ALICE-A question',
          'ALICE-A answer',
          'ALICE-B question',
          'ALICE-B answer',
          'ALICE-C question',
          'ALICE-C answer',
          'BOB question',
          'BOB answer',
        ]);
        expect(messages[7]!.citations.map((c) => (c.data?.metadata as { recordId?: string }).recordId)).to.deep.equal(['rec-X']);
        expect(world.records.queries.map((q) => q.authorization)).to.deep.equal([bearer(s.alice), bearer(s.alice)]);
      });

      it('S3-3: a revoked source means the next fork stubs the turn again', async () => {
        const s = await labelled();
        world.records.revoke(bearer(s.bob), 'rec-A');
        const outcome = await forkAs(world, s, s.bob);
        const messages = await world.backend.messages(outcome.sessionId);
        expect(messages.map((m) => (m.foreignTurn ? 'stub' : m.content))).to.deep.equal(['stub', 'stub', 'BOB question', 'BOB answer']);
      });
    });

    it('FK-L: one info line per fork with ids and counts; no text, names or paths', async () => {
      const s = await aliceAndBob(world);
      await seedWorkspace(world.backend, s.orgId, s.sessionId, [{ path: 'SECRET-PATH.md', text: 'x' }]);
      await forkAs(world, s, s.alice);
      const completed = world.lines.filter(([, message]) => message === 'session.fork_completed');
      expect(completed).to.have.length(1);
      expect(completed[0]![2]).to.include({ orgId: s.orgId, sessionId: s.sessionId, actorId: s.alice, role: 'owner', files: 1, messages: 4, stubs: 2, replayed: false });
      const dump = JSON.stringify(world.lines);
      for (const hidden of [SENTINEL, 'BOB', 'SECRET-PATH', 'Quarterly']) expect(dump).to.not.include(hidden);
    });
  });
}
