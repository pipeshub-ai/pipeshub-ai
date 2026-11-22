/**
 * Properties of the identity repositories that only a real PostgreSQL behind PgBouncer shows: nothing crosses an
 * organisation, racing sign-ins and creations settle
 * the way the contract says, and every statement carries a tag.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { asAccount } from '../../src/libs/context/orgContext';
import { BadRequestError, InternalServerError } from '../../src/libs/errors/http.errors';
import { UniqueConstraintError } from '../../src/libs/repositories/errors';
import { setIdentityPort } from '../../src/libs/repositories/identity';
import { PgGroupRepository } from '../../src/libs/repositories/group/pg';
import { PgIdentityPort } from '../../src/libs/repositories/identity/pg';
import { PgOrgAuthConfigRepository } from '../../src/libs/repositories/org-auth-config/pg';
import { PgOrgRepository } from '../../src/libs/repositories/org/pg';
import { PgResetLinkRepository } from '../../src/libs/repositories/reset-link/pg';
import { PgUnitOfWork } from '../../src/libs/repositories/pg';
import { PgUserRepository } from '../../src/libs/repositories/user/pg';
import { setUserRepository } from '../../src/libs/repositories/user';
import {
  MAX_ORG_ADMINS,
  createUserEnsuringAdminCap,
  saveUserEnsuringAdminCap,
  saveUserEnsuringOrgRetainsAdmin,
} from '../../src/modules/user_management/services/user-admin.service';
import { createOrgLikeSignUp } from './contract/rest.contract';
import { adminInviter, withoutSideStores } from './admin-invite-race';
import { deleteRace, gate, waitUntil, type DeleteOutcome, type DeleteRace } from './admin-delete-race';
import { USER_PROMOTED_DURING_DELETE } from '../../src/modules/user_management/controller/users.controller';
import type { UserRepository } from '../../src/libs/repositories/user/types';
import { IdentityWorld } from './pg-identity-harness';
import {
  OrgLedger,
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  startPgStack,
} from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;
const sha = (s: string): string => s.padEnd(64, '0');

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(
    `PostgreSQL identity properties (${bin}) behind PgBouncer`,
    function () {
      this.timeout(180_000);
      let stack: PgStack;
      const world = new IdentityWorld(undefined as never, new OrgLedger());
      const port = new PgIdentityPort();
      const users = new PgUserRepository();
      const groups = new PgGroupRepository();
      const orgs = new PgOrgRepository();
      const count = async (
        sql: string,
        args: unknown[] = [],
      ): Promise<number> =>
        stack.admin(async (c) => Number((await c.query(sql, args)).rows[0].n));
      let n = 0;
      const email = (p = 'p'): string =>
        `${p}${process.pid}x${Date.now()}x${n++}@example.com`;
      /** A live account made as the organisation's administrator would. */
      const account = async (
        org: string,
        extra: Record<string, unknown> = {},
      ) =>
        inOrg(
          org,
          () =>
            users.create(org, { email: email(), fullName: 'Person', ...extra }),
          await world.adminOf(org),
        );

      before(async () => {
        stack = await startPgStack(bin, 'identity_checks');
        world.stack = stack;
        setIdentityPort(port);
      });
      after(async () => {
        setIdentityPort();
        await stack?.stop();
      });

      it('the sign-in methods removed from a policy are read back from its row', async () => {
        const org = await world.newOrg();
        const repo = new PgOrgAuthConfigRepository();
        await inOrg(org, () => repo.createDefault(org, 'example.com'));
        expect(
          await inOrg(org, () => repo.removedSignInMethods(org)),
        ).to.deep.equal([]);
        await stack.admin((c) =>
          c.query(
            `UPDATE identity.org_auth_config SET removed_sign_in_methods = '{samlSso,oauth}' WHERE org_id = $1`,
            [org],
          ),
        );
        expect(
          await inOrg(org, () => repo.removedSignInMethods(org)),
        ).to.deep.equal(['samlSso', 'oauth']);
        expect(
          await inOrg(await world.newOrg(), () =>
            repo.removedSignInMethods(org),
          ).catch(() => 'refused'),
        ).to.equal('refused');
      });

      it('a profile write sent while a removal holds the account waits for it, then writes nothing and answers null', async () => {
        const org = await world.newOrg();
        const u = await account(org);
        const held = gate();
        const commit = gate();
        // A superuser transaction stands in for the removal: the delete's single UPDATE cannot be held open from outside.
        const removal = stack.admin(async (c) => {
          await c.query('BEGIN');
          await c.query('UPDATE identity.users SET is_deleted = true WHERE id = $1', [u._id]);
          held.open();
          await commit.opened;
          await c.query('COMMIT');
        });
        await held.opened;
        const write = inOrg(org, () => users.updateProfile(org, u._id, { role: 'admin', fullName: 'Late Name' }), await world.adminOf(org));
        try {
          await waitUntil('the profile write waits on the removal\'s row lock', async () =>
            (await count(
              "SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query LIKE '%identity.user_lock_live%'",
            )) > 0);
        } finally {
          commit.open();
          await removal;
        }
        expect(await write).to.equal(null);
        const row = await inOrg(org, () => users.getAny(org, u._id));
        expect(row).to.include({ role: 'member', fullName: 'Person', isDeleted: true });
      });

      it('an account of one organisation is invisible and untouchable from another, and a call naming another organisation fails first', async () => {
        const [a, b] = [await world.newOrg(), await world.newOrg()];
        const inB = await account(b);
        const groupB = await inOrg(b, () =>
          groups.create(b, { name: 'secret', type: 'custom' }),
        );
        await inOrg(b, () => groups.addMembers(b, [groupB._id], [inB._id]));
        await inOrg(a, async () => {
          expect(await users.getLive(a, inB._id)).to.equal(null);
          expect(await users.getAny(a, inB._id)).to.equal(null);
          expect(await users.getLiveById(inB._id)).to.equal(null);
          expect(
            await users.updateProfile(a, inB._id, { fullName: 'taken over' }),
          ).to.equal(null);
          expect(await users.markDeleted(a, inB._id)).to.equal(null);
          expect(
            await users.listFieldsByIds([inB._id], ['email']),
          ).to.deep.equal([]);
          expect(await groups.get(a, groupB._id)).to.equal(null);
          expect(await groups.softDelete(a, groupB._id)).to.equal(null);
          expect(await groups.addMembers(a, [groupB._id], [inB._id])).to.equal(
            0,
          );
          expect(await port.find(inB._id, a)).to.equal(null);
          for (const call of [
            () => users.getLive(b, inB._id),
            () => users.create(b, { email: email() }),
            () => groups.list(b, { skip: 0, limit: 5 }),
            () => orgs.getLive(b),
            () => port.find(inB._id, b),
          ]) {
            let err: unknown;
            await call().catch((e) => (err = e));
            expect(err).to.be.instanceOf(InternalServerError);
          }
        });
        expect(
          await count(
            "SELECT count(*) AS n FROM identity.users WHERE org_id = $1 AND id = $2 AND NOT is_deleted AND full_name = 'Person'",
            [b, inB._id],
          ),
        ).to.equal(1);
        expect(
          await count(
            'SELECT count(*) AS n FROM identity.user_groups WHERE org_id = $1 AND NOT is_deleted',
            [b],
          ),
        ).to.equal(1);
      });

      it('racing sign-in attempts: at most five are counted; one request spends a code; one claims a link', async () => {
        const org = await world.newOrg();
        const u = await account(org);
        await inOrg(
          org,
          () => port.savePassword(u._id, org, 'hash-value', '10.0.0.1'),
          u._id,
        );
        const attempts = await Promise.all(
          Array.from({ length: 20 }, () =>
            inOrg(org, () => port.reserveAttempt(u._id, org, 5), u._id),
          ),
        );
        expect(attempts.filter((x) => x !== null)).to.have.length(5);
        expect(
          (await inOrg(org, () => port.find(u._id, org), u._id))!
            .wrongCredentialCount,
        ).to.equal(5);
        await inOrg(
          org,
          () =>
            port.saveOtp(u._id, org, {
              hashedOTP: 'otp-hash-value',
              otpValidity: Date.now() + 60_000,
              ipAddress: '10.0.0.1',
            }),
          u._id,
        );
        const spent = await Promise.all(
          Array.from({ length: 8 }, () =>
            inOrg(
              org,
              () => port.claimOtp(u._id, org, 'otp-hash-value'),
              u._id,
            ),
          ),
        );
        expect(spent.filter((x) => x !== null)).to.have.length(1);
        const links = new PgResetLinkRepository();
        const claims = await Promise.all(
          Array.from({ length: 8 }, () =>
            inOrg(org, () =>
              links.claim({
                linkHash: sha('link'),
                userId: u._id,
                orgId: org,
                expiresAt: new Date(Date.now() + 60_000),
              }),
            ),
          ),
        );
        expect(claims.filter(Boolean)).to.have.length(1);
      });

      it('concurrent demotions of three administrators: the organisation row lock leaves one', async () => {
        const org = await world.newOrg();
        const [x, y, z] = [
          await account(org, { role: 'admin' }),
          await account(org, { role: 'admin' }),
          { _id: await world.adminOf(org) },
        ];
        const uow = new PgUnitOfWork();
        const demote = (id: string) =>
          inOrg(org, () =>
            uow.run(async (tx) => {
              await users.lockOrgAdminChanges(org, tx);
              if ((await users.countActiveAdmins(org, tx)) <= 1)
                throw new Error('last administrator');
              await users.updateProfile(org, id, { role: 'member' }, tx);
            }),
          );
        const done = await Promise.allSettled([
          demote(x._id),
          demote(y._id),
          demote(z._id),
        ]);
        expect(done.filter((d) => d.status === 'fulfilled')).to.have.length(2);
        expect(await inOrg(org, () => users.countActiveAdmins(org))).to.equal(
          1,
        );
      });

      describe('the admin limit, through the services that enforce it', () => {
        const ROUNDS = 5;
        let savedDocStore: string | undefined;
        let restoreSideStores: () => void;
        before(() => {
          restoreSideStores = withoutSideStores();
          // The services pick the unit of work and the repository from DOC_STORE.
          savedDocStore = process.env.DOC_STORE;
          process.env.DOC_STORE = 'postgres';
          setUserRepository(users);
        });
        after(() => {
          restoreSideStores();
          setUserRepository();
          if (savedDocStore === undefined) delete process.env.DOC_STORE;
          else process.env.DOC_STORE = savedDocStore;
        });

        /** An organisation one seat short of the limit, and its first administrator. */
        const orgWithOneSeat = async (): Promise<{ org: string; actor: string }> => {
          const org = await world.newOrg();
          const actor = await world.adminOf(org);
          for (let i = 1; i < MAX_ORG_ADMINS - 1; i += 1) await account(org, { role: 'admin' });
          expect(await inOrg(org, () => users.countActiveAdmins(org))).to.equal(MAX_ORG_ADMINS - 1);
          return { org, actor };
        };
        const newAdmin = () => ({ email: email('adm'), fullName: 'New Admin', role: 'admin' as const });
        const settle = async (runs: Array<Promise<unknown>>) => {
          const done = await Promise.allSettled(runs);
          for (const d of done) {
            if (d.status === 'rejected') expect(d.reason).to.be.instanceOf(BadRequestError);
          }
          return done.filter((d) => d.status === 'fulfilled').length;
        };

        it('two admin creates for the last seat: the organisation row lock lets exactly one in', async () => {
          for (let round = 0; round < ROUNDS; round += 1) {
            const { org, actor } = await orgWithOneSeat();
            const [a, b] = [newAdmin(), newAdmin()];
            const ok = await settle([a, b].map((f) => inOrg(org, () => createUserEnsuringAdminCap(org, f, false), actor)));
            expect(ok, `round ${round}`).to.equal(1);
            expect(await inOrg(org, () => users.countActiveAdmins(org))).to.equal(MAX_ORG_ADMINS);
            expect(
              await count('SELECT count(*) AS n FROM identity.users WHERE email = ANY($1)', [[a.email, b.email]]),
            ).to.equal(1);
          }
        });

        it('an admin create racing a promotion for the last seat: exactly one wins', async () => {
          for (let round = 0; round < ROUNDS; round += 1) {
            const { org, actor } = await orgWithOneSeat();
            const member = await account(org);
            const ok = await settle([
              inOrg(org, () => createUserEnsuringAdminCap(org, newAdmin(), false), actor),
              inOrg(org, () => saveUserEnsuringAdminCap(org, member._id, { role: 'admin' }, false), actor),
            ]);
            expect(ok, `round ${round}`).to.equal(1);
            expect(await inOrg(org, () => users.countActiveAdmins(org))).to.equal(MAX_ORG_ADMINS);
          }
        });

        it('two bulk admin invites for the last seats: exactly one batch wins, and the other leaves no trace', async () => {
          for (let round = 0; round < ROUNDS; round += 1) {
            const org = await world.newOrg();
            const actor = await world.adminOf(org);
            await account(org, { role: 'admin' });
            // Each batch makes three admins: a removed member, a pending member and a new address. Three seats are free.
            const batch = async () => ({
              removed: await account(org, { isDeleted: true }),
              pending: await account(org),
              fresh: email('fresh'),
            });
            const [a, b] = [await batch(), await batch()];
            const invite = adminInviter(false);
            const done = await Promise.allSettled(
              [a, b].map((x) => inOrg(org, () => invite(org, [x.removed.email, x.pending.email, x.fresh]), actor)),
            );
            for (const d of done) if (d.status === 'rejected') expect(d.reason).to.be.instanceOf(BadRequestError);
            expect(done.filter((d) => d.status === 'fulfilled'), `round ${round}`).to.have.length(1);
            expect(await inOrg(org, () => users.countActiveAdmins(org))).to.equal(MAX_ORG_ADMINS);
            const loser = done[0].status === 'rejected' ? a : b;
            const removed = await inOrg(org, () => users.getAny(org, loser.removed._id));
            const pending = await inOrg(org, () => users.getAny(org, loser.pending._id));
            expect([removed?.isDeleted, removed?.role, pending?.role]).to.deep.equal([true, 'member', 'member']);
            expect(await count('SELECT count(*) AS n FROM identity.users WHERE email = $1', [loser.fresh])).to.equal(0);
          }
        });

        // Every PostgreSQL user insert locks the org's slug counter until commit, which serialises two batches that
        // insert on its own. Batches of removed and pending accounts insert nothing, so only the org lock orders them.
        it('two bulk admin invites of removed and pending accounts only: the organisation row lock lets exactly one batch in', async () => {
          for (let round = 0; round < ROUNDS; round += 1) {
            const org = await world.newOrg();
            const actor = await world.adminOf(org);
            await account(org, { role: 'admin' });
            // Each batch makes two admins; three seats are free.
            const batch = async () => ({ removed: await account(org, { isDeleted: true }), pending: await account(org) });
            const [a, b] = [await batch(), await batch()];
            const invite = adminInviter(false);
            const done = await Promise.allSettled(
              [a, b].map((x) => inOrg(org, () => invite(org, [x.removed.email, x.pending.email]), actor)),
            );
            for (const d of done) if (d.status === 'rejected') expect(d.reason).to.be.instanceOf(BadRequestError);
            expect(done.filter((d) => d.status === 'fulfilled'), `round ${round}`).to.have.length(1);
            expect(await inOrg(org, () => users.countActiveAdmins(org))).to.equal(4);
            const loser = done[0].status === 'rejected' ? a : b;
            const removed = await inOrg(org, () => users.getAny(org, loser.removed._id));
            const pending = await inOrg(org, () => users.getAny(org, loser.pending._id));
            expect([removed?.isDeleted, removed?.role, pending?.role]).to.deep.equal([true, 'member', 'member']);
          }
        });

        it('a taken address inside the locked unit is still a unique-key error on email', async () => {
          const { org, actor } = await orgWithOneSeat();
          const taken = (await account(org)).email;
          let caught: unknown;
          try {
            await inOrg(org, () => createUserEnsuringAdminCap(org, { ...newAdmin(), email: taken }, false), actor);
          } catch (error) {
            caught = error;
          }
          expect(caught).to.be.instanceOf(UniqueConstraintError);
          expect(String((caught as UniqueConstraintError).metadata?.key)).to.include('email');
        });
      });

      describe('deleting a member who is made an admin meanwhile', () => {
        let race: DeleteRace;
        let savedDocStore: string | undefined;
        before(() => {
          savedDocStore = process.env.DOC_STORE;
          process.env.DOC_STORE = 'postgres';
          setUserRepository(users);
          race = deleteRace(false);
        });
        afterEach(() => setUserRepository(users));
        after(() => {
          race.restore();
          setUserRepository();
          if (savedDocStore === undefined) delete process.env.DOC_STORE;
          else process.env.DOC_STORE = savedDocStore;
        });

        const expectKeptAsAdmin = async (org: string, u: string, a: string, outcome: DeleteOutcome) => {
          expect(outcome.status).to.equal('refused');
          const error = (outcome as { error: Error }).error;
          expect(error).to.be.instanceOf(BadRequestError);
          expect(error.message).to.equal(USER_PROMOTED_DURING_DELETE);
          expect(await inOrg(org, () => users.getAny(org, u))).to.include({ role: 'admin', isDeleted: false });
          expect(await inOrg(org, () => users.getAny(org, a))).to.include({ role: 'member' });
          expect(await inOrg(org, () => users.countActiveAdmins(org))).to.equal(1);
        };

        it('a delete that checked a member, then saw them promoted and the other admin demoted, keeps them: the org keeps its admin', async () => {
          const org = await world.newOrg();
          const a = await world.adminOf(org);
          const u = (await account(org))._id;
          const del = inOrg(org, () => race.start(org, a, u), a);
          await del.parked;
          await inOrg(org, () => saveUserEnsuringAdminCap(org, u, { role: 'admin' }, false), a);
          await inOrg(org, () => saveUserEnsuringOrgRetainsAdmin(org, a, { role: 'member' }, false), a);
          del.release();
          await expectKeptAsAdmin(org, u, a, await del.done);
        });

        it('a removal sent while the promotion holds the row lock waits for it, then keeps the new admin', async () => {
          const org = await world.newOrg();
          const a = await world.adminOf(org);
          const u = (await account(org))._id;
          const written = gate();
          const commit = gate();
          setUserRepository(new Proxy(users, {
            get: (t, k: keyof UserRepository) => (k === 'updateProfile'
              ? async (...args: Parameters<UserRepository['updateProfile']>) => {
                const saved = await users.updateProfile(...args);
                written.open();
                await commit.opened; // still inside the unit, holding the row lock
                return saved;
              }
              : (t[k] as (...x: unknown[]) => unknown).bind(t)),
          }) as UserRepository);
          const del = inOrg(org, () => race.start(org, a, u), a);
          await del.parked;
          const promotion = inOrg(org, () => saveUserEnsuringAdminCap(org, u, { role: 'admin' }, false), a);
          let settled = false;
          try {
            await written.opened;
            del.release();
            void del.done.then(() => { settled = true; });
            await waitUntil('the removal waits on the row lock', async () =>
              (await count(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query LIKE '%identity.user_mark_deleted%'",
              )) > 0);
            expect(settled, 'the removal finished before the promotion committed').to.equal(false);
          } finally {
            commit.open();
          }
          await promotion;
          setUserRepository(users);
          await inOrg(org, () => saveUserEnsuringOrgRetainsAdmin(org, a, { role: 'member' }, false), a);
          await expectKeptAsAdmin(org, u, a, await del.done);
        });
      });

      it('creating an organisation is atomic: a failing unit leaves nothing behind, and the first password is stored with it', async () => {
        const tables = [
          'orgs',
          'users',
          'user_groups',
          'org_auth_config',
          'user_credentials',
        ];
        const total = async (): Promise<number[]> =>
          Promise.all(
            tables.map((t) => count(`SELECT count(*) AS n FROM identity.${t}`)),
          );
        const input = (adminEmail = email('a')) => ({
          org: {
            registeredName: 'Atomic',
            domain: 'atomic.example.com',
            contactEmail: email('o'),
            accountType: 'business',
          },
          admin: { fullName: 'A', email: adminEmail },
          passwordHash: 'hash-value-1',
          ipAddress: '10.0.0.9',
        });
        const fx = {
          orgs,
          uow: new PgUnitOfWork(),
          credentials: port,
          asNewOrg: <T>(
            ids: { orgId: string; adminUserId: string },
            fn: () => Promise<T>,
          ) => asAccount(ids.orgId, ids.adminUserId, 'org-create', fn),
        } as never;
        const before = await total();
        await createOrgLikeSignUp(fx, input(), async () => {
          throw new Error('boom');
        }).catch(() => undefined);
        expect(await total()).to.deep.equal(before);
        const made = await createOrgLikeSignUp(fx, input());
        expect(await total()).to.deep.equal(
          before.map((n, i) => n + (i === 2 ? 2 : 1)),
        ); // two default groups
        const hash = await stack.admin(
          async (c) =>
            (
              await c.query(
                'SELECT password_hash FROM identity.user_credentials WHERE org_id = $1',
                [made.org._id],
              )
            ).rows[0]?.password_hash,
        );
        expect(hash).to.equal('hash-value-1');
      });

      it('an address held by another account of the organisation is taken, and an address change cannot move onto it', async () => {
        const org = await world.newOrg();
        const mine = await account(org);
        const theirs = await account(org);
        const adminOrg = await world.adminOf(org);
        await inOrg(
          org,
          async () => {
            expect(
              await users.emailTakenElsewhere(theirs.email, mine._id),
            ).to.equal(true);
            expect(await users.emailTakenElsewhere(email(), mine._id)).to.equal(
              false,
            );
          },
          adminOrg,
        );
        let err: unknown;
        await inOrg(
          org,
          () => users.changeEmail(mine._id, theirs.email),
          mine._id,
        ).catch((e) => (err = e));
        expect(err).to.be.instanceOf(UniqueConstraintError);
        expect(
          (await inOrg(org, () => users.getLive(org, mine._id), adminOrg))
            ?.email,
        ).to.equal(mine.email);
      });

      it('removing an account and dropping its password are one unit: a failure between them undoes both', async () => {
        const org = await world.newOrg();
        const admin = await world.adminOf(org);
        const victim = await account(org);
        await inOrg(
          org,
          () =>
            port.savePassword(victim._id, org, 'hash-of-victim-1', '10.0.0.1'),
          victim._id,
        );
        await inOrg(
          org,
          () =>
            new PgUnitOfWork().run(async () => {
              await users.markDeleted(org, victim._id, { signedOut: true });
              await port.clearPassword(victim._id, victim.email);
              throw new Error('boom');
            }),
          admin,
        ).catch(() => undefined);
        expect(
          (await inOrg(org, () => users.getLive(org, victim._id), admin))?._id,
        ).to.equal(victim._id);
        expect(
          await count(
            'SELECT count(*) AS n FROM identity.user_credentials WHERE org_id = $1 AND user_id = $2 AND password_hash IS NOT NULL',
            [org, victim._id],
          ),
        ).to.equal(1);
        await inOrg(
          org,
          () =>
            new PgUnitOfWork().run(async () => {
              await users.markDeleted(org, victim._id, { signedOut: true });
              await port.clearPassword(victim._id, victim.email);
            }),
          admin,
        );
        expect(
          await inOrg(org, () => users.getLive(org, victim._id), admin),
        ).to.equal(null);
        expect(
          await count(
            'SELECT count(*) AS n FROM identity.user_credentials WHERE org_id = $1 AND user_id = $2 AND password_hash IS NOT NULL',
            [org, victim._id],
          ),
        ).to.equal(0);
      });

      it('a creation that rolls back gives its slug number back; concurrent organisations get distinct slugs', async () => {
        const org = await world.newOrg();
        const admin = await world.adminOf(org);
        const first = await account(org);
        await inOrg(
          org,
          () =>
            new PgUnitOfWork().run(async () => {
              await users.create(org, { email: email() });
              throw new Error('boom');
            }),
          admin,
        ).catch(() => undefined);
        const second = await account(org);
        const num = (s?: string): number => Number((s ?? '').split('-').pop());
        expect(num(second.slug)).to.equal(num(first.slug) + 1);

        const fx = {
          orgs,
          uow: new PgUnitOfWork(),
          credentials: port,
          asNewOrg: <T>(
            ids: { orgId: string; adminUserId: string },
            fn: () => Promise<T>,
          ) => asAccount(ids.orgId, ids.adminUserId, 'org-create', fn),
        } as never;
        const mk = () =>
          createOrgLikeSignUp(fx, {
            org: {
              registeredName: 'Race',
              domain: 'race.example.com',
              contactEmail: email('o'),
              accountType: 'business',
            },
            admin: { fullName: 'A', email: email('a') },
            passwordHash: 'hash-value-1',
            ipAddress: '10.0.0.9',
          });
        const made = await Promise.all([mk(), mk(), mk()]);
        expect(new Set(made.map((m) => m.org.slug)).size).to.equal(3);
        expect(await orgs.getFirstLive()).to.not.equal(undefined);
      });

      it('every statement the repositories sent carries a tag', () => {
        expect(
          [...stack.catalog.untagged].filter((sql) => /identity\./.test(sql)),
        ).to.deep.equal([]);
        expect(
          stack.catalog
            .toJSON()
            .statements.filter((s) => s.tag.startsWith('identity.')).length,
        ).to.be.greaterThan(15);
      });
    },
  );
}
