/**
 * The conversation module's erasure, retention, backfill and read ports on PostgreSQL (WP-114), through main's
 * conversation and notification repositories (Alembic head, row-level security, PgBouncer in transaction mode): the
 * contracts the in-memory and MongoDB adapters meet, seeded through the conversation repository, plus the
 * attachment carriers, the session roles, the session fields and the finished-run reader.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… mocha … tests/modules/agent-harness/conversation-erasure.pg.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgConversationRepository } from '../../../src/libs/repositories/conversation/pg';
import {
  getConversationRepository,
  setConversationRepository,
} from '../../../src/libs/repositories/conversation';
import { PgNotificationRepository } from '../../../src/libs/repositories/notification/pg';
import {
  getNotificationRepository,
  setNotificationRepository,
} from '../../../src/libs/repositories/notification';
import { PgHarnessConversationEraser } from '../../../src/libs/repositories/agent-harness/conversation-eraser/pg';
import { PgHarnessConversationRetention } from '../../../src/libs/repositories/agent-harness/conversation-retention/pg';
import { PgHarnessConversationBackfillSource } from '../../../src/libs/repositories/agent-harness/conversation-backfill-source/pg';
import { PgHarnessAttachmentCarriers } from '../../../src/libs/repositories/agent-harness/attachment-carriers/pg';
import { PgHarnessSessionRoles } from '../../../src/libs/repositories/agent-harness/session-roles/pg';
import { PgHarnessConversationReader } from '../../../src/libs/repositories/agent-harness/conversation-reader/pg';
import {
  pgHarnessSessionView,
  pgMarkErasureRequested,
  pgPinForkExecutor,
} from '../../../src/libs/repositories/agent-harness/session-fields/pg';
import type { Tx } from '../../../src/libs/repositories/uow';
import type { NewMessage } from '../../../src/libs/repositories/conversation';
import { EMPTY_LEDGER } from '../../../src/modules/agent-harness/domain/session-import';
import {
  ConversationRemains,
  describeConversationEraserContract,
  describeConversationRetentionContract,
  describeConversationStateLookupContract,
} from './support/conversation-erasure-contract';
import {
  describeBackfillSourceContract,
  hex,
} from './support/erasure-backfill-contract';
import {
  CONVERSATION_SENTINEL,
  SeedConversation,
  citationRecordPrefix,
} from './support/conversation-seeds';
import { FakeChatAttachments } from './support/chat-attachments';
import {
  PgStack,
  boundStore,
  inOrg,
  pgBins,
  pgSuite,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

const repo = () => getConversationRepository();

for (const bin of pgBins()) {
  pgSuite(
    `agent harness conversation erasure and reads on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      /** Runs the seeded sessions have, as the execution store would count them. */
      const seededRuns = new Map<string, number>();

      before(async () => {
        stack = await startHarnessPg(bin, 'harness_conversation_erasure');
        setConversationRepository(new PgConversationRepository());
        setNotificationRepository(new PgNotificationRepository());
      });
      after(async () => {
        setConversationRepository();
        setNotificationRepository();
        await stack?.stop();
      });

      const empty = async (): Promise<void> => {
        seededRuns.clear();
        await stack.admin((c) =>
          c.query(
            'TRUNCATE conversation.chat_sessions, conversation.citations, platform.notifications CASCADE',
          ),
        );
      };

      /** Conversations as the conversation module writes them, through its repository; deletion fields as a delete leaves them. */
      const seed = async (conversations: SeedConversation[]): Promise<void> => {
        for (const c of conversations) {
          const owner = c.ownerId ?? hex();
          await inOrg(c.orgId, async () => {
            await repo().createSession({
              id: c.sessionId,
              orgId: c.orgId,
              userId: owner,
              initiator: owner,
              title: CONVERSATION_SENTINEL,
              lastActivityAt: c.lastActivityAt ?? Date.now(),
              status: 'Complete',
              sessionType: 'chat',
            });
            for (let m = 0; m < (c.messages ?? 0); m += 1) {
              const citations = await repo().createCitations(
                Array.from({ length: c.citations ?? 0 }, (_unused, i) => ({
                  content: `${CONVERSATION_SENTINEL} ${String(i)}`,
                  chunkIndex: i,
                  citationType: 'vectordb|document',
                  metadata: {
                    orgId: c.orgId,
                    mimeType: 'text/plain',
                    recordId: `${citationRecordPrefix(c.sessionId)}${String(i)}`,
                    recordName: 'r',
                    origin: 'UPLOAD',
                    extension: 'txt',
                  },
                })),
              );
              await repo().appendMessages(c.sessionId, c.orgId, [
                {
                  messageType: 'bot_response',
                  content: CONVERSATION_SENTINEL,
                  citations: citations.map((x) => ({ citationId: x._id })),
                } as NewMessage,
              ]);
            }
            for (const { recordId, senderId } of c.attachments ?? []) {
              await repo().appendMessages(c.sessionId, c.orgId, [
                {
                  messageType: 'user_query',
                  content: CONVERSATION_SENTINEL,
                  attachments: [
                    {
                      recordId,
                      recordName: `${CONVERSATION_SENTINEL}.txt`,
                      mimeType: 'text/plain',
                    },
                  ],
                  ...(senderId !== undefined ? { senderId } : {}),
                } as NewMessage,
              ]);
            }
          });
          const requestedAt = c.requestedAt ?? (c.marked ? Date.now() : null);
          await stack.admin((client) =>
            client.query(
              'UPDATE conversation.chat_sessions SET is_deleted = $3, deleted_by = $4, erasure_requested_at = $5 WHERE org_id = $1 AND id = $2',
              [
                c.orgId,
                c.sessionId,
                c.deleted,
                c.deletedBy ?? null,
                requestedAt,
              ],
            ),
          );
          if (c.runs) seededRuns.set(`${c.orgId}:${c.sessionId}`, c.runs);
        }
      };

      const remains = (
        orgId: string,
        sessionId: string,
      ): Promise<ConversationRemains> =>
        stack.admin(async (c) => {
          const count = async (text: string, values: unknown[]) =>
            Number((await c.query<{ n: string | number }>(text, values)).rows[0]!.n);
          return {
            sessions: await count(
              'SELECT count(*)::int AS n FROM conversation.chat_sessions WHERE org_id = $1 AND id = $2',
              [orgId, sessionId],
            ),
            messages: await count(
              'SELECT count(*)::int AS n FROM conversation.chat_messages WHERE org_id = $1 AND session_id = $2',
              [orgId, sessionId],
            ),
            citations: await count(
              'SELECT count(*)::int AS n FROM conversation.citations WHERE org_id = $1 AND record_id LIKE $2',
              [orgId, `${citationRecordPrefix(sessionId)}%`],
            ),
            legacyCopies: 0,
          };
        });

      const removedIn = (orgId: string, sessionId: string): Promise<string[]> =>
        stack.admin(async (c) => {
          const { rows } = await c.query<{
            attachments: Array<{ recordId: string; removed?: boolean }>;
          }>(
            'SELECT attachments FROM conversation.chat_messages WHERE org_id = $1 AND session_id = $2',
            [orgId, sessionId],
          );
          return rows
            .flatMap((row) => row.attachments)
            .filter((a) => a.removed === true)
            .map((a) => a.recordId)
            .sort();
        });

      describeConversationStateLookupContract(
        'PgHarnessConversationEraser',
        async () => {
          await empty();
          return {
            lookup: boundStore(new PgHarnessConversationEraser(new FakeChatAttachments())),
            seed,
          };
        },
      );

      describeConversationEraserContract(
        'PgHarnessConversationEraser',
        async () => {
          await empty();
          const attachments = new FakeChatAttachments();
          return {
            eraser: boundStore(new PgHarnessConversationEraser(attachments)),
            seed,
            remains,
            attachments,
            removedIn,
            legacy: false,
          };
        },
      );

      describeConversationRetentionContract<Tx>(
        'PgHarnessConversationRetention',
        async () => {
          await empty();
          return {
            retention: boundStore(new PgHarnessConversationRetention()),
            seed,
            inTransaction: (work, orgId) => transactAs(work, orgId),
          };
        },
      );

      describeBackfillSourceContract('postgres', async () => {
        await empty();
        return {
          source: boundStore(new PgHarnessConversationBackfillSource(
            async (orgId, ids) =>
              new Map(
                ids.map((id) => [id, seededRuns.get(`${orgId}:${id}`) ?? 0]),
              ),
          )),
          seed,
          legacy: false,
        };
      });

      describe('beside the contracts', () => {
        beforeEach(empty);

        it("erasure takes the conversation's inbox notices with it, and only its own org's", async () => {
          const [org, other, user, session] = [hex(), hex(), hex(), hex()];
          await seed([
            { orgId: org, sessionId: session, deleted: true, messages: 1 },
          ]);
          const notice = (orgId: string, conversationId: string) =>
            inOrg(orgId, () =>
              getNotificationRepository().insert({
                orgId,
                assignedTo: user,
                type: 'schedule',
                status: 'unread',
                isDeleted: false,
                payload: { conversationId },
              }),
            );
          await notice(org, session);
          await notice(org, hex());
          await notice(other, session);
          await boundStore(new PgHarnessConversationEraser(
            new FakeChatAttachments(),
          )).erase(org, session);
          const left = await stack.admin(
            async (c) =>
              (
                await c.query<{ org_id: string; cid: string }>(
                  "SELECT org_id, payload->>'conversationId' AS cid FROM platform.notifications ORDER BY org_id",
                )
              ).rows,
          );
          expect(left).to.have.length(2);
          expect(
            left.filter((row) => row.org_id === org && row.cid === session),
          ).to.deep.equal([]);
        });

        it("erasure refuses a caller bound to another org rather than reaching that org's rows", async () => {
          const [org, other, session] = [hex(), hex(), hex()];
          await seed([
            { orgId: org, sessionId: session, deleted: true, messages: 1 },
          ]);
          let refused = false;
          await inOrg(other, () =>
            new PgHarnessConversationEraser(new FakeChatAttachments()).erase(
              org,
              session,
            ),
          ).catch(() => {
            refused = true;
          });
          expect(refused).to.equal(true);
          expect(await remains(org, session)).to.deep.include({
            sessions: 1,
            messages: 1,
          });
        });

        it("names the conversation of a message that carries each upload; another org's message never counts", async () => {
          const [org, other, session] = [hex(), hex(), hex()];
          await seed([
            {
              orgId: org,
              sessionId: session,
              deleted: false,
              attachments: [
                { recordId: 'a' },
                { recordId: 'b' },
                { recordId: 'b' },
              ],
            },
            {
              orgId: other,
              sessionId: hex(),
              deleted: false,
              attachments: [{ recordId: 'c' }],
            },
          ]);
          const carriers = boundStore(new PgHarnessAttachmentCarriers());
          const carried = await carriers.carriedBy(org, ['a', 'b', 'c', 'd']);
          expect([...carried.entries()].sort()).to.deep.equal([
            ['a', session],
            ['b', session],
          ]);
          expect((await carriers.carriedBy('not-an-id', ['a'])).size).to.equal(
            0,
          );
        });

        it('roles are read from the conversation: owner, shares, kind, deletion and org', async () => {
          const [org, owner, reader, writer, session] = [
            hex(),
            hex(),
            hex(),
            hex(),
            hex(),
          ];
          await seed([
            { orgId: org, sessionId: session, deleted: false, ownerId: owner },
          ]);
          const roles = boundStore(new PgHarnessSessionRoles());
          const scope = (
            actorId: string,
            over: Record<string, unknown> = {},
          ) => ({ orgId: org, sessionId: session, actorId, ...over });
          expect(
            await roles.isShared({ orgId: org, sessionId: session }),
          ).to.equal(false);
          await inOrg(org, () =>
            repo().setSharing({ kind: 'chat' }, session, {
              isShared: true,
              sharedWith: [
                { userId: reader, accessLevel: 'read' },
                { userId: writer, accessLevel: 'write' },
              ],
            }),
          );
          expect(await roles.roleOf(scope(owner))).to.equal('owner');
          const asReader = await roles.roleOf(scope(reader));
          const asWriter = await roles.roleOf(scope(writer));
          expect(asReader).to.not.equal(null);
          expect(asWriter).to.not.equal(asReader);
          expect(
            await roles.roleOf(scope(owner, { agentKey: 'ag' })),
            'not under another kind',
          ).to.equal(null);
          expect(
            await roles.roleOf({
              orgId: hex(),
              sessionId: session,
              actorId: owner,
            }),
            'another org',
          ).to.equal(null);
          expect(
            await roles.isShared({ orgId: org, sessionId: session }),
          ).to.equal(true);
          expect(
            await roles.isShared({ orgId: org, sessionId: hex() }),
            'unknown reads as shared (fail closed)',
          ).to.equal(true);
          await stack.admin((c) =>
            c.query(
              'UPDATE conversation.chat_sessions SET is_deleted = true WHERE id = $1',
              [session],
            ),
          );
          expect(await roles.roleOf(scope(owner))).to.equal(null);
        });

        it("the session fields: a fork's executor pin, the erasure marker, and the view a read adds", async () => {
          const [org, session, fork] = [hex(), hex(), hex()];
          await seed([
            { orgId: org, sessionId: session, deleted: false },
            { orgId: org, sessionId: fork, deleted: false },
          ]);
          await inOrg(org, async () => {
            expect(await pgHarnessSessionView(session, org)).to.deep.equal({
              harnessVersion: undefined,
              metadata: undefined,
            });
            await transactAs(async (tx) => {
              await pgPinForkExecutor(fork, org, 'legacy', tx);
              await pgPinForkExecutor(session, org, 'durable-v1', tx);
              await pgMarkErasureRequested(fork, org, 4242, tx);
            }, org);
            expect(
              (await pgHarnessSessionView(session, org)).harnessVersion,
            ).to.equal('durable-v1');
            expect(
              (await pgHarnessSessionView(fork, org)).harnessVersion,
            ).to.equal(undefined);
            expect(
              await repo().findHarnessSession(session, org),
            ).to.deep.include({ harnessLedger: EMPTY_LEDGER });
            expect(
              (await repo().findHarnessSession(fork, org))!.erasureRequestedAt,
            ).to.equal(4242);
          });
        });

        it("a finished run's conversation is the session with its newest answer, citations populated", async () => {
          const [org, owner, session] = [hex(), hex(), hex()];
          await seed([
            {
              orgId: org,
              sessionId: session,
              deleted: false,
              ownerId: owner,
              messages: 2,
              citations: 1,
            },
          ]);
          await inOrg(org, () =>
            repo().appendMessages(session, org, [
              {
                messageType: 'user_query',
                content: 'later question',
              } as NewMessage,
            ]),
          );
          const reader = boundStore(new PgHarnessConversationReader());
          const finished = await inOrg(org, () =>
            reader.finishedConversation({
              orgId: org,
              sessionId: session,
              actorId: owner,
            }),
          );
          expect(finished).to.include({ title: CONVERSATION_SENTINEL });
          const messages = (
            finished as { messages: Array<Record<string, unknown>> }
          ).messages;
          expect(messages.map((m) => m.messageType)).to.include('bot_response');
          await stack.admin((c) =>
            c.query(
              'UPDATE conversation.chat_sessions SET is_deleted = true WHERE id = $1',
              [session],
            ),
          );
          expect(
            await inOrg(org, () =>
              reader.finishedConversation({
                orgId: org,
                sessionId: session,
                actorId: owner,
              }),
            ),
          ).to.equal(null);
        });
      });
    },
  );
}
