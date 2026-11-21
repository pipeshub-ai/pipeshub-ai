import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { Types } from 'mongoose';
import { InMemoryJournalStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import {
  InMemoryForkTx,
  InMemorySessionForkStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-session-forks';
import { InMemoryWorkspaceObjects } from '../../../src/modules/agent-harness/adapters/in-memory-workspace-objects';
import {
  InMemoryWorkspaceKeyStore,
  InMemoryWorkspaceRepository,
  InMemoryWorkspaceWriterStore,
} from '../../../src/modules/agent-harness/adapters/in-memory-workspace-store';
import { SessionParticipantJournal } from '../../../src/modules/agent-harness/application/session-participant-journal';
import { SessionWorkspaceKeys } from '../../../src/modules/agent-harness/application/session-workspace-keys';
import { WorkspaceService } from '../../../src/modules/agent-harness/application/workspace-service';
import { FOREIGN_TURN_STUB } from '../../../src/modules/agent-harness/domain/session-audience';
import {
  forkTranscript,
  ForkSourceMessage,
} from '../../../src/modules/agent-harness/domain/session-forks';
import { validatingPreparer } from '../../../src/modules/agent-harness/ports/journal-store';
import {
  markerArtifactRefs,
  rewriteArtifactMarkers,
  UNAVAILABLE_ARTIFACT_MARKER,
} from '../../../src/modules/agent-harness/projection/artifact-access';
import { testCipher } from './support/data-protection';
import {
  describeSessionForkContract,
  ForkBackend,
  StoredMessage,
} from './support/session-fork-contract';

const message = (messageType: string, senderId: string | null, content: string): ForkSourceMessage => ({
  messageType,
  senderId,
  content,
  citations: [],
  fields: { confidence: 'High' },
});

describe('SS3 fork transcript (FK-1) and artifact markers (FK-2)', () => {
  const OWNER = 'owner';
  const BOB = 'bob';

  it('FK-1: own questions and answers kept; each run of someone else\'s turns is one stub pair; other types dropped', () => {
    const projected = forkTranscript(
      [
        message('user_query', null, 'owner q1'),
        message('bot_response', null, 'owner a1'),
        message('tool_call', BOB, 'bob tool'),
        message('user_query', BOB, 'bob q2'),
        message('bot_response', BOB, 'bob a2'),
        message('error', BOB, 'bob error'),
        message('user_query', null, 'owner q3'),
        message('bot_response', null, 'owner a3'),
        message('user_query', 'carol', 'carol q4'),
        message('bot_response', 'carol', 'carol a4'),
      ],
      BOB,
      OWNER,
    );
    expect(projected.map((m) => [m.messageType, m.content, m.foreignTurn])).to.deep.equal([
      ['user_query', FOREIGN_TURN_STUB, true],
      ['bot_response', FOREIGN_TURN_STUB, true],
      ['user_query', 'bob q2', false],
      ['bot_response', 'bob a2', false],
      ['user_query', FOREIGN_TURN_STUB, true],
      ['bot_response', FOREIGN_TURN_STUB, true],
    ]);
    expect(projected[3]!.fields).to.deep.equal({ confidence: 'High' });
    expect(projected[0]!.fields).to.deep.equal({});
  });

  it('FK-1: a message without a sender is the owner\'s', () => {
    const asOwner = forkTranscript([message('user_query', null, 'mine'), message('bot_response', null, 'mine too')], OWNER, OWNER);
    expect(asOwner.map((m) => m.foreignTurn)).to.deep.equal([false, false]);
    const asBob = forkTranscript([message('user_query', null, 'theirs')], BOB, OWNER);
    expect(asBob.map((m) => m.content)).to.deep.equal([FOREIGN_TURN_STUB, FOREIGN_TURN_STUB]);
  });

  const full = '::artifact[a.png](record:A){image/png|doc-A|A|IMAGE|3}';
  const noUrl = '::artifact[b.csv]{text/csv|doc-B|B|DATA|1}';
  const legacy = '::artifact[c.pdf](https://signed/url){application/pdf|doc-C|C}';
  const short = '::artifact[d.png](https://x)';
  const download = '::download_conversation_task[e.csv](https://files/e)';

  it('FK-2: every marker form gives its artifact and version, once', () => {
    expect(markerArtifactRefs(`${full} ${noUrl} ${legacy} ${short} ${download} ${full}`)).to.deep.equal([
      { artifactId: 'A', version: 3 },
      { artifactId: 'B', version: 1 },
      { artifactId: 'C', version: null },
    ]);
  });

  it('FK-2: copied versions point at their copy; everything else becomes the placeholder', () => {
    const copies = new Map([
      ['A@3', { artifactId: 'A2', documentId: 'doc-A2', version: 1 }],
      ['C@', { artifactId: 'C2', documentId: 'doc-C2', version: 1 }],
    ]);
    expect(rewriteArtifactMarkers(`x ${full} ${noUrl} ${legacy} ${short} ${download} y`, copies)).to.equal(
      [
        'x',
        '::artifact[a.png](record:A2){image/png|doc-A2|A2|IMAGE|1}',
        UNAVAILABLE_ARTIFACT_MARKER,
        '::artifact[c.pdf](record:C2){application/pdf|doc-C2|C2||1}',
        UNAVAILABLE_ARTIFACT_MARKER,
        UNAVAILABLE_ARTIFACT_MARKER,
        'y',
      ].join(' '),
    );
    expect(rewriteArtifactMarkers('plain text', copies)).to.equal('plain text');
  });
});

function memoryBackend(): ForkBackend<InMemoryForkTx> {
  const store = new InMemorySessionForkStore();
  const journal = new InMemoryJournalStore();
  const cipher = testCipher();
  const workspaces = new InMemoryWorkspaceRepository();
  const keys = new SessionWorkspaceKeys({ store: new InMemoryWorkspaceKeyStore(), cipher });
  const recorder = new SessionParticipantJournal<InMemoryForkTx>(journal, validatingPreparer);
  return {
    store,
    transaction: (work) => store.transaction(work),
    journal,
    recorder,
    failingRecorder: {
      participantsChanged: () => Promise.reject(new Error('journal unavailable')),
      forked: () => Promise.reject(new Error('journal unavailable')),
    },
    cipher,
    workspaces,
    keys,
    objects: new InMemoryWorkspaceObjects(),
    workspaceService: new WorkspaceService({
      runs: { get: () => Promise.resolve(null) },
      repository: workspaces,
      cipher,
      keys,
      writers: new InMemoryWorkspaceWriterStore(),
    }),
    newId: () => new Types.ObjectId().toString(),
    seed: (conversation) => {
      const sessionId = new Types.ObjectId().toString();
      store.conversations.set(sessionId, {
        orgId: conversation.orgId,
        participants: {
          ownerId: conversation.ownerId,
          isShared: conversation.shares.length > 0,
          shares: conversation.shares.map(([userId, accessLevel]) => ({ userId, accessLevel })),
        },
        sessionType: conversation.agentKey === undefined ? 'chat' : 'agent',
        agentKey: conversation.agentKey ?? null,
        title: conversation.title,
        modelInfo: { modelName: 'm' },
        ...(conversation.durable === true ? { durable: true } : {}),
        deleted: false,
        messages: conversation.messages.map((m) => ({
          messageType: m.messageType,
          senderId: m.senderId,
          content: m.content,
          citations: (m.citations ?? []).map((data) => ({ citationId: store.addCitation(data) })),
          fields: typeof m.extra?.['confidence'] === 'string' ? { confidence: m.extra['confidence'] } : {},
          ...(m.extra !== undefined ? { extra: m.extra } : {}),
          ...(m.label !== undefined ? { label: m.label } : {}),
        })),
      });
      return Promise.resolve(sessionId);
    },
    unshare: (sessionId, userId) => {
      const conversation = store.conversations.get(sessionId)!;
      conversation.participants = {
        ...conversation.participants,
        shares: conversation.participants.shares.filter((share) => share.userId !== userId),
      };
      return Promise.resolve();
    },
    conversation: (sessionId) => {
      const conversation = store.conversations.get(sessionId);
      return Promise.resolve(
        conversation === undefined
          ? null
          : {
              ownerId: conversation.participants.ownerId,
              isShared: conversation.participants.isShared,
              shares: conversation.participants.shares.length,
              sessionType: conversation.sessionType,
              agentKey: conversation.agentKey,
              title: conversation.title,
              projectId: null,
              durable: conversation.durable === true,
            },
      );
    },
    messages: (sessionId) =>
      Promise.resolve(
        (store.conversations.get(sessionId)?.messages ?? []).map(
          (m): StoredMessage => ({
            messageType: m.messageType,
            content: m.content,
            senderId: m.senderId,
            foreignTurn: m.foreignTurn === true,
            copied: m.copied === true,
            citations: m.citations.map((c) => ({ citationId: c.citationId, data: store.citations.get(c.citationId) ?? null })),
            fields: [
              'messageType',
              'content',
              'citations',
              ...Object.keys(m.fields),
              ...(m.foreignTurn === true ? ['foreignTurn'] : []),
              ...(m.copied === true ? ['copied'] : []),
              ...Object.keys(m.extra ?? {}),
            ],
          }),
        ),
      ),
    citationIds: () => Promise.resolve(new Set(store.citations.keys())),
  };
}

describeSessionForkContract('in-memory', memoryBackend);

describe('SS3 in-memory transaction', () => {
  it('applies a commit only when the work succeeds', async () => {
    const store = new InMemorySessionForkStore();
    const key = { orgId: 'o', sourceSessionId: 's', actorId: 'a', requestKey: randomUUID() };
    const record = await store.begin(key, 'f');
    const commit = {
      key,
      forkSessionId: record.forkSessionId,
      source: { sessionType: 'chat' as const, agentKey: null, title: 't', modelInfo: null, durable: false },
      messages: [],
      result: { sessionId: 'f', fromSessionId: 's', atSeq: 0, role: 'owner' as const, counts: { messages: 0, stubs: 0, citations: 0, citationsHidden: 0, artifacts: 0, artifactsHidden: 0, files: 0 } },
      now: 1,
    };
    let caught: unknown;
    try {
      await store.transaction(async (tx) => {
        await store.commit(commit, tx);
        throw new Error('abort');
      });
    } catch (error) {
      caught = error;
    }
    expect((caught as Error).message).to.equal('abort');
    expect(store.conversations.has('f')).to.equal(false);
    await store.transaction((tx) => store.commit(commit, tx));
    expect(store.conversations.has('f')).to.equal(true);
  });
});
