import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { InstanceAppOwnerMigration } from '../../../../../src/modules/configuration_manager/services/migrations/instance_app_owner.migration';
import { setIdentityPort } from '../../../../../src/libs/repositories/identity';
import { setOAuthAppRepository } from '../../../../../src/libs/repositories/oauth-app';

const logger = () => ({ info: sinon.stub(), error: sinon.stub(), debug: sinon.stub(), warn: sinon.stub() });

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

  // The repository leaves every app this release stored alone (ownerFormat), so nothing gates a run.
  it('repairs the instance apps of the install organisation at every start', async () => {
    const migration = new InstanceAppOwnerMigration(logger() as any, async () => 'org-1');

    expect(await migration.run()).to.deep.equal({ updated: 2 });
    disown.resolves(0);
    expect(await migration.run()).to.deep.equal({ updated: 0 });

    expect(disown.callCount).to.equal(2);
    expect(disown.alwaysCalledWithExactly('org-1', 'org-1')).to.equal(true);
  });

  it('moves dynamic clients to the organisation it is given', async () => {
    await new InstanceAppOwnerMigration(logger() as any, async () => 'org-shared').run();

    expect(disown.calledOnceWithExactly('org-1', 'org-shared')).to.equal(true);
  });

  it('surfaces a failed repair, so the caller logs it and the next start retries', async () => {
    disown.rejects(new Error('store down'));

    const error = await new InstanceAppOwnerMigration(logger() as any, async () => 'org-1')
      .run()
      .catch((e: unknown) => e);

    expect((error as Error).message).to.equal('store down');
  });

  it('does nothing on an instance with no organisation yet', async () => {
    setIdentityPort({ oldestOrgId: async () => null } as any);

    expect(await new InstanceAppOwnerMigration(logger() as any, async () => null).run()).to.deep.equal({ updated: 0 });
    expect(disown.called).to.equal(false);
  });
});
