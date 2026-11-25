/**
 * The Slack-only account paths on PostgreSQL, with no org context bound when they start, the way a Slack bot's token
 * and a Google or Microsoft sign-in reach them. Every repository call fails closed without a context there, so these
 * are the tests that the code binds the org itself. (The unit tests mock the repositories and cannot see this.)
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { getCurrentOrgId } from '../../src/libs/context/orgContext';
import { AIServiceCommand } from '../../src/libs/commands/ai_service/ai.service.command';
import { setIdentityPort } from '../../src/libs/repositories/identity';
import { PgIdentityPort } from '../../src/libs/repositories/identity/pg';
import { PgActivityRepository } from '../../src/libs/repositories/activity/pg';
import { setActivityRepository } from '../../src/libs/repositories/activity';
import { PgGroupRepository } from '../../src/libs/repositories/group/pg';
import { setGroupRepository } from '../../src/libs/repositories/group';
import { PgOrgRepository } from '../../src/libs/repositories/org/pg';
import { setOrgRepository } from '../../src/libs/repositories/org';
import { PgUserRepository } from '../../src/libs/repositories/user/pg';
import { setUserRepository } from '../../src/libs/repositories/user';
import * as cmController from '../../src/modules/configuration_manager/controller/cm_controller';
import { hydrateScopedRequestAsUser } from '../../src/modules/enterprise_search/utils/scoped-request';
import { JitProvisioningService } from '../../src/modules/auth/services/jit-provisioning.service';
import { IdentityWorld } from './pg-identity-harness';
import { OrgLedger, PG_BINS, PgStack, haveStack, newId, startPgStack } from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`Slack-only accounts on PostgreSQL (${bin}), no org context bound at the entry`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    const world = new IdentityWorld(undefined as never, new OrgLedger());
    const appConfig = { jwtSecret: 'session-secret', scopedJwtSecret: 'scoped-secret', aiBackend: 'http://ai.local' } as never;
    const logger = { info: sinon.stub(), debug: sinon.stub(), warn: sinon.stub(), error: sinon.stub() } as never;
    let orgId: string;

    before(async () => {
      stack = await startPgStack(bin, 'slack_only_accounts', { jobs: true });
      world.stack = stack;
      setIdentityPort(new PgIdentityPort());
      setUserRepository(new PgUserRepository());
      setOrgRepository(new PgOrgRepository());
      setActivityRepository(new PgActivityRepository());
      setGroupRepository(new PgGroupRepository());
    });
    after(async () => {
      setIdentityPort();
      setUserRepository();
      setOrgRepository();
      setActivityRepository();
      setGroupRepository();
      await stack?.stop();
    });
    beforeEach(async () => {
      await world.clearOrgs();
      orgId = await world.newOrg();
      await stack.admin((c) => c.query('DELETE FROM identity.users WHERE org_id = $1', [orgId]).catch(() => undefined));
      sinon.stub(cmController, 'getSlackBotStore').resolves({
        configs: [{ id: 'cfg-1', agentId: 'agent-1', createAccountsForSlackUsers: true }],
      } as never);
      sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: { isServiceAccount: true } } as never);
    });
    afterEach(() => sinon.restore());

    const rows = (email: string) =>
      stack.admin((c) => c.query('SELECT id, kind, is_deleted FROM identity.users WHERE org_id = $1 AND email = $2', [orgId, email]));
    const slackReq = (email: string): never =>
      ({
        headers: { authorization: 'Bearer incoming-slack-token' },
        params: { agentKey: 'agent-1' },
        tokenPayload: {
          email,
          scopes: ['conversation:create'],
          configId: 'cfg-1',
          slackFullMember: true,
          slackUserId: 'U1',
          slackTeamId: 'T1',
          slackFullName: 'Sam',
        },
      }) as never;

    it('a first Slack message creates the account, and the second finds the same one', async () => {
      expect(getCurrentOrgId(), 'nothing is bound at the entry').to.equal(undefined);
      const first: any = slackReq('sam@acme.test');
      await hydrateScopedRequestAsUser(first, appConfig, {} as never);

      const created = await rows('sam@acme.test');
      expect(created.rows).to.have.length(1);
      expect(created.rows[0].kind).to.equal('slack');
      expect(first.user.userId).to.equal(created.rows[0].id);
      expect(first.user.isServiceAccount).to.equal(true);
      const activity = await stack.admin((c) => c.query("SELECT 1 FROM identity.user_activities WHERE org_id = $1 AND email = 'sam@acme.test'", [orgId]));
      expect(activity.rowCount, 'the creation is recorded').to.be.greaterThan(0);

      const second: any = slackReq('sam@acme.test');
      await hydrateScopedRequestAsUser(second, appConfig, {} as never);
      expect(second.user.userId).to.equal(first.user.userId);
      expect((await rows('sam@acme.test')).rows).to.have.length(1);
    });

    it('a full member is answered as themselves', async () => {
      const id = newId();
      await stack.admin((c) =>
        c.query(
          `INSERT INTO identity.users (org_id, id, slug, full_name, email, role, created_at, updated_at) VALUES ($1, $2, $3, 'Mia', 'mia@acme.test', 'member', now(), now())`,
          [orgId, id, `user-${id}`],
        ),
      );
      const req: any = slackReq('mia@acme.test');
      await hydrateScopedRequestAsUser(req, appConfig, {} as never);
      expect(req.user.userId).to.equal(id);
      expect(req.user.isServiceAccount).to.equal(undefined);
    });

    it('a Google or Microsoft sign-in upgrades the Slack-only account, and refuses a switched-off one', async () => {
      const slack: any = slackReq('sam@acme.test');
      await hydrateScopedRequestAsUser(slack, appConfig, {} as never);
      const id = slack.user.userId as string;
      const jit = new JitProvisioningService(logger, { start: async () => undefined, stop: async () => undefined, publishEvent: async () => undefined } as never);

      await jit.upgradeSlackOnlyUser({ _id: id, orgId, email: 'sam@acme.test' }, { fullName: 'Sam' }, 'google');
      expect((await rows('sam@acme.test')).rows[0].kind).to.equal('human');

      // a second Slack-only account that was switched off is left alone
      const other: any = slackReq('kim@acme.test');
      await hydrateScopedRequestAsUser(other, appConfig, {} as never);
      await stack.admin((c) => c.query('UPDATE identity.users SET is_deleted = true WHERE org_id = $1 AND id = $2', [orgId, other.user.userId]));
      let error: unknown;
      try {
        await jit.upgradeSlackOnlyUser({ _id: other.user.userId, orgId, email: 'kim@acme.test' }, { fullName: 'Kim' }, 'microsoft');
      } catch (e) {
        error = e;
      }
      expect(error).to.be.instanceOf(Error);
      expect((await rows('kim@acme.test')).rows[0].kind).to.equal('slack');
    });
  });
}
