import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { userAdminCheck } from '../../../../src/modules/user_management/middlewares/userAdminCheck';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';

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
    const findStub = sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().resolves([
        { type: 'admin' },
        { type: 'everyone' },
      ]),
    } as any);

    await userAdminCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });

  it('should call next with error when user is not admin', async () => {
    const findStub = sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().resolves([
        { type: 'standard' },
        { type: 'everyone' },
      ]),
    } as any);

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

  it('should call next with NotFoundError when user object is undefined', async () => {
    req.user = undefined;

    await userAdminCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Account not found');
  });

  it('should call next with error when user has no groups', async () => {
    const findStub = sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().resolves([]),
    } as any);

    await userAdminCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Admin access required');
  });

  it('should query UserGroups with correct parameters', async () => {
    const selectStub = sinon.stub().resolves([{ type: 'admin' }]);
    const findStub = sinon.stub(UserGroups, 'find').returns({
      select: selectStub,
    } as any);

    await userAdminCheck(req, res, next);

    expect(findStub.calledOnce).to.be.true;
    const query = findStub.firstCall.args[0];
    expect(query).to.deep.equal({
      orgId: '507f1f77bcf86cd799439012',
      users: { $in: ['507f1f77bcf86cd799439011'] },
      isDeleted: false,
    });
    expect(selectStub.calledWith('type')).to.be.true;
  });

  it('should handle database errors gracefully', async () => {
    const dbError = new Error('Database connection failed');
    sinon.stub(UserGroups, 'find').returns({
      select: sinon.stub().rejects(dbError),
    } as any);

    await userAdminCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Database connection failed');
  });
});
