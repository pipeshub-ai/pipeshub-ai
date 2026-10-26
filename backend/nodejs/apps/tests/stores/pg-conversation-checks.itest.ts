/**
 * What only PostgreSQL has to prove for the conversation stores: the author-only columns are projected per row inside
 * SQL, the content search keeps its budget, no row crosses an organisation under concurrency through PgBouncer, the
 * row policies hold, and the plans use the intended indexes and touch one org partition.
 */
import 'reflect-metadata';
import { EventEmitter } from 'node:events';
import { expect } from 'chai';
import { orgContextStorage } from '../../src/libs/context/orgContext';
import { attachUpstreamAbort } from '../../src/modules/enterprise_search/utils/stream-lifecycle';
import { PermissionDenied } from '../../src/libs/db';
import { InternalServerError } from '../../src/libs/errors/http.errors';
import { kernelTx } from '../../src/libs/repositories/pg';
import type { Tx } from '../../src/libs/repositories/uow';
import { PgAccessRequestRepository } from '../../src/libs/repositories/access-request/pg';
import { PgConversationRepository } from '../../src/libs/repositories/conversation/pg';
import type { NewMessage } from '../../src/libs/repositories/conversation';
import { PgSemanticSearchRepository } from '../../src/libs/repositories/semantic-search/pg';
import { OrgLedger, PG_BINS, PgStack, haveStack, inOrg, newId, scoped, scopedUow, startPgStack } from './pg-store-harness';

const CHAT = { kind: 'chat' } as const;
const ORG_ARGS = {
  findIdsByOwnTurnContent: 0, listVisibleTitles: 0, appendMessages: 1, replaceMessage: 1, deleteMessages: 1, appendFeedback: 1, findMessage: 1, countMessages: 1, getCitations: 1,
};
const bot = (content: string, extra: Partial<NewMessage> = {}): NewMessage => ({ messageType: 'bot_response', content, ...extra });
const user = (content: string, extra: Partial<NewMessage> = {}): NewMessage => ({ messageType: 'user_query', content, ...extra });
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
    const uow = scopedUow();

    before(async () => {
      stack = await startPgStack(bin, 'conversation_checks', { pool: 4, clientPool: 16 });
    });
    after(async () => {
      await stack?.stop();
    });

    const open = (org: string, userId: string, over: Record<string, unknown> = {}, first: NewMessage = user('hello')) =>
      repo.openSession(
        { orgId: org, userId, initiator: userId, title: 'chat', lastActivityAt: Date.now(), status: 'Complete', sessionType: 'chat', ...over } as never,
        first,
      );

    describe('the author-only fields of a turn (tools, reasoning, parts, reference data)', () => {
      it('come back only for the viewer’s own rows, whoever else reads the chat', async () => {
        const org = newId(), alice = newId(), bob = newId(), carol = newId();
        const s = await open(org, alice);
        await repo.setSharing(CHAT, s._id, { isShared: true, sharedWith: [{ userId: bob, accessLevel: 'write' }, { userId: carol, accessLevel: 'read' }] });
        await repo.appendMessages(s._id, org, [bot('alice answer', { ...DETAILS, sourceRecordIds: ['r1'] })], undefined, alice);
        await repo.appendMessages(s._id, org, [bot('bob answer', { ...DETAILS })], undefined, bob);
        await repo.appendMessages(s._id, org, [bot('bob plain answer')], undefined, bob);

        const rows = async (viewer: string) => (await repo.listMessages(s._id, viewer, { orgId: org })).filter((r) => r.dto.messageType === 'bot_response');
        const [aliceOwn, bobOnAlice, bobPlain] = [(await rows(alice))[0]!, (await rows(bob))[0]!, (await rows(bob))[2]!];
        expect(aliceOwn.dto.tools).to.deep.equal(DETAILS.tools);
        expect(aliceOwn.dto.reasoning).to.deep.equal(DETAILS.reasoning);
        expect(aliceOwn.dto.parts).to.deep.equal(DETAILS.parts);
        expect(aliceOwn.dto.referenceData).to.deep.equal(DETAILS.referenceData);
        expect(aliceOwn.dto).to.not.have.property('detailsHidden');

        // another viewer: the four are not returned at all; the row only says they exist
        for (const k of ['tools', 'reasoning', 'parts', 'referenceData']) expect(bobOnAlice.dto).to.not.have.property(k);
        expect(bobOnAlice.dto.detailsHidden).to.equal(true);
        expect(bobOnAlice.hasDetails).to.equal(true);
        expect(bobOnAlice.sourceRecordIds).to.deep.equal(['r1']);
        expect(bobOnAlice.authorUserId).to.equal(alice);
        // a row that holds none shows the same empty arrays everywhere
        expect(bobPlain.dto.tools).to.deep.equal([]);
        expect(bobPlain.hasDetails).to.equal(false);

        // a reader, and a caller that names no viewer, see no turn's details
        for (const viewer of [carol, '']) {
          const text = JSON.stringify(await repo.listMessages(s._id, viewer, { orgId: org }));
          for (const secret of ['TOOL-SECRET', 'REASONING-SECRET', 'PART-SECRET', 'REFERENCE-SECRET']) expect(text, `${viewer || 'no viewer'} / ${secret}`).to.not.include(secret);
        }
        const grouped = JSON.stringify([...(await repo.listMessagesOfSessions([s._id], carol, { orgId: org })).values()]);
        expect(grouped).to.not.include('SECRET');
        // reads with no viewer at all
        const found = JSON.stringify(await repo.findMessage(s._id, org, aliceOwn.dto._id));
        expect(found).to.not.include('SECRET');
        const fed = JSON.stringify(await repo.appendFeedback(aliceOwn.dto._id, org, { isHelpful: true }));
        expect(fed).to.not.include('SECRET');
      });

      it('are selected only for the viewer’s own rows by every statement that reads rows for a viewer', () => {
        const withAuthorOnly = stack.catalog.toJSON().statements.filter((x) => /chat_messages/.test(x.sql) && /\b(m\.)?(tools|reasoning|parts|reference_data)\b/.test(x.sql));
        expect(withAuthorOnly.length).to.be.greaterThan(3);
        for (const x of withAuthorOnly) {
          const own = /\b(INSERT INTO|UPDATE) conversation\.chat_messages/.test(x.sql); // the caller's own write, returned to them
          const projected = /CASE WHEN m\.author_user_id = \$3::text OR NOT m\.has_details THEN m\.tools END/.test(x.sql);
          const none = /NULL::jsonb AS tools/.test(x.sql);
          expect(own || projected || none, x.tag).to.equal(true);
        }
      });

      it('the archives list reads each listed chat for the viewer: a member sees the owner’s turns without their details', async () => {
        const org = newId(), alice = newId(), bob = newId();
        const s = await open(org, alice);
        await repo.appendMessages(s._id, org, [bot('answer', DETAILS)], undefined, alice);
        await repo.setSharing(CHAT, s._id, { isShared: true, sharedWith: [{ userId: bob, accessLevel: 'read' }] });
        await repo.setArchived(CHAT, s._id, { by: alice });
        const list = await repo.listSessions({
          orgId: org, userId: bob, scope: CHAT, owned: true, shared: true, archived: 'only', sort: { lastActivityAt: -1, _id: -1 }, skip: 0, limit: 10,
        });
        expect(list.rows.map((r) => r._id)).to.deep.equal([s._id]);
        const grouped = await repo.listMessagesOfSessions(list.rows.map((r) => r._id), bob, { orgId: org, withCitations: true });
        const answer = grouped.get(s._id)!.find((r) => r.dto.messageType === 'bot_response')!;
        expect(answer.dto.detailsHidden).to.equal(true);
        expect(JSON.stringify(grouped.get(s._id))).to.not.include('SECRET');
      });
    });

    describe('the content search of a user’s own turns', () => {
      const term = 'zebracorn';
      let org: string, heavy: string, other: string, oldSession: string;

      before(async () => {
        org = newId();
        heavy = newId();
        other = newId();
        const old = await open(org, heavy, { title: 'old' }, user(`a note about ${term}`));
        oldSession = old._id;
        const busy = await open(org, heavy, { title: 'busy' });
        await open(org, other, { title: 'theirs' }, user(`${term} in someone else's turn`));
        // 24,000 newer own messages (more than phase A scans) in the busy chat, none with the rare term, all with a common one
        await stack.admin((c) =>
          c.query(
            `INSERT INTO conversation.chat_messages (org_id, session_id, seq, id, author_user_id, message_type, content, created_at, updated_at)
             SELECT $1, $2, 1000 + g, lpad(to_hex(1000000 + g), 24, '0'), $3, 'user_query', 'the quick brown fox number ' || g, now() + make_interval(secs => g), now()
               FROM generate_series(1, 24000) g`,
            [org, busy._id, heavy],
          ),
        );
        await stack.admin((c) => c.query('ANALYZE conversation.chat_messages'));
      });

      it('finds a term in the newest messages in phase A alone', async () => {
        const r = await repo.findIdsByOwnTurnContent(org, 'quick brown', heavy, 50);
        expect(r.ids).to.have.length(1);
        expect(r.truncated).to.equal(true); // older own messages were not scanned
      });

      it('reaches older messages through the trigram index in phase B, and never another user’s turns', async () => {
        const r = await repo.findIdsByOwnTurnContent(org, term, heavy);
        expect(r.ids).to.deep.equal([oldSession]);
        expect(r.truncated).to.equal(false);
        expect((await repo.findIdsByOwnTurnContent(org, term, newId())).ids).to.deep.equal([]);
      });

      it('puts the unit’s own statement timeout back after phase B and leaves the unit usable (the cancelled path is in conversation-search.test.ts)', async () => {
        const short = scoped(new PgConversationRepository('1ms'), ledger, ORG_ARGS);
        const setting = async (tx: Tx) => (await kernelTx(tx).query<{ v: string }>("/* pipeshub:test.timeout */ SELECT current_setting('statement_timeout') AS v")).rows[0]?.v;
        const out = await uow.run(async (tx) => {
          const before = await setting(tx);
          const r = await short.findIdsByOwnTurnContent(org, 'the', heavy, 5000);
          const n = await short.countMessages(oldSession, org);
          return { r, n, before, after: await setting(tx) };
        }, { org, user: heavy });
        expect(out.n).to.equal(1);
        expect(out.r.ids.length).to.be.greaterThan(0);
        expect(out.after).to.equal(out.before);
      });

      it('a term of one or two characters is never sent to the trigram index', async () => {
        const r = await repo.findIdsByOwnTurnContent(org, 'qu', heavy, 50);
        expect(r.ids.length).to.be.greaterThan(0);
        expect(r.truncated).to.equal(true);
      });

      it('treats LIKE metacharacters in the search as text', async () => {
        const s = await open(org, heavy, { title: 'pct' }, user('growth was 100% over_budget'));
        expect((await repo.findIdsByOwnTurnContent(org, '100%', heavy, 50)).ids).to.include(s._id);
        expect((await repo.findIdsByOwnTurnContent(org, '100\\%', heavy, 50)).ids).to.include(s._id);
        expect((await repo.findIdsByOwnTurnContent(org, 'over_bud', heavy, 50)).ids).to.include(s._id);
        expect((await repo.findIdsByOwnTurnContent(org, 'overXbud', heavy, 50)).ids).to.deep.equal([]);
      });
    });

    describe('the content search with the longest terms the API accepts', () => {
      let org: string, me: string;
      before(async () => {
        org = newId();
        me = newId();
        for (const text of ['ab'.repeat(300), 'x'.repeat(1000), '%'.repeat(1000), '_%\\'.repeat(300), 'plain text about nothing'])
          await open(org, me, { title: text.slice(0, 20) }, user(text));
      });
      const find = async (term: string) => {
        const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); // what the API hands the repository
        return (await repo.findIdsByOwnTurnContent(org, escaped, me)).ids.length;
      };

      it('answers a 600 and a 1000 character term instead of refusing it', async () => {
        expect(await find('ab'.repeat(300))).to.equal(1);
        expect(await find('x'.repeat(1000))).to.equal(1);
        expect(await find('x'.repeat(999))).to.equal(1); // a substring of the 1000
        expect(await find('q'.repeat(399))).to.equal(0);
      });

      it('matches % _ and \\ literally, so a wildcard-heavy term finds only what holds it', async () => {
        expect(await find('%'.repeat(1000))).to.equal(1); // not "everything"
        expect(await find('%')).to.equal(2); // the two messages that hold a percent sign
        expect(await find('_%\\'.repeat(300))).to.equal(1);
        expect(await find('_')).to.equal(1);
        expect(await find('a_b')).to.equal(0);
      });

      it('takes the user from the unit’s context: a call naming another user is refused, not answered for them', async () => {
        const e = await inOrg(org, () => new PgConversationRepository().findIdsByOwnTurnContent(org, 'plain', me), newId()).catch((x) => x);
        expect(e).to.be.instanceOf(InternalServerError);
        expect(e.message).to.match(/user of the call/);
        // and the database function itself answers for app.user_id only
        const other = await inOrg(org, () => stack.rt.withUow({}, async (tx) => (await tx.query('/* pipeshub:test.fn */ SELECT * FROM conversation.search_recent($1, 5)', ['plain'])).rows), newId());
        expect(other).to.deep.equal([]);
        const none = await inOrg(org, () => stack.rt.withUow({}, async (tx) => (await tx.query('/* pipeshub:test.fn */ SELECT * FROM conversation.search_recent($1, 5)', ['plain'])).rows), '');
        expect(none).to.deep.equal([]);
      });
    });

    describe('a client that leaves mid-answer', () => {
      it('the partial answer is saved in the request’s org when the detached-run cap fires, though the disconnect is emitted outside any request context', async () => {
        const org = newId(), me = newId();
        const s = await open(org, me);
        const res = Object.assign(new EventEmitter(), { writableEnded: false, writableFinished: false });
        const raw = new PgConversationRepository(); // no scoping of its own: only the bound listener supplies the org
        let saved!: Promise<unknown>;
        const capped = new Promise<void>((resolve) => {
          // a real (short) cap, not a fake clock: the context must travel through a real timer
          inOrg(org, () => attachUpstreamAbort(res, 'req-1', () => {
            saved = raw.appendMessages(s._id, org, [bot('partial answer', { status: 'stopped' })], undefined, me);
            resolve();
          }, 50), me);
        });
        orgContextStorage.exit(() => res.emit('close')); // the emitter's context is another request's, or none
        await capped;
        await saved;
        const rows = await stack.admin(async (c) => (await c.query("SELECT content, status FROM conversation.chat_messages WHERE org_id = $1 AND session_id = $2 AND message_type = 'bot_response'", [org, s._id])).rows);
        expect(rows).to.deep.equal([{ content: 'partial answer', status: 'stopped' }]);
      });
    });

    describe('guards that only a test can see', () => {
      const asked: string[] = [];
      const spy = async <T>(fn: () => Promise<T>): Promise<T> => {
        const connect = stack.pool.connect.bind(stack.pool) as () => Promise<{ query: (...a: unknown[]) => Promise<unknown> }>;
        (stack.pool as unknown as { connect: unknown }).connect = async () => {
          const c = await connect();
          const q = c.query.bind(c);
          c.query = (...a: unknown[]) => {
            asked.push(typeof a[0] === 'string' ? a[0] : String((a[0] as { text?: string }).text));
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
        const a = newId(), b = newId(), me = newId();
        const s = await open(b, me);
        asked.length = 0;
        const calls: Array<[string, () => Promise<unknown>]> = [
          ['listMessages', () => new PgConversationRepository().listMessages(s._id, me, { orgId: b })],
          ['countMessages', () => new PgConversationRepository().countMessages(s._id, b)],
          ['appendMessages', () => new PgConversationRepository().appendMessages(s._id, b, [user('x')])],
          ['getCitations', () => new PgConversationRepository().getCitations([s._id], b)],
          ['access get', () => new PgAccessRequestRepository().get(b, s._id)],
          ['search findVisible', () => new PgSemanticSearchRepository().findVisible(b, me, s._id)],
        ];
        for (const [name, call] of calls) {
          const e = await spy(() => inOrg(a, call)).catch((x) => x);
          expect(e, name).to.be.instanceOf(InternalServerError);
          expect(e.message, name).to.match(/organisation of the call/);
        }
        expect(asked.filter((q) => /pipeshub:(conversation|access|search)\./.test(q)), 'no statement on the tables').to.deep.equal([]);
      });

      it('two concurrent requests for the same record give one row and no error', async () => {
        const org = newId(), me = newId(), owner = newId();
        const access = new PgAccessRequestRepository();
        const input = { requesterId: me, recordId: 'rec-same', kind: 'native' as const, ownerUserIds: [owner], status: 'pending' as const };
        const made = await Promise.all(Array.from({ length: 8 }, () => inOrg(org, () => access.create(org, input))));
        expect(new Set(made.map((r) => r._id)).size).to.equal(1);
        const n = await stack.admin(async (c) => (await c.query('SELECT count(*)::int AS n FROM access.access_requests WHERE org_id = $1', [org])).rows[0].n);
        expect(n).to.equal(1);
        const owners = await stack.admin(async (c) => (await c.query('SELECT count(*)::int AS n FROM access.access_request_approvers WHERE org_id = $1', [org])).rows[0].n);
        expect(owners).to.equal(1);
      });
    });

    describe('under concurrency through PgBouncer', () => {
      it('no row crosses an organisation, seq numbers stay dense, and no role or org leaks into the pooled connections', async () => {
        const orgs = Array.from({ length: 6 }, () => newId());
        const users = new Map(orgs.map((o) => [o, [newId(), newId(), newId()]]));
        const sessions = new Map<string, string[]>();
        for (const o of orgs) {
          const [u1, u2] = users.get(o)!;
          const list: string[] = [];
          for (const owner of [u1!, u2!]) {
            const s = await open(o, owner);
            await repo.setSharing(CHAT, s._id, { isShared: true, sharedWith: [{ userId: users.get(o)![2]!, accessLevel: 'write' }] });
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
            await repo.appendMessages(sid, o, [user(`q${i}`), bot(`a${i}`, DETAILS)], undefined, author);
            written.set(o, (written.get(o) ?? 0) + 2);
          }
          const mine = await repo.listSessions({ orgId: o, userId: author, scope: CHAT, owned: true, shared: true, archived: 'no', sort: { lastActivityAt: -1, _id: -1 }, skip: 0, limit: 50 });
          for (const r of mine.rows) if (!sessions.get(o)!.includes(r._id)) foreign.push(r._id);
          const msgs = await repo.listMessages(sid, author, { orgId: o });
          if (msgs.some((m) => m.dto.orgId !== o || m.dto.sessionId !== sid)) foreign.push(sid);
          const lease = await repo.reserveRun(CHAT, sid, { orgId: o, userId: author }, 2000).catch(() => null);
          if (lease) await repo.releaseRun(lease);
        };
        let next = 0;
        await Promise.all(Array.from({ length: 24 }, async () => {
          for (;;) {
            const i = next++;
            if (i >= 240) return;
            await task(i);
          }
        }));
        expect(foreign).to.deep.equal([]);
        const check = await stack.admin(async (c) => {
          const dense = (await c.query(`SELECT s.org_id, s.id, s.next_seq, count(m.*)::int AS n, count(DISTINCT m.seq)::int AS d, COALESCE(max(m.seq), 0)::int AS top
              FROM conversation.chat_sessions s LEFT JOIN conversation.chat_messages m ON m.org_id = s.org_id AND m.session_id = s.id
             WHERE s.org_id = ANY($1::text[]) GROUP BY s.org_id, s.id, s.next_seq`, [orgs])).rows;
          const crossed = (await c.query(`SELECT count(*)::int AS n FROM conversation.chat_messages m
             WHERE m.org_id = ANY($1::text[]) AND NOT EXISTS (SELECT 1 FROM conversation.chat_sessions s WHERE s.org_id = m.org_id AND s.id = m.session_id)`, [orgs])).rows[0].n;
          return { dense, crossed };
        });
        expect(check.crossed).to.equal(0);
        for (const r of check.dense) {
          expect(r.n, `${r.id} has duplicate seq`).to.equal(r.d);
          expect(r.top, `${r.id} seq is not dense`).to.equal(r.n);
          expect(r.next_seq).to.equal(r.n);
        }
        for (const o of orgs) expect(check.dense.filter((r: { org_id: string }) => r.org_id === o).reduce((n: number, r: { n: number }) => n + r.n, 0)).to.equal(written.get(o)! + 2);
        // a connection from the pool carries nothing of the last unit
        const probe = await stack.pool.query("SELECT current_setting('role') AS role, current_setting('app.org_id', true) AS org");
        expect(probe.rows[0].role).to.equal('none');
        expect(['', null]).to.include(probe.rows[0].org);
      });

      it('of racing writers on one write-shared chat each gets its own seq block, and the run lease has one winner', async () => {
        const org = newId(), a = newId(), b = newId();
        const s = await open(org, a);
        await repo.setSharing(CHAT, s._id, { isShared: true, sharedWith: [{ userId: b, accessLevel: 'write' }] });
        await Promise.all(Array.from({ length: 30 }, (_, i) => repo.appendMessages(s._id, org, [user(`q${i}`), bot(`a${i}`)], undefined, i % 2 ? a : b)));
        const seqs = (await repo.listMessages(s._id, a, { orgId: org })).map((r) => r.dto.seq);
        expect(seqs).to.deep.equal(Array.from({ length: 61 }, (_, i) => i + 1));
        const racers = await Promise.allSettled(Array.from({ length: 6 }, () => repo.reserveRun(CHAT, s._id, { orgId: org, userId: a }, 150)));
        expect(racers.filter((r) => r.status === 'fulfilled' && r.value)).to.have.length(1);
      });
    });

    describe('row-level security', () => {
      const TABLES = [
        'conversation.chat_sessions', 'conversation.chat_session_members', 'conversation.chat_messages', 'conversation.citations',
        'conversation.semantic_searches', 'conversation.semantic_search_members', 'access.access_requests', 'access.access_request_approvers',
      ];

      it('shows an organisation only its own rows in every table, and nothing before the organisation is known', async () => {
        const a = newId(), b = newId(), u = newId(), w = newId();
        const search = scoped(new PgSemanticSearchRepository(), ledger, { findVisible: 0, listVisible: 0 });
        const access = scoped(new PgAccessRequestRepository(), ledger, { create: 0 });
        for (const org of [a, b]) {
          const s = await open(org, u);
          await repo.setSharing(CHAT, s._id, { isShared: true, sharedWith: [{ userId: w, accessLevel: 'read' }] });
          await repo.createCitations([{ content: 'c', chunkIndex: 0, citationType: 't', metadata: { orgId: org, mimeType: 'm', recordId: 'r', recordName: 'n', origin: 'o' } as never }]);
          const m = await search.create({ orgId: org, userId: u, query: 'q', limit: 1, records: new Map() });
          await search.setSharing(m._id, { isShared: true, sharedWith: [{ userId: w, accessLevel: 'read' }] });
          await access.create(org, { requesterId: u, recordId: 'rec', kind: 'native', ownerUserIds: [w], status: 'pending' });
        }
        const count = async (org: string | undefined) =>
          Object.fromEntries(
            await Promise.all(TABLES.map(async (t) => {
              const run = () => stack.rt.withUow({ preOrg: org === undefined }, async (tx) => Number((await tx.query<{ n: number }>(`/* pipeshub:test.count */ SELECT count(*)::int AS n FROM ${t} WHERE true`)).rows[0]?.n));
              return [t, await (org ? inOrg(org, run) : run())] as const;
            })),
          );
        const seenByA = await count(a);
        for (const t of TABLES) expect(seenByA[t], t).to.equal(1);
        expect(Object.values(await count(undefined)).every((n) => n === 0)).to.equal(true);
      });

      it('refuses a write that names another organisation, and a direct read of a partition', async () => {
        const a = newId(), b = newId();
        const e = await inOrg(a, () => stack.rt.withUow({ write: true }, (tx) =>
          tx.query(`/* pipeshub:test.cross */ INSERT INTO conversation.chat_sessions (org_id, id, user_id, initiator, last_activity_at, created_at, updated_at)
                    VALUES ($1, $2, $3, $3, now(), now(), now())`, [b, newId(), newId()]))).catch((x) => x);
        expect(e).to.be.instanceOf(PermissionDenied);
        const p = await inOrg(a, () => stack.rt.withUow({}, (tx) => tx.query('/* pipeshub:test.leaf */ SELECT 1 FROM conversation.chat_sessions_h00'))).catch((x) => x);
        expect(p).to.be.instanceOf(PermissionDenied);
      });

      it('the content search function answers a viewer only from their own org', async () => {
        const a = newId(), b = newId(), v = newId();
        const sb = await open(b, v, {}, user('needle text'));
        expect((await repo.findIdsByOwnTurnContent(b, 'needle', v)).ids).to.deep.equal([sb._id]);
        expect((await repo.findIdsByOwnTurnContent(a, 'needle', v)).ids).to.deep.equal([]);
      });
    });

    describe('plans', () => {
      it('use the owner and members indexes, the message primary key, and one org partition', async () => {
        // an org with many chats among a few thousand rows of others, statistics fresh
        const org = newId(), me = newId();
        await stack.admin(async (c) => {
          await c.query(
            `INSERT INTO conversation.chat_sessions (org_id, id, user_id, initiator, last_activity_at, created_at, updated_at)
             SELECT o, lpad(to_hex(2000000 + g), 24, '0'), CASE WHEN g % 50 = 0 THEN $2 ELSE lpad(to_hex(g % 40), 24, '0') END, lpad(to_hex(g % 40), 24, '0'), now() - make_interval(secs => g), now(), now()
               FROM unnest($1::text[]) o, generate_series(1, 6000) g`,
            [[org, newId(), newId()], me],
          );
          await c.query(
            `INSERT INTO conversation.chat_messages (org_id, session_id, seq, id, author_user_id, message_type, content, created_at, updated_at)
             SELECT $1, lpad(to_hex(2000000 + g), 24, '0'), s, lpad(to_hex(9000000 + g * 10 + s), 24, '0'), $2, 'user_query', 'm', now(), now() FROM generate_series(1, 200) g, generate_series(1, 10) s`,
            [org, me],
          );
          await c.query('ANALYZE conversation.chat_sessions; ANALYZE conversation.chat_messages');
        });
        const plan = (sql: string, values: unknown[]) =>
          inOrg(org, () => stack.rt.withUow({}, async (tx) => (await tx.query<{ 'QUERY PLAN': string }>(`/* pipeshub:test.plan */ EXPLAIN (COSTS OFF) ${sql}`, values)).rows.map((r) => r['QUERY PLAN']).join('\n')));
        const leaves = (p: string, table: string) => new Set(p.match(new RegExp(`${table}_h\\d\\d`, 'g')) ?? []);

        const owner = await plan(
          `SELECT s.id FROM conversation.chat_sessions s WHERE s.org_id = $1 AND NOT s.is_deleted AND NOT s.is_archived AND s.session_type = 'chat' AND s.user_id = $2::text
            ORDER BY s.last_activity_at DESC NULLS LAST, s.id DESC LIMIT 20`, [org, me]);
        expect(owner).to.match(/chat_sessions_h\d\d_org_id_user_id_session_type_last_activity_/);
        expect(leaves(owner, 'chat_sessions').size).to.equal(1);
        expect(owner).to.not.match(/Sort/);

        const messages = await plan(
          'SELECT m.seq FROM conversation.chat_messages m WHERE m.org_id = $1 AND m.session_id = $2::text ORDER BY m.seq OFFSET 0 LIMIT 50', [org, (2000001).toString(16).padStart(24, '0')]);
        expect(messages).to.match(/chat_messages_h\d\d_pkey/);
        expect(leaves(messages, 'chat_messages').size).to.equal(1);

        const shared = await plan(
          `SELECT s.id FROM conversation.chat_session_members w JOIN conversation.chat_sessions s ON s.org_id = $1 AND s.id = w.session_id
            WHERE w.org_id = $1 AND w.user_id = $2::text AND s.is_shared AND NOT s.is_deleted`, [org, me]);
        expect(shared).to.match(/chat_session_members_h\d\d_org_id_user_id_/);
        expect(leaves(shared, 'chat_sessions').size).to.equal(1);
        expect(leaves(shared, 'chat_session_members').size).to.equal(1);

        const byId = await plan('SELECT s.id FROM conversation.chat_sessions s WHERE s.org_id = $1 AND s.id = $2::text', [org, '000000000000000000000001']);
        expect(byId).to.match(/chat_sessions_h\d\d_pkey/);
        expect(leaves(byId, 'chat_sessions').size).to.equal(1);
      });
    });
  });
}
