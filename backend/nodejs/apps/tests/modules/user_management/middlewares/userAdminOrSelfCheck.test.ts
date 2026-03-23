import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { userAdminOrSelfCheck } from '../../../../src/modules/user_management/middlewares/userAdminOrSelfCheck';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';
import mongoose from 'mongoose';

describe('userAdminOrSelfCheck Middleware', () => {
  let req: any;
  let res: any;
  let next: sinon.SinonStub;
  const adminUserId = new mongoose.Types.ObjectId().toString();
  const standardUserId = new mongoose.Types.ObjectId().toString();
  const targetUserId = new mongoose.Types.ObjectId().toString();
  const orgId = new mongoose.Types.ObjectId().toString();

  beforeEach(() => {
    req = {
      user: {
        userId: adminUserId,
        orgId: orgId,
      },
      params: {
        id: targetUserId,
      },
    };
    res = {
      status: sinon.stub().returnsThis(),
      json: sinon.stub(),
    };
    next = sinon.stub();
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should call next() without error when user is admin', async () => {
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().resolves([
        { type: 'admin' },
        { type: 'everyone' },
      ]),
    } as any);

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });

  it('should call next() without error when user is modifying self', async () => {
    // User is not admin but is modifying their own record
    req.user.userId = targetUserId;
    req.params.id = targetUserId;

    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().resolves([
        { type: 'standard' },
        { type: 'everyone' },
      ]),
    } as any);

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });

  it('should call next with error when non-admin user tries to modify another user', async () => {
    req.user.userId = standardUserId;
    req.params.id = targetUserId;

    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().resolves([
        { type: 'standard' },
        { type: 'everyone' },
      ]),
    } as any);

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal(
      "You dont have admin access and can't change other users",
    );
  });

  it('should call next with NotFoundError when userId is missing', async () => {
    req.user = { orgId: orgId };

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Account not found');
  });

  it('should call next with NotFoundError when orgId is missing', async () => {
    req.user = { userId: adminUserId };

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Account not found');
  });

  it('should call next with NotFoundError when user is undefined', async () => {
    req.user = undefined;

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Account not found');
  });

  it('should query UserGroups with correct parameters', async () => {
    const selectStub = sinon.stub().resolves([{ type: 'admin' }]);
    const findStub = sinon.stub(UserGroups, 'find').returns({
      select: selectStub,
    } as any);

    await userAdminOrSelfCheck(req, res, next);

    expect(findStub.calledOnce).to.be.true;
    const query = findStub.firstCall.args[0];
    expect(query).to.deep.equal({
      orgId: orgId,
      users: { $in: [adminUserId] },
      isDeleted: false,
    });
  });

  it('should handle database errors gracefully', async () => {
    const dbError = new Error('Database connection failed');
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().rejects(dbError),
    } as any);

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Database connection failed');
  });

  it('should allow admin to modify any user regardless of id match', async () => {
    // Admin user modifying a completely different user
    req.user.userId = adminUserId;
    req.params.id = new mongoose.Types.ObjectId().toString();

    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().resolves([{ type: 'admin' }]),
    } as any);

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });
});
