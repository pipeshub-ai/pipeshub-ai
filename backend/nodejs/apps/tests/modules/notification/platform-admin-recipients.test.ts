import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { setOrgRepository } from '../../../src/libs/repositories/org';
import { setUserRepository } from '../../../src/libs/repositories/user';
import { getCurrentOrgId } from '../../../src/libs/context/orgContext';
import { resolvePlatformAdminRecipients } from '../../../src/config';

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
    setOrgRepository({ getFirstLive: sinon.stub().resolves({ _id: orgId }) } as never);
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
    setOrgRepository({ getFirstLive: sinon.stub().resolves(null) } as never);
    const listAdminIds = sinon.stub();
    setUserRepository({ listAdminIds } as never);

    expect(await resolvePlatformAdminRecipients()).to.deep.equal([]);
    expect(listAdminIds.called).to.be.false;
  });

  it('returns nothing when the organisation has no admins', async () => {
    setOrgRepository({ getFirstLive: sinon.stub().resolves({ _id: orgId }) } as never);
    setUserRepository({ listAdminIds: sinon.stub().resolves([]) } as never);

    expect(await resolvePlatformAdminRecipients()).to.deep.equal([]);
  });
});
