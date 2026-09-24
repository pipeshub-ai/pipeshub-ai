import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { userAdminCheck } from '../../../../src/modules/user_management/middlewares/userAdminCheck';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';

function stubUserRole(role: 'admin' | 'member' | null) {
  // null => existing user with unset role (not admin)
  const doc = role === null ? {} : { role };
  return sinon.stub(Users, 'findOne').returns({
    select: sinon.stub().returns({
      lean: sinon.stub().resolves(doc),
    }),
  } as any);
}

describe('userAdminCheck Middleware', () => {
  let req: any;
  let res: any;
  let next: sinon.SinonStub;

  beforeEach(() => {
    req = {
      user: {
        userId: '507f1f77bcf86cd799439011',
        orgId: '507f1f77bcf86cd799439012',
      },
      params: {},
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

    await userAdminCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });

  it('should call next with error when user is not admin', async () => {
    stubUserRole('member');

    await userAdminCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Admin access required');
  });

  it('should deny access when role is unset (no admin-group fallback)', async () => {
    stubUserRole(null);

    await userAdminCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Admin access required');
  });

  it('should call next with NotFoundError when userId is missing', async () => {
    req.user = { orgId: '507f1f77bcf86cd799439012' };

    await userAdminCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Account not found');
  });

  it('should call next with NotFoundError when orgId is missing', async () => {
    req.user = { userId: '507f1f77bcf86cd799439011' };

    await userAdminCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Account not found');
  });
});
