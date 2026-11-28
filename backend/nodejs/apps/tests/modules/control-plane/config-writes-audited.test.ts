/**
 * CPD-1 CW: every config write the CPD-1 row names reaches the org's audit
 * chain as one `config.changed` through the control-plane transaction, a
 * refused write records nothing, and no entry carries a value or a secret.
 * Route level where the route has a test harness, service level otherwise.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import { AddressInfo } from 'node:net';
import { Server } from 'node:http';
import { Container } from 'inversify';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { AuthMiddleware } from '../../../src/libs/middlewares/auth.middleware';
import * as userAdminService from '../../../src/modules/user_management/services/user-admin.service';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import { ScheduleService } from '../../../src/modules/agent-harness/application/schedule-service';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { AGENT_HARNESS_CODING_ENGINES } from '../../../src/modules/agent-harness/composition';
import { noEngineCeilings } from '../../../src/modules/agent-harness/ports/engine-profiles';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { createAgentHarnessAdminRouter } from '../../../src/modules/agent-harness/transport/admin-routes';
import { ALICE, FakeActors, FakeAdmission, FakeSetting, ORG as SCHEDULE_ORG, TestClock } from '../agent-harness/support/schedules';
import { auditedControlPlane, InMemoryControlPlaneTransaction, RecordingLog } from './support/in-memory';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { bindAdminAuthorizer } from '../agent-harness/support/admin-authorizer';
import { continueAs } from '../../helpers/request-org-context';

const ORG = 'org-1';

describe('CPD-1 admin routes are audited (CW-10, CW-12)', () => {
  let server: Server;
  let base: string;
  let admin = true;
  const log = new RecordingLog();
  const chain = new InMemoryAuditChainStore();
  const transaction = new InMemoryControlPlaneTransaction({ audit: new ChainConfigAuditLog(chain), log });
  const entries = () => (chain.records.get(ORG) ?? []).filter((record) => record.streamId === `cp:${ORG}`);

  before(() => {
    sinon.stub(userAdminService, 'isUserOrgAdmin').callsFake(() => Promise.resolve(admin));
    const container = new Container();
    container.bind<AuthMiddleware>('AuthMiddleware').toConstantValue({
      authenticate: (req: any, _res: unknown, next: () => void) => {
        req.user = { userId: 'u-admin', orgId: ORG };
        req.tokenPayload = req.user;
        continueAs(req.user, next);
      },
    } as never);
    container.bind(AGENT_HARNESS_CODING_ENGINES).toConstantValue(
      new CodingEngineService({
        ceilings: noEngineCeilings,
        access: { engineRefusals: async () => new Map(), signInsCarried: async () => true },
        preferences: new InMemoryCodingEnginePreferences(),
        transaction,
      }),
    );
    const app = express();
    app.use(express.json());
    bindAdminAuthorizer(container);
    app.use('/admin', createAgentHarnessAdminRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = app.listen(0);
    base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}/admin`;
  });

  after(() => {
    sinon.restore();
    server.close();
  });

  beforeEach(() => {
    admin = true;
  });

  const put = (path: string, body: unknown) =>
    fetch(`${base}${path}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

  it('CW-10: a non-admin and an invalid body record nothing', async () => {
    const before = entries().length;
    admin = false;
    expect((await put('/coding-engine', { autoRoute: false })).status).to.equal(403);
    admin = true;
    expect((await put('/coding-engine', {})).status).to.equal(400);
    expect(entries()).to.have.length(before);
  });

  it('CW-12: one cp.change.committed info line per write, with ids only; no *.configured lines remain', async () => {
    const before = log.lines.length;
    expect((await put('/coding-engine', { autoRoute: false })).status).to.equal(200);
    const lines = log.lines.slice(before);
    expect(lines.map(([level, message]) => [level, message])).to.deep.equal([['info', 'cp.change.committed']]);
    expect(lines[0]![2]).to.include({ orgId: ORG, objectType: 'coding-engine', objectId: 'org/autoRoute' });
  });
});

describe('CPD-1 schedules (CW-7, CW-11)', () => {
  it('CW-7: create, pause, resume and delete of a schedule are four entries on the owner scope; input and webhook token stay out', async () => {
    const audit = auditedControlPlane();
    const clock = new TestClock(Date.parse('2026-07-01T00:00:00.000Z'));
    const store = new InMemoryScheduleStore(clock);
    const actors = new FakeActors();
    const setting = new FakeSetting();
    const firer = new ScheduleFirer({ store, admission: new FakeAdmission(), actors, setting });
    const service = new ScheduleService({
      transaction: audit.transaction,
      store,
      firer,
      actors,
      setting,
      newToken: () => 'pxt_secret_hook',
      newSecret: () => 'pxs_secret_hook',
    });
    const alice = { orgId: SCHEDULE_ORG, userId: ALICE, via: 'session' as const };
    const { schedule } = await service.create(alice, {
      name: 'Digest',
      input: { query: 'SENTINEL-schedule-input' },
      trigger: { kind: 'webhook' },
    });
    await service.pause(alice, schedule.id);
    await service.resume(alice, schedule.id);
    await service.delete(alice, schedule.id);
    const written = audit.configEntries(SCHEDULE_ORG);
    expect(written.map((entry) => [entry.fields['objectType'], entry.fields['objectId'], entry.fields['changed']])).to.deep.equal([
      ['schedule', schedule.id, 'agentKey,agentVersion,askDigest,codingEngine,deadlineS,maxCostUsd,name,notify,overlap,skipNextAt,status,trigger'],
      ['schedule', schedule.id, 'status'],
      ['schedule', schedule.id, 'status'],
      ['schedule', schedule.id, 'agentKey,agentVersion,askDigest,codingEngine,deadlineS,maxCostUsd,name,notify,overlap,skipNextAt,status,trigger'],
    ]);
    expect(written.every((entry) => entry.fields['scope'] === `user:${ALICE}`)).to.equal(true);
    const text = JSON.stringify(written);
    expect(text).to.not.include('SENTINEL-schedule-input');
    expect(text).to.not.include('pxt_secret_hook');
    // TR-04: a signed webhook's key stays out as its token does.
    expect(text).to.not.include('pxs_secret_hook');
  });

  it('CW-7 (ADR-SCH-03): an edit of only the ask and a skip are entries that name what changed and the revisions, never the ask', async () => {
    const audit = auditedControlPlane();
    const clock = new TestClock(Date.parse('2026-07-01T00:00:00.000Z'));
    const store = new InMemoryScheduleStore(clock);
    const actors = new FakeActors();
    const setting = new FakeSetting();
    const firer = new ScheduleFirer({ store, admission: new FakeAdmission(), actors, setting });
    const service = new ScheduleService({ transaction: audit.transaction, store, firer, actors, setting });
    const alice = { orgId: SCHEDULE_ORG, userId: ALICE, via: 'session' as const };
    const { schedule } = await service.create(alice, {
      name: 'Standup',
      input: { query: 'SENTINEL-before' },
      trigger: { kind: 'cron', cron: '0 9 * * 1-5', timeZone: 'UTC' },
    });
    await service.update(alice, schedule.id, { revision: 0, query: 'SENTINEL-after' });
    await service.skipNext(alice, schedule.id, 1);
    const [, edited, skipped] = audit.configEntries(SCHEDULE_ORG);
    expect([edited!.fields['changed'], edited!.fields['revisionFrom'], edited!.fields['revisionTo']]).to.deep.equal(['askDigest', 0, 1]);
    expect([skipped!.fields['changed'], skipped!.fields['revisionFrom'], skipped!.fields['revisionTo']]).to.deep.equal(['skipNextAt', 1, 2]);
    const text = JSON.stringify(audit.configEntries(SCHEDULE_ORG));
    expect(text).to.not.include('SENTINEL-before');
    expect(text).to.not.include('SENTINEL-after');
  });

  it('CW-10: a schedule pause that loses its race records nothing', async () => {
    const audit = auditedControlPlane();
    const clock = new TestClock(Date.parse('2026-07-01T00:00:00.000Z'));
    const store = new InMemoryScheduleStore(clock);
    const actors = new FakeActors();
    const setting = new FakeSetting();
    const firer = new ScheduleFirer({ store, admission: new FakeAdmission(), actors, setting });
    const service = new ScheduleService({ transaction: audit.transaction, store, firer, actors, setting });
    const alice = { orgId: SCHEDULE_ORG, userId: ALICE, via: 'session' as const };
    const { schedule } = await service.create(alice, { name: 'Digest', input: { query: 'q' }, trigger: { kind: 'webhook' } });
    sinon.stub(store, 'setStatus').resolves(null);
    const refused = await service.pause(alice, schedule.id).catch((error: unknown) => error);
    expect(refused).to.have.property('code', 'SCHEDULE_NOT_FOUND');
    expect(audit.configEntries(SCHEDULE_ORG)).to.have.length(1);
  });
});
