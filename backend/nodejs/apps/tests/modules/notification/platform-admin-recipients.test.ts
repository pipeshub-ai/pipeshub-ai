import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { setOrgRepository } from '../../../src/libs/repositories/org';
import { setUserRepository } from '../../../src/libs/repositories/user';
import { forOrg, getCurrentOrgId } from '../../../src/libs/context/orgContext';
import { resolvePlatformAdminRecipients } from '../../../src/config';
import { resetPlatformRecipientWarnings } from '../../../src/modules/notification/utils/platform-admin-recipients';
import { Logger } from '../../../src/libs/services/logger.service';

describe('notification/platform-admin-recipients', () => {
  const orgId = new mongoose.Types.ObjectId().toString();
  const admin1 = new mongoose.Types.ObjectId().toString();
  const admin2 = new mongoose.Types.ObjectId().toString();

  afterEach(() => {
    setOrgRepository();
    setUserRepository();
    sinon.restore();
  });

  it("returns the admins of the installation's organisation, read in that organisation", async () => {
    setOrgRepository({ liveSummary: sinon.stub().resolves({ count: 1, oldestId: orgId }) } as never);
    let seenOrg: string | undefined;
    const listAdminIds = sinon.stub().callsFake(async () => {
      seenOrg = getCurrentOrgId();
      return [admin1, admin2];
    });
    setUserRepository({ listAdminIds } as never);

    const result = await resolvePlatformAdminRecipients();

    expect(result).to.deep.equal([{ orgId, userIds: [admin1, admin2] }]);
    expect(listAdminIds.calledOnceWith(orgId)).to.be.true;
    expect(seenOrg).to.equal(orgId);
  });

  it('returns nothing when there is no organisation yet', async () => {
    setOrgRepository({ liveSummary: sinon.stub().resolves({ count: 0, oldestId: null }) } as never);
    const listAdminIds = sinon.stub();
    setUserRepository({ listAdminIds } as never);

    expect(await resolvePlatformAdminRecipients()).to.deep.equal([]);
    expect(listAdminIds.called).to.be.false;
  });

  it('returns nothing when the organisation has no admins', async () => {
    setOrgRepository({ liveSummary: sinon.stub().resolves({ count: 1, oldestId: orgId }) } as never);
    setUserRepository({ listAdminIds: sinon.stub().resolves([]) } as never);

    expect(await resolvePlatformAdminRecipients()).to.deep.equal([]);
  });

  it('returns nothing while more than one organisation is live: the first one found could be any of them', async () => {
    setOrgRepository({ liveSummary: sinon.stub().resolves({ count: 2, oldestId: orgId }) } as never);
    const listAdminIds = sinon.stub();
    setUserRepository({ listAdminIds } as never);

    expect(await resolvePlatformAdminRecipients()).to.deep.equal([]);
    expect(listAdminIds.called).to.be.false;
  });

  it("returns nothing inside another organisation's context, without binding the installation's one there", async () => {
    setOrgRepository({ liveSummary: sinon.stub().resolves({ count: 1, oldestId: orgId }) } as never);
    const listAdminIds = sinon.stub().resolves([admin1]);
    setUserRepository({ listAdminIds } as never);
    const other = new mongoose.Types.ObjectId().toString();

    expect(await forOrg(other, 'test', () => resolvePlatformAdminRecipients())).to.deep.equal([]);
    expect(listAdminIds.called).to.be.false;
    expect(await forOrg(orgId, 'test', () => resolvePlatformAdminRecipients())).to.deep.equal([
      { orgId, userIds: [admin1] },
    ]);
  });

  it('warns once per process, naming no organisation, while more than one is live', async () => {
    resetPlatformRecipientWarnings();
    const warn = sinon.stub();
    sinon.stub(Logger, 'getInstance').returns({ warn } as never);
    setOrgRepository({ liveSummary: sinon.stub().resolves({ count: 2, oldestId: orgId }) } as never);
    setUserRepository({ listAdminIds: sinon.stub() } as never);

    await resolvePlatformAdminRecipients();
    await resolvePlatformAdminRecipients();

    expect(warn.callCount).to.equal(1);
    expect(JSON.stringify(warn.firstCall.args)).to.not.include(orgId);
  });

  it('warns once per process, naming no organisation, while none is live, and apart from the several-live warning', async () => {
    resetPlatformRecipientWarnings();
    const warn = sinon.stub();
    sinon.stub(Logger, 'getInstance').returns({ warn } as never);
    const listAdminIds = sinon.stub();
    setUserRepository({ listAdminIds } as never);
    const liveSummary = sinon.stub().resolves({ count: 0, oldestId: null });
    setOrgRepository({ liveSummary } as never);

    expect(await resolvePlatformAdminRecipients()).to.deep.equal([]);
    await resolvePlatformAdminRecipients();
    liveSummary.resolves({ count: 2, oldestId: orgId });
    await resolvePlatformAdminRecipients();
    await resolvePlatformAdminRecipients();

    expect(listAdminIds.called).to.be.false;
    expect(warn.callCount).to.equal(2);
    expect(warn.firstCall.args[0]).to.include('no organisation is live');
    expect(warn.secondCall.args[0]).to.include('more than one organisation is live');
    expect(JSON.stringify(warn.args)).to.not.include(orgId);
  });
});
