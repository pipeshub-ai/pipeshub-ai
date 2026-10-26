/**
 * The identity repositories on PostgreSQL (Alembic head, row-level security, through PgBouncer): the same contract suites the
 * Mongo stores meet.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { asAccount } from '../../src/libs/context/orgContext';
import { PgUnitOfWork } from '../../src/libs/repositories/pg';
import { PgActivityRepository } from '../../src/libs/repositories/activity/pg';
import { PgGroupRepository } from '../../src/libs/repositories/group/pg';
import { setIdentityPort } from '../../src/libs/repositories/identity';
import { PgIdentityPort } from '../../src/libs/repositories/identity/pg';
import { PgOrgAuthConfigRepository } from '../../src/libs/repositories/org-auth-config/pg';
import { PgOrgMetricsJobRepository } from '../../src/libs/repositories/org-metrics-job/pg';
import { PgOrgRepository } from '../../src/libs/repositories/org/pg';
import { PgPictureRepository } from '../../src/libs/repositories/picture/pg';
import { PgResetLinkRepository } from '../../src/libs/repositories/reset-link/pg';
import { PgUserRepository } from '../../src/libs/repositories/user/pg';
import { describeIdentityContract } from './contract/identity.contract';
import { describeLookupContract, describeMetricsContract, describeOrgContract } from './contract/rest.contract';
import {
  ACTIVITY_SPEC,
  AUTH_CONFIG_SPEC,
  GROUP_SPEC,
  IDENTITY_SPEC,
  IdentityWorld,
  ORG_SPEC,
  PICTURE_SPEC,
  USER_SPEC,
} from './pg-identity-harness';
import { OrgLedger, PG_BINS, PgStack, haveStack, newId, scopedUow, startPgStack } from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL identity stores (${bin}) behind PgBouncer`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    const world = new IdentityWorld(undefined as never, new OrgLedger());
    const port = new PgIdentityPort();

    before(async () => {
      stack = await startPgStack(bin, 'identity_stores', { jobs: true });
      process.env.PG_JOBS_URL = 'in-test'; // the metrics reads need to know a jobs pool exists; the stack gave the runtime one
      world.stack = stack;
      setIdentityPort(port); // the repositories reach the port through the registry
    });
    after(async () => {
      delete process.env.PG_JOBS_URL;
      setIdentityPort();
      await stack?.stop();
    });

    describe('identity repositories contract', () => {
      describeIdentityContract({
        users: world.scoped(new PgUserRepository(), USER_SPEC),
        groups: world.scoped(new PgGroupRepository(), GROUP_SPEC),
        credentials: world.scoped(port, IDENTITY_SPEC),
        identity: world.scoped(port, IDENTITY_SPEC),
        activities: world.scoped(new PgActivityRepository(), ACTIVITY_SPEC),
        orgAuth: world.scoped(new PgOrgAuthConfigRepository(), AUTH_CONFIG_SPEC),
        resetLinks: world.scoped(new PgResetLinkRepository()),
        pictures: world.scoped(new PgPictureRepository(), PICTURE_SPEC),
        uow: scopedUow(),
        transactional: true,
        newOrg: () => world.newOrg(),
        newId,
        clearOrgAuth: () => world.clearOrgs(),
      });
    });

    const orgFixture = () => ({
      orgs: world.scoped(new PgOrgRepository(), ORG_SPEC),
      metrics: new PgOrgMetricsJobRepository(),
      users: world.scoped(new PgUserRepository(), USER_SPEC),
      groups: world.scoped(new PgGroupRepository(), GROUP_SPEC),
      credentials: world.scoped(port, IDENTITY_SPEC),
      orgAuth: world.scoped(new PgOrgAuthConfigRepository(), AUTH_CONFIG_SPEC),
      uow: new PgUnitOfWork(),
      transactional: true,
      newId,
      asNewOrg: <T>(ids: { orgId: string; adminUserId: string }, fn: () => Promise<T>): Promise<T> =>
        asAccount(ids.orgId, ids.adminUserId, 'org-create', fn).then((r) => {
          world.ledger.learn({ orgId: ids.orgId, adminUserId: ids.adminUserId });
          return r;
        }),
    });

    describe('organisation contract', () => {
      describeOrgContract(orgFixture() as never);
    });
    describe('user and group lookups contract', () => {
      describeLookupContract(orgFixture() as never);
    });
    describe('organisation metrics contract', () => {
      describeMetricsContract(orgFixture() as never);
    });
  });
}
