import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { userAdminOrSelfCheck } from '../../../../src/modules/user_management/middlewares/userAdminOrSelfCheck';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import mongoose from 'mongoose';

function stubUserRole(role: 'admin' | 'member' | null) {
  return sinon.stub(Users, 'findOne').returns({
    select: sinon.stub().returns({
      lean: sinon.stub().resolves(role ? { role } : null),
    }),
  } as any);
}

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
    stubUserRole('admin');

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });

  it('should call next() without error when user is modifying self', async () => {
    req.user.userId = targetUserId;
    req.params.id = targetUserId;

    stubUserRole('member');

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });

  it('should call next with error when non-admin user tries to modify another user', async () => {
    req.user.userId = standardUserId;
    req.params.id = targetUserId;

    stubUserRole('member');

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

  it('should query Users with correct parameters', async () => {
    const leanStub = sinon.stub().resolves({ role: 'admin' });
    const selectStub = sinon.stub().returns({ lean: leanStub });
    const findOneStub = sinon.stub(Users, 'findOne').returns({
      select: selectStub,
    } as any);

    await userAdminOrSelfCheck(req, res, next);

    expect(findOneStub.calledOnce).to.be.true;
    expect(findOneStub.firstCall.args[0]).to.deep.equal({
      _id: adminUserId,
      orgId: orgId,
      isDeleted: { $ne: true },
    });
  });

  it('should handle database errors gracefully', async () => {
    const dbError = new Error('Database connection failed');
    sinon.stub(Users, 'findOne').returns({
      select: sinon.stub().returns({
        lean: sinon.stub().rejects(dbError),
      }),
    } as any);

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Database connection failed');
  });

  it('should allow admin to modify any user regardless of id match', async () => {
    req.user.userId = adminUserId;
    req.params.id = new mongoose.Types.ObjectId().toString();

    stubUserRole('admin');

    await userAdminOrSelfCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });
});
