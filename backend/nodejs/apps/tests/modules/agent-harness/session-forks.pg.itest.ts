/**
 * "Continue privately" (SS3) on PostgreSQL (WP-114): the fork request records (`harness.session_forks`) and the
 * conversation side through main's conversation repository (Alembic head, row-level security, PgBouncer in
 * transaction mode). The record's session id is chosen once under concurrent asks, a commit happens once under
 * concurrent commits, an aborted unit leaves neither the conversation nor the mark, the copies are copies (new
 * citation rows, `copied`, no run), the abandoned-fork sweep drops only what is still uncommitted, and another
 * organisation sees nothing. The SS3 contract runs here on the same stores, each fork request bound to its org as
 * its route binds it.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… mocha … tests/modules/agent-harness/session-forks.pg.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgConversationRepository } from '../../../src/libs/repositories/conversation/pg';
import {
  getConversationRepository,
  setConversationRepository,
  type NewMessage,
} from '../../../src/libs/repositories/conversation';
import { PgHarnessSessionForkStore } from '../../../src/libs/repositories/agent-harness/session-forks/pg';
import { PgAbandonedForks } from '../../../src/libs/repositories/agent-harness/abandoned-forks/pg';
import type { AbandonedFork } from '../../../src/modules/agent-harness/ports/session-forks';
import {
  ForkAlreadyCommittedError,
  type ForkCommit,
} from '../../../src/modules/agent-harness/ports/session-forks';
import type {
  ForkKey,
  ForkResult,
  ForkedMessage,
} from '../../../src/modules/agent-harness/domain/session-forks';
import { EMPTY_LEDGER } from '../../../src/modules/agent-harness/domain/session-import';
import {
  detachCopiedRuns,
  withForeignRunsDetached,
} from '../../../src/modules/enterprise_search/utils/copied-runs';
import { PgJournalStore } from '../../../src/libs/repositories/agent-harness/journal/pg';
import { PgPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/pg';
import { PgAuditChainStore, pgAuditLinksIdle } from '../../../src/libs/repositories/agent-harness/audit/pg';
import {
  PgWorkspaceKeyStore,
  PgWorkspaceRepository,
  PgWorkspaceWriterStore,
} from '../../../src/libs/repositories/agent-harness/workspace/pg';
import { inPgDurableTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/pg';
import { newObjectId } from '../../../src/libs/repositories/ids';
import type { Tx } from '../../../src/libs/repositories/uow';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { PayloadReferencingJournalStore } from '../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { SessionParticipantJournal } from '../../../src/modules/agent-harness/application/session-participant-journal';
import { SessionWorkspaceKeys } from '../../../src/modules/agent-harness/application/session-workspace-keys';
import { WorkspaceService } from '../../../src/modules/agent-harness/application/workspace-service';
import { InMemoryWorkspaceObjects } from '../../../src/modules/agent-harness/adapters/in-memory-workspace-objects';
import type { ForkCitationData } from '../../../src/modules/agent-harness/domain/session-forks';
import type { SessionParticipantRecorder } from '../../../src/modules/agent-harness/ports/session-participants';
import {
  type ForkBackend,
  type StoredMessage,
  describeSessionForkContract,
} from './support/session-fork-contract';
import { testCipher } from './support/data-protection';
import {
  PgStack,
  boundStore,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

const repo = () => getConversationRepository();

for (const bin of pgBins()) {
  pgSuite(
    `agent harness session forks on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_session_forks');
        setConversationRepository(new PgConversationRepository());
      });
      after(async () => {
        setConversationRepository();
        await stack?.stop();
      });
      beforeEach(() => resetHarnessPg(stack));

      /** The conversation's org, read outside every policy: the contract names a session by its id alone. */
      const orgOf = async (sessionId: string): Promise<string | null> =>
        (
          await stack.admin((c) =>
            c.query<{ org_id: string }>(`SELECT org_id FROM conversation.chat_sessions WHERE id = $1`, [sessionId]),
          )
        ).rows[0]?.org_id ?? null;

      const NOT_COPIED = new Set(['_id', 'sessionId', 'orgId', 'seq', 'createdAt', 'updatedAt']);

      const pgForkBackend = (): ForkBackend<Tx> => {
        const cipher = testCipher();
        const payloads = boundStore(new PgPayloadStore());
        const journal = boundStore(
          new AuditedJournalStore(
            new PayloadReferencingJournalStore(new SealedJournalStore(new PgJournalStore(), cipher), payloads),
            new PgAuditChainStore(),
          ),
        );
        const recorder = new SessionParticipantJournal(journal, new JournalService(payloads, cipher, 2_000));
        const failingRecorder: SessionParticipantRecorder<Tx> = {
          participantsChanged: (change, tx) => recorder.participantsChanged(change, tx),
          forked: async (orgId, sessionId, fork, tx) => {
            // Written first, so the rollback of the whole unit is what is tested.
            await recorder.forked(orgId, sessionId, fork, tx);
            throw new Error('journal unavailable');
          },
        };
        const WORKSPACE_ORG_ARGS = { head: 0, manifest: 0, revisions: 0, setMergeBase: 0, release: 0, get: 0, destroy: 0 };
        const workspaces = scopedStore(new PgWorkspaceRepository(), WORKSPACE_ORG_ARGS);
        const keys = new SessionWorkspaceKeys({ store: scopedStore(new PgWorkspaceKeyStore(), WORKSPACE_ORG_ARGS), cipher });
        return {
          store: boundStore(new PgHarnessSessionForkStore()),
          transaction: inPgDurableTransaction,
          asOrg: (orgId, work) => inOrg(orgId, work),
          journal,
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
            writers: scopedStore(new PgWorkspaceWriterStore(), WORKSPACE_ORG_ARGS),
          }),
          newId: newObjectId,
          seed: (conversation) =>
            inOrg(conversation.orgId, async () => {
              const { orgId, ownerId } = conversation;
              const session = await repo().createSession({
                orgId,
                userId: ownerId,
                initiator: ownerId,
                title: conversation.title,
                lastActivityAt: 1,
                status: 'Complete',
                modelInfo: { modelName: 'm' },
                projectId: newObjectId(),
                ...(conversation.agentKey !== undefined
                  ? { sessionType: 'agent' as const, agentKey: conversation.agentKey }
                  : { sessionType: 'chat' as const }),
                ...(conversation.durable === true ? { harnessVersion: 'durable-v1' as const } : {}),
              });
              if (conversation.shares.length > 0) {
                await repo().setSharing({ kind: session.sessionType ?? 'chat', ...(conversation.agentKey !== undefined ? { agentKey: conversation.agentKey } : {}) } as never, session._id, {
                  isShared: true,
                  sharedWith: conversation.shares.map(([userId, accessLevel]) => ({ userId, accessLevel })),
                });
              }
              for (const message of conversation.messages) {
                const citations = message.citations?.length
                  ? await repo().createCitations(message.citations.map((data) => ({ ...data })) as never)
                  : [];
                await repo().appendMessages(session._id, orgId, [
                  {
                    messageType: message.messageType,
                    content: message.content,
                    ...(message.senderId !== null ? { senderId: message.senderId } : {}),
                    citations: citations.map((citation) => ({ citationId: citation._id, relevanceScore: 0.5 })),
                    ...(message.extra ?? {}),
                    ...(message.label !== undefined ? { label: message.label } : {}),
                  } as unknown as NewMessage,
                ]);
              }
              return session._id;
            }),
          unshare: async (sessionId, userId) => {
            await stack.admin((c) =>
              c.query(`DELETE FROM conversation.chat_session_members WHERE session_id = $1 AND user_id = $2`, [
                sessionId,
                userId,
              ]),
            );
          },
          conversation: async (sessionId) => {
            const orgId = await orgOf(sessionId);
            if (orgId === null) return null;
            const row = await inOrg(orgId, () => repo().findHarnessSession(sessionId, orgId));
            if (row === null) return null;
            const { session } = row;
            return {
              ownerId: session.userId,
              isShared: session.isShared === true,
              shares: (session.sharedWith ?? []).length,
              sessionType: row.sessionType,
              agentKey: session.agentKey ?? null,
              title: session.title ?? '',
              projectId: session.projectId ?? null,
              durable: row.harnessVersion === 'durable-v1',
            };
          },
          messages: async (sessionId) => {
            const orgId = await orgOf(sessionId);
            if (orgId === null) return [];
            const rows = await inOrg(orgId, () =>
              repo().listMessages(sessionId, { orgId, withCitations: true, withLabels: true }),
            );
            return rows.map(({ dto }): StoredMessage => ({
              messageType: dto.messageType,
              content: dto.content,
              senderId: dto.senderId ?? null,
              foreignTurn: dto.foreignTurn === true,
              copied: dto.copied === true,
              // Read with citations, a link's `citationId` is the citation row (null when it is gone), as Mongo populates it.
              citations: ((dto.citations ?? []) as unknown as Array<{ citationId: Record<string, unknown> | null }>).map(
                (link) => ({
                  citationId: String((link.citationId?.['_id'] as string | number | undefined) ?? ''),
                  data: link.citationId === null ? null : (link.citationId as unknown as ForkCitationData),
                }),
              ),
              fields: Object.entries(dto)
                .filter(
                  ([field, value]) =>
                    value !== undefined && !NOT_COPIED.has(field) && !(Array.isArray(value) && value.length === 0),
                )
                .map(([field]) => field),
            }));
          },
          citationIds: async () =>
            new Set(
              (
                await stack.admin((c) => c.query<{ id: string }>(`SELECT id::text AS id FROM conversation.citations`))
              ).rows.map((row) => row.id),
            ),
        };
      };

      describe('the SS3 contract', () => {
        after(pgAuditLinksIdle);
        describeSessionForkContract('postgres', pgForkBackend);
      });

      const forks = scopedStore(new PgHarnessSessionForkStore());
      const abandoned = new PgAbandonedForks();

      const keyOf = (
        orgId: string,
        sourceSessionId: string,
        actorId: string,
        requestKey = 'req-1',
      ): ForkKey => ({
        orgId,
        sourceSessionId,
        actorId,
        requestKey,
      });

      /** A durable source conversation with a question and a cited answer from a run. */
      const sourceSession = async (
        orgId: string,
        owner: string,
        durable = true,
      ) =>
        inOrg(orgId, async () => {
          const s = await repo().createSession({
            orgId,
            userId: owner,
            initiator: owner,
            title: 'Source',
            lastActivityAt: 1,
            status: 'Complete',
            sessionType: 'chat',
            modelInfo: { modelKey: 'm', chatMode: 'quick' },
            ...(durable
              ? {
                  harnessVersion: 'durable-v1' as const,
                  harnessLedger: { seq: 2, digest: 'd' },
                }
              : {}),
          });
          const [citation] = await repo().createCitations([
            {
              content: 'cited',
              chunkIndex: 1,
              citationType: 'vectordb|document',
              metadata: {
                orgId,
                recordId: 'r-1',
                recordName: 'n',
                mimeType: 'm',
                origin: 'UPLOAD',
              },
            },
          ]);
          await repo().appendMessages(s._id, orgId, [
            { messageType: 'user_query', content: 'Q?' } as NewMessage,
            {
              messageType: 'bot_response',
              content: 'A.',
              runId: 'run-source',
              citations: [{ citationId: citation!._id, relevanceScore: 0.5 }],
              label: { complete: true, sources: [] },
            } as unknown as NewMessage,
          ]);
          return { sessionId: s._id, citationId: citation!._id };
        });

      const result = (sessionId: string, fromSessionId: string): ForkResult =>
        ({
          sessionId,
          fromSessionId,
          atSeq: 2,
          role: 'owner',
          counts: { messages: 2, stubs: 0 },
        }) as unknown as ForkResult;

      const commitOf = async (
        key: ForkKey,
        forkSessionId: string,
        durable = true,
      ): Promise<ForkCommit> => {
        const source = (await forks.source({
          orgId: key.orgId,
          sessionId: key.sourceSessionId,
          actorId: key.actorId,
        }))!;
        const messages: ForkedMessage[] = source.messages.map((m) => ({
          messageType: m.messageType as ForkedMessage['messageType'],
          content: m.content,
          citations: m.citations,
          fields: m.fields,
          foreignTurn: false,
        }));
        return {
          key,
          forkSessionId,
          source: {
            sessionType: source.sessionType,
            agentKey: source.agentKey,
            title: source.title,
            modelInfo: source.modelInfo,
            durable,
          },
          messages,
          result: result(forkSessionId, key.sourceSessionId),
          now: 99,
        };
      };

      it('begin chooses the fork session once: a repeat, or asks racing, read the first choice', async () => {
        const [org, owner, actor] = [hexOrg(), hexOrg(), hexOrg()];
        const { sessionId } = await sourceSession(org, owner);
        const key = keyOf(org, sessionId, actor);
        expect(await forks.find(key)).to.equal(null);
        const candidates = Array.from({ length: 6 }, () => hexOrg());
        const records = await Promise.all(
          candidates.map((candidate) => forks.begin(key, candidate, 5)),
        );
        const chosen = records[0]!.forkSessionId;
        expect(candidates).to.include(chosen);
        expect(records.map((r) => r.forkSessionId)).to.deep.equal(
          records.map(() => chosen),
        );
        expect(await forks.begin(key, hexOrg(), 6)).to.deep.equal({
          forkSessionId: chosen,
          result: null,
        });
        expect(await forks.find(key)).to.deep.equal({
          forkSessionId: chosen,
          result: null,
        });
        expect(
          await forks.find(keyOf(org, sessionId, actor, 'req-2')),
          'another request key',
        ).to.equal(null);
      });

      it('source reads the conversation with its participants, citations and labels; never another kind, org or a deleted one', async () => {
        const [org, owner] = [hexOrg(), hexOrg()];
        const { sessionId } = await sourceSession(org, owner);
        const source = await forks.source({
          orgId: org,
          sessionId,
          actorId: hexOrg(),
        });
        expect(source).to.deep.include({
          sessionType: 'chat',
          agentKey: null,
          title: 'Source',
          durable: true,
        });
        expect(source!.participants).to.deep.equal({
          ownerId: owner,
          isShared: false,
          shares: [],
        });
        expect(
          source!.messages.map((m) => [
            m.messageType,
            m.content,
            m.citations.length,
          ]),
        ).to.deep.equal([
          ['user_query', 'Q?', 0],
          ['bot_response', 'A.', 1],
        ]);
        expect(source!.messages[1]!.citations[0]).to.deep.include({
          relevanceScore: 0.5,
        });
        expect(source!.messages[1]!.citations[0]!.data).to.deep.include({
          content: 'cited',
          chunkIndex: 1,
        });
        expect(source!.messages[1]!.label).to.deep.equal({
          complete: true,
          sources: [],
        });
        expect(
          await forks.source({
            orgId: org,
            sessionId,
            actorId: owner,
            agentKey: 'ag',
          }),
        ).to.equal(null);
        expect(
          await forks.source({ orgId: hexOrg(), sessionId, actorId: owner }),
        ).to.equal(null);
        await inOrg(org, () =>
          repo().softDelete(sessionId, { orgId: org, userId: owner }),
        );
        expect(
          await forks.source({ orgId: org, sessionId, actorId: owner }),
        ).to.equal(null);
      });

      it("commit writes the forker's own copy: not shared, pinned with an empty ledger, new citations, copied turns naming no run", async () => {
        const [org, owner, actor] = [hexOrg(), hexOrg(), hexOrg()];
        const { sessionId, citationId } = await sourceSession(org, owner);
        const key = keyOf(org, sessionId, actor);
        const { forkSessionId } = await forks.begin(key, hexOrg(), 5);
        const commit = await commitOf(key, forkSessionId);
        await transactAs((tx) => forks.commit(commit, tx), org);

        expect(await forks.find(key)).to.deep.equal({
          forkSessionId,
          result: commit.result,
        });
        const fork = await inOrg(org, () =>
          repo().findHarnessSession(forkSessionId, org),
        );
        expect(fork).to.deep.include({
          sessionType: 'chat',
          nextSeq: 2,
          harnessVersion: 'durable-v1',
          harnessLedger: EMPTY_LEDGER,
        });
        expect(fork!.session).to.include({
          userId: actor,
          initiator: actor,
          title: 'Source',
          status: 'Complete',
          isShared: false,
          lastActivityAt: 99,
        });
        expect(fork!.session.sharedWith).to.deep.equal([]);
        const copied = (
          await inOrg(org, () =>
            repo().listMessages(forkSessionId, {
              orgId: org,
              withCitations: true,
            }),
          )
        ).map((r) => r.dto);
        expect(
          copied.map((m) => [
            m.seq,
            m.messageType,
            m.content,
            m.copied ?? false,
            m.runId ?? null,
          ]),
        ).to.deep.equal([
          [1, 'user_query', 'Q?', true, null],
          [2, 'bot_response', 'A.', true, null],
        ]);
        const copiedCitation =
          copied[1]!.citations![0]!.citationData ??
          (copied[1]!.citations![0]!.citationId as unknown as {
            _id: string;
            content: string;
          });
        expect((copiedCitation as { _id: string })._id).to.not.equal(
          citationId,
        );
        expect((copiedCitation as { content: string }).content).to.equal(
          'cited',
        );
        // The source keeps its own citation and run.
        const original = (
          await inOrg(org, () => repo().listMessages(sessionId, { orgId: org }))
        ).map((r) => r.dto);
        expect(original[1]).to.include({ runId: 'run-source' });
        expect(String(original[1]!.citations![0]!.citationId)).to.equal(
          citationId,
        );
      });

      it('a legacy source forks unpinned; a read through the conversation route detaches a stale copied run', async () => {
        const [org, owner, actor] = [hexOrg(), hexOrg(), hexOrg()];
        const { sessionId } = await sourceSession(org, owner, false);
        const key = keyOf(org, sessionId, actor);
        const { forkSessionId } = await forks.begin(key, hexOrg(), 5);
        await transactAs(
          async (tx) =>
            forks.commit(await commitOf(key, forkSessionId, false), tx),
          org,
        );
        expect(
          await inOrg(org, () => repo().findHarnessSession(forkSessionId, org)),
        ).to.not.have.property('harnessVersion');
        // FORKFIX: what a fork stores for a copied answer, as the copy reads it.
        const detached = detachCopiedRuns({
          messageType: 'bot_response',
          content: 'x',
          runId: 'run-elsewhere',
          parts: [{ type: 'run', runId: 'r' } as never],
        });
        expect(detached).to.not.have.property('runId');
        expect((detached as { copied?: boolean }).copied).to.equal(true);
      });

      it("a fork's read detaches a run id of another conversation on PostgreSQL (the run lookup is the execution store's)", async () => {
        const [org, fork] = [hexOrg(), hexOrg()];
        const was = process.env.DOC_STORE;
        process.env.DOC_STORE = 'postgres';
        try {
          const read = await inOrg(org, () =>
            withForeignRunsDetached(
              { _id: fork, metadata: { forkedFromConversationId: hexOrg() } },
              org,
              [
                {
                  messageType: 'bot_response' as const,
                  content: 'x',
                  runId: 'run-of-the-original',
                },
                { messageType: 'user_query' as const, content: 'q' },
              ],
            ),
          );
          expect(read[0]).to.not.have.property('runId');
          expect((read[0] as { copied?: boolean }).copied).to.equal(true);
          expect(read[1]).to.deep.equal({
            messageType: 'user_query',
            content: 'q',
          });
        } finally {
          if (was === undefined) delete process.env.DOC_STORE;
          else process.env.DOC_STORE = was;
        }
      });

      it('commits once: a second commit, or one racing it, is refused and writes nothing', async () => {
        const [org, owner, actor] = [hexOrg(), hexOrg(), hexOrg()];
        const { sessionId } = await sourceSession(org, owner);
        const key = keyOf(org, sessionId, actor);
        const { forkSessionId } = await forks.begin(key, hexOrg(), 5);
        const commit = await commitOf(key, forkSessionId);
        const outcomes = await Promise.allSettled(
          [1, 2, 3].map(() =>
            transactAs((tx) => forks.commit(commit, tx), org),
          ),
        );
        expect(outcomes.filter((o) => o.status === 'fulfilled')).to.have.length(
          1,
        );
        for (const o of outcomes.filter(
          (x): x is PromiseRejectedResult => x.status === 'rejected',
        )) {
          expect(o.reason).to.be.instanceOf(ForkAlreadyCommittedError);
        }
        const counts = await stack.admin(async (c) => ({
          sessions: Number(
            (
              await c.query(
                'SELECT count(*)::int AS n FROM conversation.chat_sessions WHERE org_id = $1 AND id = $2',
                [org, forkSessionId],
              )
            ).rows[0].n,
          ),
          citations: Number(
            (
              await c.query(
                'SELECT count(*)::int AS n FROM conversation.citations WHERE org_id = $1',
                [org],
              )
            ).rows[0].n,
          ),
        }));
        expect(counts).to.deep.equal({ sessions: 1, citations: 2 });
      });

      it('an aborted commit leaves neither the copy nor the mark; the next commit goes through', async () => {
        const [org, owner, actor] = [hexOrg(), hexOrg(), hexOrg()];
        const { sessionId } = await sourceSession(org, owner);
        const key = keyOf(org, sessionId, actor);
        const { forkSessionId } = await forks.begin(key, hexOrg(), 5);
        const commit = await commitOf(key, forkSessionId);
        const error = await transactAs(async (tx) => {
          await forks.commit(commit, tx);
          throw new Error('artifact copy failed');
        }, org).catch((e: Error) => e);
        expect((error as Error).message).to.equal('artifact copy failed');
        expect(await forks.find(key)).to.deep.equal({
          forkSessionId,
          result: null,
        });
        expect(
          await inOrg(org, () => repo().findHarnessSession(forkSessionId, org)),
        ).to.equal(null);
        await transactAs((tx) => forks.commit(commit, tx), org);
        expect((await forks.find(key))!.result).to.deep.equal(commit.result);
      });

      it('another organisation sees no fork record and cannot commit one', async () => {
        const [org, other, owner, actor] = [
          hexOrg(),
          hexOrg(),
          hexOrg(),
          hexOrg(),
        ];
        const { sessionId } = await sourceSession(org, owner);
        const key = keyOf(org, sessionId, actor);
        const { forkSessionId } = await forks.begin(key, hexOrg(), 5);
        const visible = await stack.admin(async (c) =>
          Number(
            (
              await c.query(
                'SELECT count(*)::int AS n FROM harness.session_forks WHERE org_id = $1',
                [org],
              )
            ).rows[0].n,
          ),
        );
        expect(visible).to.equal(1);
        expect(await forks.find({ ...key, orgId: other })).to.equal(null);
        const commit = await commitOf(key, forkSessionId);
        const refused = await transactAs(
          (tx) =>
            forks.commit({ ...commit, key: { ...key, orgId: other } }, tx),
          other,
        ).catch((e: Error) => e);
        expect(refused).to.be.instanceOf(ForkAlreadyCommittedError);
        expect(await forks.find(key)).to.deep.equal({
          forkSessionId,
          result: null,
        });
      });

      it('CLEANUP-G4: the sweep lists uncommitted records of every org begun before the cutoff, and drops only those', async () => {
        const [orgA, orgB, owner, actor] = [
          hexOrg(),
          hexOrg(),
          hexOrg(),
          hexOrg(),
        ];
        const a = await sourceSession(orgA, owner);
        const b = await sourceSession(orgB, owner);
        const old = await forks.begin(
          keyOf(orgA, a.sessionId, actor),
          hexOrg(),
          10,
        );
        const otherOrg = await forks.begin(
          keyOf(orgB, b.sessionId, actor),
          hexOrg(),
          20,
        );
        const recent = await forks.begin(
          keyOf(orgA, a.sessionId, actor, 'late'),
          hexOrg(),
          500,
        );
        const committedKey = keyOf(orgA, a.sessionId, actor, 'done');
        const committed = await forks.begin(committedKey, hexOrg(), 15);
        await transactAs(
          async (tx) =>
            forks.commit(
              await commitOf(committedKey, committed.forkSessionId),
              tx,
            ),
          orgA,
        );

        const listed = await abandoned.list(100, 10);
        expect(listed.map((f) => [f.orgId, f.forkSessionId])).to.deep.equal([
          [orgA, old.forkSessionId],
          [orgB, otherOrg.forkSessionId],
        ]);
        expect(await abandoned.list(100, 1)).to.have.length(1);
        const [first, second] = listed;
        const drop = (orgId: string, forks: AbandonedFork[], before: number) =>
          transactAs((tx) => abandoned.dropMany(orgId, forks, before, tx), orgId);
        // An aborted drop keeps the record; a drop past the record's begin time, or of a committed one, refuses.
        await transactAs(async (tx) => {
          expect(await abandoned.dropMany(orgA, [first!], 100, tx)).to.deep.equal([first]);
          throw new Error('abort');
        }, orgA).catch(() => undefined);
        expect(await abandoned.list(100, 10)).to.have.length(2);
        expect(await drop(orgA, [first!], 5)).to.deep.equal([]);
        // One call for the org's forks drops those still abandoned and answers only them.
        const committedAsListed = { ...first!, forkSessionId: committed.forkSessionId };
        expect(await drop(orgA, [committedAsListed, first!], 100)).to.deep.equal([first]);
        expect(await drop(orgA, [first!], 100)).to.deep.equal([]);
        // Another org's fork is not this org's to drop.
        expect(await drop(orgA, [second!], 100)).to.deep.equal([]);
        expect(await drop(orgB, [second!], 100)).to.deep.equal([second]);
        expect(await abandoned.list(100, 10)).to.deep.equal([]);
        expect(
          await forks.find(keyOf(orgA, a.sessionId, actor, 'late')),
        ).to.deep.equal({ forkSessionId: recent.forkSessionId, result: null });
      });
    },
  );
}
