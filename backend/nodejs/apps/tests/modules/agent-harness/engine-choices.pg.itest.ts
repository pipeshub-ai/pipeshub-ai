/**
 * SIGNIN's engine sign-ins and ENGPICK's coding-engine choices on PostgreSQL: the contracts the in-memory and Mongo
 * stores pass, against main's identity rows on PostgreSQL, and the PostgreSQL cases: the records are harness rows, never
 * on the user row; a removed person or conversation is never written and reads as none; another org reads nothing.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgCodingEnginePreferences } from '../../../src/libs/repositories/agent-harness/coding-engine-preferences/pg';
import { PgEngineSignIns } from '../../../src/libs/repositories/agent-harness/engine-signin/pg';
import { getConversationRepository } from '../../../src/libs/repositories/conversation';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { describeCodingEnginePreferencesContract } from './support/coding-engine-preferences-contract';
import { describeEngineSignInStoreContract } from './support/engine-signin-store-contract';
import {
  PgStack,
  inOrg,
  pgBins,
  pgSuite,
  scopedStore,
  startHarnessPg,
} from './support/harness-pg';
import {
  pgOrg,
  pgUser,
  setUserDeleted,
  usePgIdentityRepositories,
} from './support/harness-pg-identity';

for (const bin of pgBins()) {
  pgSuite(
    `agent harness engine sign-ins and choices on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      let restore: () => void;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_engine_choices');
        restore = usePgIdentityRepositories();
      });
      after(async () => {
        restore?.();
        await stack?.stop();
      });

      const ORG_ARGS = {
        read: 0,
        write: 0,
        signedInSince: 0,
        orgAllows: 0,
        setOrgAllows: 0,
        eraseUser: 0,
        eraseOrg: 0,
        eraseSession: 0,
        writeUser: 0,
        writeOrg: 0,
        writeOrgAutoRoute: 0,
        readSession: 0,
        writeSession: 0,
        readSessionPin: 0,
        writeSessionPin: 0,
        readSessionRepository: 0,
        writeSessionRepository: 0,
        memoryOff: 0,
        writeMemoryOff: 0,
      };

      const conversation = async (
        orgId: string,
        userId: string,
      ): Promise<string> =>
        (
          await inOrg(orgId, () =>
            getConversationRepository().createSession({
              orgId,
              userId,
              initiator: userId,
              lastActivityAt: Date.now(),
              status: 'Complete',
              sessionType: 'chat',
            }),
          )
        )._id;

      describeEngineSignInStoreContract('PgEngineSignIns', async () => {
        const orgId = await pgOrg(stack);
        const other = await pgOrg(stack);
        return {
          store: scopedStore(new PgEngineSignIns(), ORG_ARGS),
          orgId,
          users: [await pgUser(stack, orgId), await pgUser(stack, orgId)],
          serviceAccount: await pgUser(stack, orgId, { kind: 'service' }),
          stranger: { orgId: other, userId: await pgUser(stack, other) },
        };
      });

      describeCodingEnginePreferencesContract(
        "harness rows beside main's identity rows",
        async () => {
          const orgId = await pgOrg(stack);
          const other = await pgOrg(stack);
          const users: [string, string] = [
            await pgUser(stack, orgId),
            await pgUser(stack, orgId),
          ];
          return {
            store: scopedStore(new PgCodingEnginePreferences(), ORG_ARGS),
            orgId,
            users,
            stranger: { orgId: other, userId: await pgUser(stack, other) },
            session: () => conversation(orgId, users[0]),
          };
        },
      );

      describe('PostgreSQL engine sign-in and choice invariants', () => {
        it('ES-P1: the record is a harness row, never on the user row; a deleted user is not found', async () => {
          const store = scopedStore(new PgEngineSignIns(), ORG_ARGS);
          const orgId = await pgOrg(stack);
          const me = await pgUser(stack, orgId);
          await store.write(orgId, me, {
            enabled: true,
            email: 'ada@example.com',
            plan: 'team',
          });
          const userRow = await stack.admin((c) =>
            c.query(
              `SELECT * FROM identity.users WHERE org_id = $1 AND id = $2`,
              [orgId, me],
            ),
          );
          expect(JSON.stringify(userRow.rows[0])).not.to.include(
            'ada@example.com',
          );
          const rows = await stack.admin((c) =>
            c.query(
              `SELECT count(*)::int AS n FROM harness.engine_signins WHERE org_id = $1`,
              [orgId],
            ),
          );
          expect(rows.rows[0].n).to.equal(1);
          await setUserDeleted(stack, orgId, me);
          expect(await store.read(orgId, me)).to.equal(null);
        });

        it("another organisation bound to the unit can neither read nor write this org's records", async () => {
          const raw = new PgEngineSignIns();
          const orgId = await pgOrg(stack);
          const me = await pgUser(stack, orgId);
          await inOrg(orgId, () =>
            raw.write(orgId, me, {
              enabled: true,
              email: 'a@x.example',
              plan: 'team',
            }),
          );
          const theirs = await pgOrg(stack);
          expect(
            await inOrg(theirs, () =>
              raw.signedInSince(orgId, 0).catch((e: Error) => e),
            ),
          ).to.be.instanceOf(Error);
          expect(
            await inOrg(theirs, () =>
              raw.setOrgAllows(orgId, true).catch((e: Error) => e),
            ),
          ).to.be.instanceOf(Error);
          expect(await inOrg(orgId, () => raw.orgAllows(orgId))).to.equal(
            false,
          );
        });

        it('CP-P1: a user of another org, or a deleted user, is never written; the choices are harness rows', async () => {
          const store = scopedStore(new PgCodingEnginePreferences(), ORG_ARGS);
          const orgId = await pgOrg(stack);
          const outsider = await pgUser(stack, await pgOrg(stack));
          expect(
            await store
              .writeUser(orgId, outsider, 'codex')
              .catch((e: unknown) => e),
          ).to.be.instanceOf(HarnessNotFoundError);
          const me = await pgUser(stack, orgId);
          await store.writeUser(orgId, me, 'codex');
          await store.writeOrg(orgId, 'opencode');
          await store.writeOrgAutoRoute(orgId, false);
          const rows = await stack.admin((c) =>
            c.query(
              `SELECT subject, doc FROM harness.personal_choices WHERE org_id = $1 ORDER BY subject`,
              [orgId],
            ),
          );
          expect(rows.rows).to.deep.equal([
            {
              subject: 'org',
              doc: {
                defaultCodingEngine: 'opencode',
                codingEngineAutoRoute: false,
              },
            },
            { subject: `user:${me}`, doc: { codingEngine: 'codex' } },
          ]);
          await setUserDeleted(stack, orgId, me);
          expect(
            await store.writeUser(orgId, me, 'codex').catch((e: unknown) => e),
          ).to.be.instanceOf(HarnessNotFoundError);
          expect((await store.read(orgId, me)).user).to.equal(null);
          expect(
            await store
              .writeOrg(await pgOrg(stack, { deleted: true }), 'codex')
              .catch((e: unknown) => e),
          ).to.be.instanceOf(HarnessNotFoundError);
        });

        it("CP-P3 (WP114-FU-ERASE): a deleted person's and conversation's rows leave the table; the org's stays", async () => {
          const store = scopedStore(new PgCodingEnginePreferences(), ORG_ARGS);
          const orgId = await pgOrg(stack);
          const me = await pgUser(stack, orgId);
          const chat = await conversation(orgId, me);
          await store.writeUser(orgId, me, 'codex');
          await store.writeOrg(orgId, 'opencode');
          await store.writeSession(orgId, chat, 'codex');
          await setUserDeleted(stack, orgId, me);
          expect(await store.eraseUser(orgId, me)).to.equal(1);
          expect(await store.eraseSession(orgId, chat)).to.equal(1);
          const rows = await stack.admin((c) =>
            c.query(
              `SELECT subject FROM harness.personal_choices WHERE org_id = $1`,
              [orgId],
            ),
          );
          expect(rows.rows).to.deep.equal([{ subject: 'org' }]);
        });

        it("CP-P2: a conversation's choice, pin and repository live on its own row; a missing conversation is never written", async () => {
          const store = scopedStore(new PgCodingEnginePreferences(), ORG_ARGS);
          const orgId = await pgOrg(stack);
          const me = await pgUser(stack, orgId);
          const session = await inOrg(orgId, () =>
            getConversationRepository().createSession({
              orgId,
              userId: me,
              initiator: me,
              lastActivityAt: Date.now(),
              status: 'Complete',
              sessionType: 'chat',
            }),
          );
          const sessionId = session._id;
          await store.writeSession(orgId, sessionId, 'codex');
          await store.writeSessionPin(orgId, sessionId, 'codex@1.2.3');
          await store.writeSessionRepository(orgId, sessionId, {
            url: 'https://git.example/r.git',
            toolsetInstanceId: 'ti-1',
          });
          expect(await store.readSession(orgId, sessionId)).to.equal('codex');
          expect(await store.readSessionPin(orgId, sessionId)).to.equal(
            'codex@1.2.3',
          );
          expect(
            await store.readSessionRepository(orgId, sessionId),
          ).to.deep.equal({
            url: 'https://git.example/r.git',
            toolsetInstanceId: 'ti-1',
          });
          await store.writeSessionPin(orgId, sessionId, null);
          expect(await store.readSessionPin(orgId, sessionId)).to.equal(null);
          expect(await store.readSession(orgId, sessionId)).to.equal('codex');
          const missing = '0123456789abcdef01234567';
          expect(
            await store
              .writeSession(orgId, missing, 'codex')
              .catch((e: unknown) => e),
          ).to.be.instanceOf(HarnessNotFoundError);
          expect(await store.readSession(orgId, missing)).to.equal(null);
        });
      });
    },
  );
}
