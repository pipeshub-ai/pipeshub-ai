import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { InstanceAppOwnerMigration } from '../../../../../src/modules/configuration_manager/services/migrations/instance_app_owner.migration';
import { configPaths } from '../../../../../src/modules/configuration_manager/paths/paths';
import { setIdentityPort } from '../../../../../src/libs/repositories/identity';
import { setOAuthAppRepository } from '../../../../../src/libs/repositories/oauth-app';

const logger = () => ({ info: sinon.stub(), error: sinon.stub(), debug: sinon.stub(), warn: sinon.stub() });

const BOUND = '2026-11-16T00:00:00.000Z';

const kvStore = (flag: string | null = null, bound: string | null = BOUND) => ({
  get: sinon.stub().callsFake(async (path: string) =>
    path === configPaths.instanceAppOwnerMigration
      ? flag
      : path === configPaths.instanceAppOwnerBound
        ? bound
        : null,
  ),
  set: sinon.stub().resolves(),
  compareAndSet: sinon.stub().resolves(true),
});

describe('InstanceAppOwnerMigration', () => {
  let disown: sinon.SinonStub;

  beforeEach(() => {
    disown = sinon.stub().resolves(2);
    setOAuthAppRepository({ disownInstanceApps: disown } as any);
    setIdentityPort({ oldestOrgId: async () => 'org-1' } as any);
  });

  afterEach(() => {
    sinon.restore();
    setOAuthAppRepository();
    setIdentityPort();
  });

  it('repairs the instance apps of the install organisation once, then sets its flag', async () => {
    const kv = kvStore();

    const result = await new InstanceAppOwnerMigration(logger() as any, kv as any, async () => 'org-1').run();

    expect(result).to.deep.equal({ skipped: false, updated: 2 });
    expect(disown.calledOnceWithExactly('org-1', 'org-1', new Date(BOUND))).to.equal(true);
    expect(kv.set.calledOnceWithExactly(configPaths.instanceAppOwnerMigration, 'true')).to.equal(true);
    expect(kv.set.calledAfter(disown)).to.equal(true);
  });

  it('moves dynamic clients to the organisation it is given', async () => {
    await new InstanceAppOwnerMigration(logger() as any, kvStore() as any, async () => 'org-shared').run();

    expect(disown.calledOnceWithExactly('org-1', 'org-shared', new Date(BOUND))).to.equal(true);
  });

  // A metadata document client consented to after the repair names its person; a second run would take that away.
  it('does nothing once its flag is set', async () => {
    const kv = kvStore('true');

    const result = await new InstanceAppOwnerMigration(logger() as any, kv as any, async () => 'org-1').run();

    expect(result).to.deep.equal({ skipped: true, updated: 0 });
    expect(disown.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('sets no flag when the repair fails, so the next start retries it', async () => {
    disown.rejects(new Error('store down'));
    const kv = kvStore();

    const error = await new InstanceAppOwnerMigration(logger() as any, kv as any, async () => 'org-1')
      .run()
      .catch((e: unknown) => e);

    expect((error as Error).message).to.equal('store down');
    expect(kv.set.called).to.equal(false);
  });

  // Apps stored by this version (a metadata client stored as its consenter) are newer than the bound and stay as they are.
  it('changes only apps older than the bound, and waits without one', async () => {
    const kv = kvStore(null, null);

    const result = await new InstanceAppOwnerMigration(logger() as any, kv as any, async () => 'org-1').run();

    expect(result).to.deep.equal({ skipped: true, updated: 0 });
    expect(disown.called).to.equal(false);
    expect(kv.set.called).to.equal(false);
  });

  it('records the bound only if no earlier process did', async () => {
    const kv = kvStore();
    const before = Date.now();

    await InstanceAppOwnerMigration.markBound(kv as any);

    const [path, expected, value] = kv.compareAndSet.firstCall.args;
    expect(path).to.equal(configPaths.instanceAppOwnerBound);
    expect(expected).to.equal(null);
    expect(Date.parse(value)).to.be.within(before, Date.now());
  });

  it('only sets its flag on an instance with no organisation yet', async () => {
    setIdentityPort({ oldestOrgId: async () => null } as any);
    const kv = kvStore();

    const result = await new InstanceAppOwnerMigration(logger() as any, kv as any, async () => null).run();

    expect(result).to.deep.equal({ skipped: false, updated: 0 });
    expect(disown.called).to.equal(false);
    expect(kv.set.calledOnce).to.equal(true);
  });
});
