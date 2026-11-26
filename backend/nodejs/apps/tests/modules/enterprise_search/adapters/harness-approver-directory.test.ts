import { expect } from 'chai';
import sinon from 'sinon';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import * as userAdmin from '../../../../src/modules/user_management/services/user-admin.service';
import { HarnessApproverDirectory } from '../../../../src/libs/repositories/agent-harness/approver-directory/mongo';

const ORG = '507f1f77bcf86cd799439011';
const USER = '507f191e810c19729de860ea';
const GROUP = '64b7f0c2a1b2c3d4e5f60718';

describe('HarnessApproverDirectory', () => {
  afterEach(() => sinon.restore());

  it('asks the user-admin service with (userId, orgId) in that order', async () => {
    const admin = sinon.stub(userAdmin, 'isUserOrgAdmin').resolves(false);
    const directory = new HarnessApproverDirectory();
    expect(await directory.isOrgAdmin(ORG, USER)).to.equal(false);
    expect(admin.firstCall.args).to.deep.equal([USER, ORG]);
  });

  it('names nobody an approver: a user and a group member are not, and no user or group is read', async () => {
    const users = sinon.stub(Users, 'findOne');
    const groups = sinon.stub(UserGroups, 'findOne');
    const directory = new HarnessApproverDirectory();
    expect(await directory.isApprover(ORG, USER)).to.equal(false);
    expect(await directory.isApprover(ORG, GROUP)).to.equal(false);
    expect(users.called || groups.called).to.equal(false);
  });
});
