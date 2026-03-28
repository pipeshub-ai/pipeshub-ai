import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { accountTypeCheck } from '../../../../src/modules/user_management/middlewares/accountTypeCheck';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';

describe('accountTypeCheck Middleware', () => {
  let req: any;
  let res: any;
  let next: sinon.SinonStub;

  beforeEach(() => {
    req = {
      user: {
        orgId: '507f1f77bcf86cd799439012',
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

  it('should call next() without error for business accounts', async () => {
    sinon.stub(Org, 'findOne').resolves({
      accountType: 'business',
      isDeleted: false,
    } as any);

    await accountTypeCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    expect(next.firstCall.args).to.have.lengthOf(0);
  });

  it('should call next with BadRequestError for individual accounts', async () => {
    sinon.stub(Org, 'findOne').resolves({
      accountType: 'individual',
      isDeleted: false,
    } as any);

    await accountTypeCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Access denied for individual accounts');
  });

  it('should call next with NotFoundError when orgId is missing', async () => {
    req.user = {};

    await accountTypeCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Account not found');
  });

  it('should call next with NotFoundError when user is undefined', async () => {
    req.user = undefined;

    await accountTypeCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Account not found');
  });

  it('should call next with BadRequestError when org is not found', async () => {
    sinon.stub(Org, 'findOne').resolves(null);

    await accountTypeCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Organisation not found');
  });

  it('should query Org with correct parameters', async () => {
    const orgFindStub = sinon.stub(Org, 'findOne').resolves({
      accountType: 'business',
    } as any);

    await accountTypeCheck(req, res, next);

    expect(orgFindStub.calledOnce).to.be.true;
    const query = orgFindStub.firstCall.args[0];
    expect(query).to.deep.equal({
      orgId: '507f1f77bcf86cd799439012',
      isDeleted: false,
    });
  });

  it('should handle database errors gracefully', async () => {
    const dbError = new Error('Database connection failed');
    sinon.stub(Org, 'findOne').rejects(dbError);

    await accountTypeCheck(req, res, next);

    expect(next.calledOnce).to.be.true;
    const error = next.firstCall.args[0];
    expect(error).to.be.an('error');
    expect(error.message).to.equal('Database connection failed');
  });
});
