/**
 * What only PostgreSQL has to prove for the conversation stores: no row crosses an organisation under concurrency through
 * PgBouncer, the row policies hold, the content search is scoped to the organisation, and the plans use the intended indexes.
 */
import 'reflect-metadata';
import { EventEmitter } from 'node:events';
import { expect } from 'chai';
import { orgContextStorage } from '../../src/libs/context/orgContext';
import { attachUpstreamAbort } from '../../src/modules/enterprise_search/utils/stream-lifecycle';
import { PermissionDenied } from '../../src/libs/db';
import { InternalServerError } from '../../src/libs/errors/http.errors';
import { PgConversationRepository } from '../../src/libs/repositories/conversation/pg';
import type { NewMessage } from '../../src/libs/repositories/conversation';
import { PgSemanticSearchRepository } from '../../src/libs/repositories/semantic-search/pg';
import {
  OrgLedger,
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  newId,
  scoped,
  startPgStack,
} from './pg-store-harness';

const CHAT = { kind: 'chat' } as const;
const ORG_ARGS = {
  findIdsByContent: 0,
  listArtifactMarkerContents: 1,
  updateUserQueryContent: 1,
  listVisibleSummaries: 0,
  appendMessages: 1,
  replaceMessage: 1,
  deleteMessages: 1,
  appendFeedback: 1,
  findMessage: 1,
  countMessages: 1,
  getCitations: 1,
};
const bot = (content: string, extra: Partial<NewMessage> = {}): NewMessage => ({
  messageType: 'bot_response',
  content,
  ...extra,
});
const user = (
  content: string,
  extra: Partial<NewMessage> = {},
): NewMessage => ({ messageType: 'user_query', content, ...extra });
const DETAILS = {
  tools: [{ toolName: 't', toolResult: { secret: 'TOOL-SECRET' } }],
  reasoning: [{ content: 'REASONING-SECRET' }],
  parts: [{ type: 'text' as const, content: 'PART-SECRET' }],
  referenceData: [{ name: 'REFERENCE-SECRET' }],
};

const suite = haveStack ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL conversation checks (${bin})`, function () {
    this.timeout(240_000);
    let stack: PgStack;
    const ledger = new OrgLedger();
    const repo = scoped(new PgConversationRepository(), ledger, ORG_ARGS);

    before(async () => {
      stack = await startPgStack(bin, 'conversation_checks', {
        pool: 4,
        clientPool: 16,
      });
    });
    after(async () => {
      await stack?.stop();
    });

    const open = (
      org: string,
      userId: string,
      over: Record<string, unknown> = {},
      first: NewMessage = user('hello'),
    ) =>
      repo.openSession(
        {
          orgId: org,
          userId,
          initiator: userId,
          title: 'chat',
          lastActivityAt: Date.now(),
          status: 'Complete',
          sessionType: 'chat',
          ...over,
        } as never,
        first,
      );

    describe('a client that leaves mid-answer', () => {
      it('the partial answer is saved in the request’s org when the detached-run cap fires, though the disconnect is emitted outside any request context', async () => {
        const org = newId(),
          me = newId();
        const s = await open(org, me);
        const res = Object.assign(new EventEmitter(), {
          writableEnded: false,
          writableFinished: false,
        });
        const raw = new PgConversationRepository(); // no scoping of its own: only the bound listener supplies the org
        let saved!: Promise<unknown>;
        const capped = new Promise<void>((resolve) => {
          // a real (short) cap, not a fake clock: the context must travel through a real timer
          inOrg(
            org,
            () =>
              attachUpstreamAbort(
                res,
                'req-1',
                () => {
                  saved = raw.appendMessages(s._id, org, [
                    bot('partial answer', { status: 'stopped' }),
                  ]);
                  resolve();
                },
                50,
              ),
            me,
          );
        });
        orgContextStorage.exit(() => res.emit('close')); // the emitter's context is another request's, or none
        await capped;
        await saved;
        const rows = await stack.admin(
          async (c) =>
            (
              await c.query(
                "SELECT content, status FROM conversation.chat_messages WHERE org_id = $1 AND session_id = $2 AND message_type = 'bot_response'",
                [org, s._id],
              )
            ).rows,
        );
        expect(rows).to.deep.equal([
          { content: 'partial answer', status: 'stopped' },
        ]);
      });
    });

    describe('guards that only a test can see', () => {
      const asked: string[] = [];
      const spy = async <T>(fn: () => Promise<T>): Promise<T> => {
        const connect = stack.pool.connect.bind(stack.pool) as () => Promise<{
          query: (...a: unknown[]) => Promise<unknown>;
        }>;
        (stack.pool as unknown as { connect: unknown }).connect = async () => {
          const c = await connect();
          const q = c.query.bind(c);
          c.query = (...a: unknown[]) => {
            asked.push(
              typeof a[0] === 'string'
                ? a[0]
                : String((a[0] as { text?: string }).text),
            );
            return q(...a);
          };
          return c;
        };
        try {
          return await fn();
        } finally {
          (stack.pool as unknown as { connect: unknown }).connect = connect;
        }
      };

      it('a call for another org than the unit runs as throws before it touches a table, whatever row policies would have said', async () => {
        const a = newId(),
          b = newId(),
          me = newId();
        const s = await open(b, me);
        asked.length = 0;
        const calls: Array<[string, () => Promise<unknown>]> = [
          [
            'listMessages',
            () =>
              new PgConversationRepository().listMessages(s._id, { orgId: b }),
          ],
          [
            'countMessages',
            () => new PgConversationRepository().countMessages(s._id, b),
          ],
          [
            'appendMessages',
            () =>
              new PgConversationRepository().appendMessages(s._id, b, [
                user('x'),
              ]),
          ],
          [
            'getCitations',
            () => new PgConversationRepository().getCitations([s._id], b),
          ],
          [
            'search findVisible',
            () => new PgSemanticSearchRepository().findVisible(b, me, s._id),
          ],
        ];
        for (const [name, call] of calls) {
          const e = (await spy(() => inOrg(a, call)).catch(
            (x: unknown) => x,
          )) as Error;
          expect(e, name).to.be.instanceOf(InternalServerError);
          expect(e.message, name).to.match(/organisation of the call/);
        }
        expect(
          asked.filter((q) => /pipeshub:(conversation|search)\./.test(q)),
          'no statement on the tables',
        ).to.deep.equal([]);
      });
    });

    describe('under concurrency through PgBouncer', () => {
      it('no row crosses an organisation, seq numbers stay dense, and no role or org leaks into the pooled connections', async () => {
        const orgs = Array.from({ length: 6 }, () => newId());
        const users = new Map(
          orgs.map((o) => [o, [newId(), newId(), newId()]]),
        );
        const sessions = new Map<string, string[]>();
        for (const o of orgs) {
          const [u1, u2] = users.get(o)!;
          const list: string[] = [];
          for (const owner of [u1!, u2!]) {
            const s = await open(o, owner);
            await repo.setSharing(CHAT, s._id, {
              isShared: true,
              sharedWith: [{ userId: users.get(o)![2]!, accessLevel: 'write' }],
            });
            list.push(s._id);
          }
          sessions.set(o, list);
        }
        const written = new Map<string, number>(orgs.map((o) => [o, 0]));
        const foreign: string[] = [];
        const task = async (i: number): Promise<void> => {
          const o = orgs[i % orgs.length]!;
          const [u1, u2, u3] = users.get(o)!;
          const sid = sessions.get(o)![i % 2]!;
          const author = [u1, u2, u3][i % 3]!;
          if (i % 3 === 2 || (i % 2 === 0 ? u1 : u2) === author) {
            await repo.appendMessages(sid, o, [
              user(`q${i}`),
              bot(`a${i}`, DETAILS),
            ]);
            written.set(o, (written.get(o) ?? 0) + 2);
          }
          const mine = await repo.listSessions({
            orgId: o,
            userId: author,
            scope: CHAT,
            owned: true,
            shared: true,
            archived: 'no',
            sort: { lastActivityAt: -1, _id: -1 },
            skip: 0,
            limit: 50,
          });
          for (const r of mine.rows)
            if (!sessions.get(o)!.includes(r._id)) foreign.push(r._id);
          const msgs = await repo.listMessages(sid, { orgId: o });
          if (msgs.some((m) => m.dto.orgId !== o || m.dto.sessionId !== sid))
            foreign.push(sid);
          const lease = await repo
            .reserveRun(CHAT, sid, { orgId: o, userId: author }, 2000)
            .catch(() => null);
          if (lease) await repo.releaseRun(lease);
        };
        let next = 0;
        await Promise.all(
          Array.from({ length: 24 }, async () => {
            for (;;) {
              const i = next++;
              if (i >= 240) return;
              await task(i);
            }
          }),
        );
        expect(foreign).to.deep.equal([]);
        const check = await stack.admin(async (c) => {
          const dense = (
            await c.query(
              `SELECT s.org_id, s.id, s.next_seq, count(m.*)::int AS n, count(DISTINCT m.seq)::int AS d, COALESCE(max(m.seq), 0)::int AS top
              FROM conversation.chat_sessions s LEFT JOIN conversation.chat_messages m ON m.org_id = s.org_id AND m.session_id = s.id
             WHERE s.org_id = ANY($1::text[]) GROUP BY s.org_id, s.id, s.next_seq`,
              [orgs],
            )
          ).rows;
          const crossed = (
            await c.query(
              `SELECT count(*)::int AS n FROM conversation.chat_messages m
             WHERE m.org_id = ANY($1::text[]) AND NOT EXISTS (SELECT 1 FROM conversation.chat_sessions s WHERE s.org_id = m.org_id AND s.id = m.session_id)`,
              [orgs],
            )
          ).rows[0].n;
          return { dense, crossed };
        });
        expect(check.crossed).to.equal(0);
        for (const r of check.dense) {
          expect(r.n, `${r.id} has duplicate seq`).to.equal(r.d);
          expect(r.top, `${r.id} seq is not dense`).to.equal(r.n);
          expect(r.next_seq).to.equal(r.n);
        }
        for (const o of orgs)
          expect(
            check.dense
              .filter((r: { org_id: string }) => r.org_id === o)
              .reduce((n: number, r: { n: number }) => n + r.n, 0),
          ).to.equal(written.get(o)! + 2);
        // a connection from the pool carries nothing of the last unit
        const probe = await stack.pool.query(
          "SELECT current_setting('role') AS role, current_setting('app.org_id', true) AS org",
        );
        expect(probe.rows[0].role).to.equal('none');
        expect(['', null]).to.include(probe.rows[0].org);
      });

      it('of racing writers on one write-shared chat each gets its own seq block, and the run lease has one winner', async () => {
        const org = newId(),
          a = newId(),
          b = newId();
        const s = await open(org, a);
        await repo.setSharing(CHAT, s._id, {
          isShared: true,
          sharedWith: [{ userId: b, accessLevel: 'write' }],
        });
        await Promise.all(
          Array.from({ length: 30 }, (_, i) =>
            repo.appendMessages(s._id, org, [user(`q${i}`), bot(`a${i}`)]),
          ),
        );
        const seqs = (await repo.listMessages(s._id, { orgId: org })).map(
          (r) => r.dto.seq,
        );
        expect(seqs).to.deep.equal(Array.from({ length: 61 }, (_, i) => i + 1));
        const racers = await Promise.allSettled(
          Array.from({ length: 6 }, () =>
            repo.reserveRun(CHAT, s._id, { orgId: org, userId: a }, 150),
          ),
        );
        expect(
          racers.filter((r) => r.status === 'fulfilled' && r.value),
        ).to.have.length(1);
      });
    });

    describe('row-level security', () => {
      const TABLES = [
        'conversation.chat_sessions',
        'conversation.chat_session_members',
        'conversation.chat_messages',
        'conversation.citations',
        'conversation.semantic_searches',
        'conversation.semantic_search_members',
      ];

      it('shows an organisation only its own rows in every table, and nothing before the organisation is known', async () => {
        const a = newId(),
          b = newId(),
          u = newId(),
          w = newId();
        const search = scoped(new PgSemanticSearchRepository(), ledger, {
          findVisible: 0,
          listVisible: 0,
        });
        for (const org of [a, b]) {
          const s = await open(org, u);
          await repo.setSharing(CHAT, s._id, {
            isShared: true,
            sharedWith: [{ userId: w, accessLevel: 'read' }],
          });
          await repo.createCitations([
            {
              content: 'c',
              chunkIndex: 0,
              citationType: 't',
              metadata: {
                orgId: org,
                mimeType: 'm',
                recordId: 'r',
                recordName: 'n',
                origin: 'o',
              } as never,
            },
          ]);
          const m = await search.create({
            orgId: org,
            userId: u,
            query: 'q',
            limit: 1,
            records: new Map(),
          });
          await search.setSharing(m._id, {
            isShared: true,
            sharedWith: [{ userId: w, accessLevel: 'read' }],
          });
        }
        const count = async (org: string | undefined) =>
          Object.fromEntries(
            await Promise.all(
              TABLES.map(async (t) => {
                const run = () =>
                  stack.rt.withUow({ preOrg: org === undefined }, async (tx) =>
                    Number(
                      (
                        await tx.query<{ n: number }>(
                          `/* pipeshub:test.count */ SELECT count(*)::int AS n FROM ${t} WHERE true`,
                        )
                      ).rows[0]?.n,
                    ),
                  );
                return [t, await (org ? inOrg(org, run) : run())] as const;
              }),
            ),
          );
        const seenByA = await count(a);
        for (const t of TABLES) expect(seenByA[t], t).to.equal(1);
        expect(
          Object.values(await count(undefined)).every((n) => n === 0),
        ).to.equal(true);
      });

      it('refuses a write that names another organisation', async () => {
        const a = newId(),
          b = newId();
        const e = await inOrg(a, () =>
          stack.rt.withUow({ write: true }, (tx) =>
            tx.query(
              `/* pipeshub:test.cross */ INSERT INTO conversation.chat_sessions (org_id, id, user_id, initiator, last_activity_at, created_at, updated_at)
                    VALUES ($1, $2, $3, $3, now(), now(), now())`,
              [b, newId(), newId()],
            ),
          ),
        ).catch((x) => x);
        expect(e).to.be.instanceOf(PermissionDenied);
      });

      it('the content search answers only from the caller’s org, and finds a term in any message of the chat', async () => {
        const a = newId(),
          b = newId(),
          v = newId();
        const sb = await open(b, v, {}, user('needle text'));
        await repo.appendMessages(sb._id, b, [bot('another Needle here')]);
        expect((await repo.findIdsByContent(b, 'needle')).ids).to.deep.equal([
          sb._id,
        ]);
        expect((await repo.findIdsByContent(a, 'needle')).ids).to.deep.equal(
          [],
        );
      });

      it('treats LIKE metacharacters in the search as text, and reports a cut-short answer', async () => {
        const org = newId(),
          me = newId();
        await open(org, me, {}, user('100 percent'));
        const wild = await open(org, me, {}, user('100% sure_thing'));
        expect((await repo.findIdsByContent(org, '100%')).ids).to.deep.equal([
          wild._id,
        ]);
        expect((await repo.findIdsByContent(org, 'sure_t')).ids).to.deep.equal([
          wild._id,
        ]);
        const two = await repo.findIdsByContent(org, '100', 1);
        expect(two.ids).to.have.length(1);
        expect(two.truncated).to.equal(true);
        expect((await repo.findIdsByContent(org, '100', 5)).truncated).to.equal(
          false,
        );
      });
    });

    describe('plans', () => {
      it('use the owner and members indexes and the primary keys', async () => {
        // an org with many chats among a few thousand rows of others, statistics fresh
        const org = newId(),
          me = newId();
        await stack.admin(async (c) => {
          await c.query(
            `INSERT INTO conversation.chat_sessions (org_id, id, user_id, initiator, last_activity_at, created_at, updated_at)
             SELECT o, lpad(to_hex(2000000 + g), 24, '0'), CASE WHEN g % 50 = 0 THEN $2 ELSE lpad(to_hex(g % 40), 24, '0') END, lpad(to_hex(g % 40), 24, '0'), now() - make_interval(secs => g), now(), now()
               FROM unnest($1::text[]) o, generate_series(1, 6000) g`,
            [[org, newId(), newId()], me],
          );
          await c.query(
            `INSERT INTO conversation.chat_messages (org_id, session_id, seq, id, message_type, content, created_at, updated_at)
             SELECT $1, lpad(to_hex(2000000 + g), 24, '0'), s, lpad(to_hex(9000000 + g * 10 + s), 24, '0'), 'user_query', 'm', now(), now() FROM generate_series(1, 200) g, generate_series(1, 10) s`,
            [org],
          );
          await c.query(
            'ANALYZE conversation.chat_sessions; ANALYZE conversation.chat_messages',
          );
        });
        const plan = (sql: string, values: unknown[]) =>
          inOrg(org, () =>
            stack.rt.withUow({}, async (tx) =>
              (
                await tx.query<{ 'QUERY PLAN': string }>(
                  `/* pipeshub:test.plan */ EXPLAIN (COSTS OFF) ${sql}`,
                  values,
                )
              ).rows
                .map((r) => r['QUERY PLAN'])
                .join('\n'),
            ),
          );
        const owner = await plan(
          `SELECT s.id FROM conversation.chat_sessions s WHERE s.org_id = $1 AND NOT s.is_deleted AND NOT s.is_archived AND s.session_type = 'chat' AND s.user_id = $2::text
            ORDER BY s.last_activity_at DESC NULLS LAST, s.id DESC LIMIT 20`,
          [org, me],
        );
        expect(owner).to.match(
          /chat_sessions_owner_list/,
        );
        expect(owner).to.not.match(/Sort/);

        const messages = await plan(
          'SELECT m.seq FROM conversation.chat_messages m WHERE m.org_id = $1 AND m.session_id = $2::text ORDER BY m.seq OFFSET 0 LIMIT 50',
          [org, (2000001).toString(16).padStart(24, '0')],
        );
        expect(messages).to.match(/chat_messages_pkey/);

        const shared = await plan(
          `SELECT s.id FROM conversation.chat_session_members w JOIN conversation.chat_sessions s ON s.org_id = $1 AND s.id = w.session_id
            WHERE w.org_id = $1 AND w.user_id = $2::text AND s.is_shared AND NOT s.is_deleted`,
          [org, me],
        );
        expect(shared).to.match(/chat_session_members_(org_id_user_id|by_user)/);

        const byId = await plan(
          'SELECT s.id FROM conversation.chat_sessions s WHERE s.org_id = $1 AND s.id = $2::text',
          [org, '000000000000000000000001'],
        );
        expect(byId).to.match(/chat_sessions_pkey/);
      });
    });
  });
}
