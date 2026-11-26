/**
 * An org's and a person's deletion on PostgreSQL end to end (ORGDEL, CLEANUP-G1, G2): the deletion's reads and writes
 * on main's organisation, user and conversation data through main's repositories, the deletion service's transactions,
 * the org's erasure across every harness table (and nothing of another org), and the deletion's small stores (who may
 * export, group membership, the instance operator, the `orgDeleted` event, the expired download tickets).
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'node:crypto';
import { getConversationRepository } from '../../../src/libs/repositories/conversation';
import { setNotificationRepository } from '../../../src/libs/repositories/notification';
import { PgNotificationRepository } from '../../../src/libs/repositories/notification/pg';
import { setOutboxRepository } from '../../../src/libs/repositories/outbox';
import { PgOutboxRepository } from '../../../src/libs/repositories/outbox/pg';
import { newObjectId } from '../../../src/libs/repositories/pg-util';
import { inPgDurableTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/pg';
import { orgDeletionData } from '../../../src/libs/repositories/agent-harness/org-deletion';
import { PgGroupMembership } from '../../../src/libs/repositories/agent-harness/group-membership/pg';
import { PgOrgDeletedAnnouncer } from '../../../src/libs/repositories/agent-harness/org-deleted-announcer/pg';
import {
  PG_ORG_TABLE_COVERAGE,
  PgOrgData,
  PgPrincipalErasureJobs,
} from '../../../src/libs/repositories/agent-harness/principal-erasure/pg';
import { PgErasureJobStore } from '../../../src/libs/repositories/agent-harness/session-erasure/pg';
import { PrincipalDeletionService } from '../../../src/modules/user_management/services/principal-deletion.service';
import { describeOrgDeletionDataContract, SeedUser } from './support/org-deletion-data-contract';
import { usePgIdentityRepositories } from './support/harness-pg-identity';
import { PgStack, boundStore, inOrg, pgBins, pgSuite, resetHarnessPg, startHarnessPg, transactAs } from './support/harness-pg';

for (const bin of pgBins()) {
  pgSuite(`org and person deletion on PostgreSQL (${bin}) behind PgBouncer`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    let restore: () => void;
    let docStore: string | undefined;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_org_deletion');
      restore = usePgIdentityRepositories();
      setNotificationRepository(new PgNotificationRepository());
      setOutboxRepository(new PgOutboxRepository());
      docStore = process.env.DOC_STORE;
      process.env.DOC_STORE = 'postgres';
    });
    after(async () => {
      if (docStore === undefined) delete process.env.DOC_STORE;
      else process.env.DOC_STORE = docStore;
      restore?.();
      setNotificationRepository();
      setOutboxRepository();
      await stack?.stop();
    });

    const sql = async <R = Record<string, unknown>>(text: string, values: unknown[] = []): Promise<R[]> =>
      (await stack.admin((c) => c.query(text, values))).rows as R[];

    const seedOrg = async (names: { registeredName?: string; shortName?: string } = {}): Promise<string> => {
      const org = newObjectId();
      await sql(
        `INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, registered_name, short_name, created_at, updated_at)
          VALUES ($1, $2, $3, 'business', $4, $5, now(), now())`,
        [org, `${randomUUID().slice(0, 8)}.example.com`, `o-${randomUUID().slice(0, 8)}@example.com`, names.registeredName ?? null, names.shortName ?? null],
      );
      return org;
    };
    let tick = 0;
    const seedUser = async (orgId: string, opts: SeedUser = {}): Promise<string> => {
      const id = newObjectId();
      tick += 1;
      await sql(
        `INSERT INTO identity.users (org_id, id, slug, full_name, email, kind, role, is_disabled, created_at, updated_at)
          VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now() + make_interval(secs => $9), now())`,
        [orgId, id, `user-${id}`, opts.fullName ?? null, opts.email ?? `${id}@example.com`, opts.kind ?? 'human', opts.role ?? 'member', opts.disabled === true, tick],
      );
      return id;
    };

    describeOrgDeletionDataContract('postgres', async () => {
      await resetHarnessPg(stack);
      return {
        data: boundStore(orgDeletionData()),
        transact: (orgId, work) => transactAs(work, orgId),
        org: seedOrg,
        user: seedUser,
        conversation: async (orgId, ownerId, opts = {}) =>
          inOrg(orgId, async () => {
            const repo = getConversationRepository();
            const session = await repo.openSession(
              { orgId, userId: ownerId, initiator: ownerId, title: 'c', lastActivityAt: Date.now(), status: 'Complete', sessionType: 'chat' },
              { messageType: 'user_query', content: 'hi', createdAt: new Date(), updatedAt: new Date() },
            );
            if (opts.sharedWith !== undefined)
              await repo.setSharing({ kind: 'chat' }, session._id, {
                isShared: true,
                sharedWith: opts.sharedWith.map((userId) => ({ userId, accessLevel: 'read' as const })),
              });
            if (opts.deleted === true) await repo.softDelete(session._id, { orgId, userId: ownerId });
            return session._id;
          }),
        conversationState: async (orgId, id) => {
          const [row] = await sql<{ user_id: string; initiator: string; is_deleted: boolean; erasure_requested_at: string | null }>(
            `SELECT user_id, initiator, is_deleted, erasure_requested_at FROM conversation.chat_sessions WHERE org_id = $1 AND id = $2`,
            [orgId, id],
          );
          const members = await sql<{ user_id: string }>(
            `SELECT user_id FROM conversation.chat_session_members WHERE org_id = $1 AND session_id = $2 ORDER BY pos`,
            [orgId, id],
          );
          return {
            owner: row!.user_id,
            initiator: row!.initiator,
            sharedWith: members.map((m) => m.user_id),
            isDeleted: row!.is_deleted,
            ...(row!.erasure_requested_at !== null ? { erasureRequestedAt: Number(row!.erasure_requested_at) } : {}),
          };
        },
        isDisabled: async (orgId, userId) =>
          (await sql<{ d: boolean }>(`SELECT is_disabled AS d FROM identity.users WHERE org_id = $1 AND id = $2`, [orgId, userId]))[0]!.d,
      };
    });

    describe('the deletion service on PostgreSQL', () => {
      beforeEach(() => resetHarnessPg(stack));
      const service = () => new PrincipalDeletionService(new PgPrincipalErasureJobs(), new PgErasureJobStore(), inPgDurableTransaction);

      it("CLEANUP-G2: a deleted user's private conversation is erased, the shared one moves to the earliest admin, in one unit with the job", async () => {
        const org = await seedOrg();
        const admin = await seedUser(org, { role: 'admin' });
        const leaving = await seedUser(org);
        const reader = await seedUser(org);
        const [own, shared] = await inOrg(org, async () => {
          const repo = getConversationRepository();
          const open = () =>
            repo.openSession(
              { orgId: org, userId: leaving, initiator: leaving, title: 'c', lastActivityAt: Date.now(), status: 'Complete', sessionType: 'chat' },
              { messageType: 'user_query', content: 'hi', createdAt: new Date(), updatedAt: new Date() },
            );
          const a = await open();
          const b = await open();
          await repo.setSharing({ kind: 'chat' }, b._id, { isShared: true, sharedWith: [{ userId: reader, accessLevel: 'read' }] });
          return [a._id, b._id];
        });
        expect(await inOrg(org, () => service().handOverUser(org, leaving, admin))).to.deep.equal({ erased: 1, transferred: 1 });
        const sessions = await sql<{ id: string; user_id: string; is_deleted: boolean }>(
          `SELECT id, user_id, is_deleted FROM conversation.chat_sessions WHERE org_id = $1 ORDER BY id`,
          [org],
        );
        expect(sessions.find((s) => s.id === own)).to.deep.include({ is_deleted: true, user_id: leaving });
        expect(sessions.find((s) => s.id === shared)).to.deep.include({ is_deleted: false, user_id: admin });
        expect(await boundStore(new PgErasureJobStore()).find(org, own)).to.deep.include({ reason: 'user_delete', status: 'pending' });
        const [job] = (await boundStore(new PgPrincipalErasureJobs()).due(Number.MAX_SAFE_INTEGER, 10)).filter((j) => j.orgId === org);
        expect(job).to.deep.include({ kind: 'user', userId: leaving, transfers: [{ sessionId: shared, to: admin }] });
      });
    });

    describe("the org's erasure across the harness tables", () => {
      beforeEach(() => resetHarnessPg(stack));

      it('every harness table that holds org rows says where it stands in an org erasure', async () => {
        const rows = await sql<{ table_name: string }>(
          `SELECT DISTINCT table_name FROM information_schema.columns
            WHERE table_schema = 'harness' AND column_name = 'org_id' ORDER BY table_name`,
        );
        expect(rows.map((r) => r.table_name)).to.deep.equal(Object.keys(PG_ORG_TABLE_COVERAGE).sort());
      });

      it("removes the org's rows from every org table and main's conversation and notification data, and nothing of another org", async () => {
        const [mine, theirs] = [await seedOrg(), await seedOrg()];
        for (const org of [mine, theirs]) {
          const user = await seedUser(org);
          await inOrg(org, () =>
            getConversationRepository().openSession(
              { orgId: org, userId: user, initiator: user, title: 'c', lastActivityAt: Date.now(), status: 'Complete', sessionType: 'chat' },
              { messageType: 'user_query', content: 'hi', createdAt: new Date(), updatedAt: new Date() },
            ),
          );
          await boundStore(new PgErasureJobStore()).enqueue({ orgId: org, sessionId: newObjectId(), reason: 'retention', by: null });
          await sql(
            `INSERT INTO harness.session_events (org_id, stream_id, seq, session_id, ts, doc, region) VALUES ($1, 's', 1, 'sess', now(), '{}', 'r')`,
            [org],
          );
          await inOrg(org, () => new PgNotificationRepository().insert({ orgId: org, assignedTo: user, type: 't' }));
          await sql(
            `INSERT INTO platform.outbox (org_id, topic, msg_key, value, status, next_attempt_at, created_at, updated_at)
             VALUES ($1, 'agent-run-events', 'dispatch', '{}', 'published', now(), now(), now()),
                    ($1, 'entity-events', 'orgDeleted', '{}', 'pending', now(), now(), now())`,
            [org],
          );
        }
        await sql(`INSERT INTO harness.erasure_backfill_plans (digest, doc, region) VALUES ('d-own', $1, 'r'), ('d-both', $2, 'r')`, [
          JSON.stringify({ orgFilter: mine, orgs: [{ orgId: mine }] }),
          JSON.stringify({ orgFilter: null, orgs: [{ orgId: mine }, { orgId: theirs }] }),
        ]);
        const counts = await boundStore(new PgOrgData()).eraseStores(mine);
        expect(counts).to.deep.include({ session_events: 1, session_erasures: 1, chat_sessions: 1, chat_messages: 1, notifications: 1, outbox: 1 });
        for (const table of Object.keys(PG_ORG_TABLE_COVERAGE).filter((t) => PG_ORG_TABLE_COVERAGE[t] === 'org_stores')) {
          const [row] = await sql<{ n: number }>(`SELECT count(*)::int AS n FROM harness.${table} WHERE org_id = $1`, [mine]);
          expect(row!.n, table).to.equal(0);
        }
        expect(await sql(`SELECT id FROM conversation.chat_sessions WHERE org_id = $1`, [mine])).to.deep.equal([]);
        expect(await sql(`SELECT id FROM conversation.chat_sessions WHERE org_id = $1`, [theirs])).to.have.length(1);
        expect(await sql(`SELECT 1 FROM harness.session_events WHERE org_id = $1`, [theirs])).to.have.length(1);
        expect(await sql(`SELECT 1 FROM harness.session_erasures WHERE org_id = $1`, [theirs])).to.have.length(1);
        expect(await sql(`SELECT 1 FROM platform.notifications WHERE org_id = $1`, [mine])).to.deep.equal([]);
        expect(await sql(`SELECT 1 FROM platform.notifications WHERE org_id = $1`, [theirs])).to.have.length(1);
        // WP114-FU-OUTBOX: the org's delivered events go; its own deletion event, still on its way, is left to go out.
        expect(await sql(`SELECT msg_key FROM platform.outbox WHERE org_id = $1`, [mine])).to.deep.equal([{ msg_key: 'orgDeleted' }]);
        expect(await sql(`SELECT 1 FROM platform.outbox WHERE org_id = $1`, [theirs])).to.have.length(2);
        const plans = await sql<{ digest: string; doc: { orgs: Array<{ orgId: string }> } }>(
          `SELECT digest, doc FROM harness.erasure_backfill_plans ORDER BY digest`,
        );
        expect(plans.map((p) => [p.digest, p.doc.orgs.map((o) => o.orgId)])).to.deep.equal([['d-both', [theirs]]]);
        expect(await boundStore(new PgOrgData()).eraseStores(mine)).to.deep.equal({});
      });

      it("the org's sessions after a cursor, its pending erasures and its live runs", async () => {
        const org = await seedOrg();
        const user = await seedUser(org);
        const session = await inOrg(org, () =>
          getConversationRepository().openSession(
            { orgId: org, userId: user, initiator: user, title: 'c', lastActivityAt: Date.now(), status: 'Complete', sessionType: 'chat' },
            { messageType: 'user_query', content: 'hi', createdAt: new Date(), updatedAt: new Date() },
          ),
        );
        const runOnly = 'f'.repeat(24);
        await sql(
          `INSERT INTO harness.runs (org_id, run_id, doc, region) VALUES ($1, 'run-live', $2, 'r'), ($1, 'run-done', $3, 'r')`,
          [
            org,
            JSON.stringify({ orgId: org, runId: 'run-live', sessionId: runOnly, status: 'running' }),
            JSON.stringify({ orgId: org, runId: 'run-done', sessionId: runOnly, status: 'completed' }),
          ],
        );
        const data = boundStore(new PgOrgData());
        expect(await data.sessionsAfter(org, null, 10)).to.deep.equal([session._id, runOnly].sort());
        expect(await data.sessionsAfter(org, session._id < runOnly ? session._id : runOnly, 10)).to.have.length(1);
        expect((await data.activeRuns(org)).map((r) => r.runId)).to.deep.equal(['run-live']);
        await transactAs((tx) => data.markSessionsDeleted(org, [session._id], tx), org);
        expect((await sql<{ d: boolean }>(`SELECT is_deleted AS d FROM conversation.chat_sessions WHERE id = $1`, [session._id]))[0]!.d).to.equal(true);
        await boundStore(new PgErasureJobStore()).enqueue({ orgId: org, sessionId: session._id, reason: 'user_delete', by: null });
        expect(await data.pendingSessionErasures(org)).to.equal(1);
        expect(await data.pendingSessionErasures(await seedOrg())).to.equal(0);
      });
    });

    describe("the deletion's small stores", () => {
      beforeEach(() => resetHarnessPg(stack));

      it("group membership: only the org's own live groups count; the others are unknown", async () => {
        const org = await seedOrg();
        const user = await seedUser(org);
        const [live, gone, empty] = [newObjectId(), newObjectId(), newObjectId()];
        for (const [id, deleted] of [
          [live, false],
          [gone, true],
          [empty, false],
        ] as const)
          await sql(
            `INSERT INTO identity.user_groups (org_id, id, slug, name, type, is_deleted, created_at, updated_at) VALUES ($1, $2, $3, $3, 'custom', $4, now(), now())`,
            [org, id, `g-${id}`, deleted],
          );
        await sql(`INSERT INTO identity.user_group_members (org_id, group_id, user_id, pos) VALUES ($1, $2, $3, 0), ($1, $4, $3, 0)`, [org, live, user, gone]);
        const groups = boundStore(new PgGroupMembership());
        expect(await groups.memberOfAny(org, user, [live])).to.equal(true);
        expect(await groups.memberOfAny(org, user, [gone, empty])).to.equal(false);
        expect(await groups.memberOfAny(await seedOrg(), user, [live])).to.equal(false);
        const stranger = newObjectId();
        expect(await groups.unknownGroups(org, [empty, gone, stranger, live])).to.deep.equal([gone, stranger]);
      });

      it("the orgDeleted event is the org's own outbox row", async () => {
        const org = await seedOrg();
        await sql(`DELETE FROM platform.outbox`);
        await boundStore(new PgOrgDeletedAnnouncer()).orgDeleted(org);
        const rows = await sql<{ org_id: string; topic: string; ordering_key: string; value: string }>(
          `SELECT org_id, topic, ordering_key, value FROM platform.outbox`,
        );
        expect(rows).to.have.length(1);
        expect(rows[0]).to.deep.include({ org_id: org, topic: 'entity-events', ordering_key: `org:${org}` });
        expect(JSON.parse(rows[0]!.value)).to.deep.include({ eventType: 'orgDeleted', payload: { orgId: org } });
      });
    });
  });
}
