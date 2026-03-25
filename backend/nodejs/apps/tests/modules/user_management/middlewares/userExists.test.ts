import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { userExists } from '../../../../src/modules/user_management/middlewares/userExists';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';

describe('userExists Middleware', () => {
  let req: any;
  let res: any;
  let next: sinon.SinonStub;

  beforeEach(() => {
    req = {
      user: {
        orgId: '507f1f77bcf86cd799439012',
      },
      params: {
        id: '507f1f77bcf86cd799439011',
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

  it('should call next() and set req.user when org and user exist', async () => {
    const mockOrg = { _id: '507f1f77bcf86cd799439012', isDeleted: false };
    const mockUser = {
      _id: '507f1f77bcf86cd799439011',
      orgId: '507f1f77bcf86cd799439012',
      email: 'test@test.com',
      fullName: 'Test User',
    };

    sinon.stub(Org, 'findOne').resolves(mockOrg as any);
    sinon.stub(Users, 'findOne').returns({
      exec: sinon.stub().resolves(mockUser),
    } as any);

    await userExists(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
    expect(req.user).to.deep.equal(mockUser);
  });

  it('should call next with NotFoundError when org does not exist', async () => {
    sinon.stub(Org, 'findOne').resolves(null);

    await userExists(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Account not found');
  });

  it('should call next with NotFoundError when user does not exist', async () => {
    const mockOrg = { _id: '507f1f77bcf86cd799439012', isDeleted: false };
    sinon.stub(Org, 'findOne').resolves(mockOrg as any);
    sinon.stub(Users, 'findOne').returns({
      exec: sinon.stub().resolves(null),
    } as any);

    await userExists(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('User not found');
  });

  it('should query Org with correct parameters', async () => {
    const orgFindStub = sinon.stub(Org, 'findOne').resolves(null);

    await userExists(req, res, next);

    expect(orgFindStub.calledOnce).to.be.true;
    const query = orgFindStub.firstCall.args[0];
    expect(query).to.deep.equal({
      _id: '507f1f77bcf86cd799439012',
      isDeleted: false,
    });
  });

  it('should query Users with correct parameters', async () => {
    const mockOrg = { _id: '507f1f77bcf86cd799439012', isDeleted: false };
    sinon.stub(Org, 'findOne').resolves(mockOrg as any);
    const execStub = sinon.stub().resolves({ _id: '507f1f77bcf86cd799439011' });
    const usersFindStub = sinon.stub(Users, 'findOne').returns({
      exec: execStub,
    } as any);

    await userExists(req, res, next);

    expect(usersFindStub.calledOnce).to.be.true;
    const query = usersFindStub.firstCall.args[0];
    expect(query).to.deep.equal({
      _id: '507f1f77bcf86cd799439011',
      orgId: '507f1f77bcf86cd799439012',
      isDeleted: false,
    });
  });

  it('should handle database errors gracefully', async () => {
    const dbError = new Error('Database connection failed');
    sinon.stub(Org, 'findOne').rejects(dbError);

    await userExists(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Database connection failed');
  });

  it('should replace req.user with the found user object', async () => {
    const originalUser = req.user;
    const mockOrg = { _id: '507f1f77bcf86cd799439012' };
    const foundUser = {
      _id: '507f1f77bcf86cd799439011',
      orgId: '507f1f77bcf86cd799439012',
      email: 'found@test.com',
      fullName: 'Found User',
    };

    sinon.stub(Org, 'findOne').resolves(mockOrg as any);
    sinon.stub(Users, 'findOne').returns({
      exec: sinon.stub().resolves(foundUser),
    } as any);

    await userExists(req, res, next);

    expect(req.user).to.not.deep.equal(originalUser);
    expect(req.user).to.deep.equal(foundUser);
  });
});
