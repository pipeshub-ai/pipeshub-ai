import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import {
  generateAuthToken,
  generateFetchConfigAuthToken,
} from '../../../../src/modules/auth/utils/generateAuthToken';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { NotFoundError } from '../../../../src/libs/errors/http.errors';

describe('generateAuthToken', () => {
  const jwtSecret = 'test-jwt-secret';
  const validOrgId = '507f1f77bcf86cd799439011';

  afterEach(() => {
    sinon.restore();
  });

  it('should generate a valid JWT token when org is found', async () => {
    const user = {
      orgId: validOrgId,
      email: 'test@example.com',
      _id: 'user123',
      fullName: 'Test User',
    };

    const mockOrg = { accountType: 'enterprise' };
    const mockQuery = {
      lean: sinon.stub(),
      exec: sinon.stub(),
    };
    sinon.stub(Org, 'findOne').returns(mockQuery as any);
    mockQuery.lean.returns(mockQuery);
    mockQuery.exec.resolves(mockOrg);
    // Org.findOne returns the mockOrg directly through the chain
    (Org.findOne as sinon.SinonStub).returns({
      ...mockOrg,
      then: (resolve: any) => resolve(mockOrg),
    } as any);

    // Re-stub to return a thenable
    sinon.restore();
    const findOneStub = sinon.stub(Org, 'findOne').resolves(mockOrg as any);

    const token = await generateAuthToken(user, jwtSecret);
    expect(token).to.be.a('string');
    expect(token.split('.')).to.have.lengthOf(3);

    const decoded = jwt.decode(token) as Record<string, any>;
    expect(decoded.email).to.equal('test@example.com');
    expect(decoded.userId).to.equal('user123');
    expect(decoded.orgId).to.equal(validOrgId);

    expect(findOneStub.calledOnce).to.be.true;
    expect(findOneStub.firstCall.args[0]).to.deep.include({
      _id: validOrgId,
      isDeleted: false,
    });
  });

  it('should throw NotFoundError when org is not found', async () => {
    const user = {
      orgId: '507f1f77bcf86cd799439099',
      email: 'test@example.com',
      _id: 'user123',
      fullName: 'Test User',
    };

    const findOneStub = sinon.stub(Org, 'findOne').resolves(null);

    try {
      await generateAuthToken(user, jwtSecret);
      expect.fail('Should have thrown NotFoundError');
    } catch (error) {
      expect(error).to.be.instanceOf(NotFoundError);
      expect((error as NotFoundError).message).to.equal(
        'Organization not found',
      );
    }
    // Confirms this test actually exercises the "not found in DB" path,
    // not the format guard below.
    expect(findOneStub.calledOnce).to.be.true;
  });

  it('should throw NotFoundError instead of an unhandled cast error when orgId is malformed', async () => {
    const user = {
      orgId: 'not-a-valid-object-id',
      email: 'test@example.com',
      _id: 'user123',
      fullName: 'Test User',
    };

    const findOneStub = sinon.stub(Org, 'findOne').resolves(null);

    try {
      await generateAuthToken(user, jwtSecret);
      expect.fail('Should have thrown NotFoundError');
    } catch (error) {
      expect(error).to.be.instanceOf(NotFoundError);
      expect((error as NotFoundError).message).to.equal(
        'Organization not found',
      );
    }
    // The format guard must reject before ever querying the database.
    expect(findOneStub.called).to.be.false;
  });
});

describe('generateFetchConfigAuthToken', () => {
  const scopedJwtSecret = 'test-scoped-secret';

  it('should generate a valid JWT token', async () => {
    const user = {
      _id: 'user123',
      orgId: 'org123',
    };

    const token = await generateFetchConfigAuthToken(user, scopedJwtSecret);
    expect(token).to.be.a('string');
    expect(token.split('.')).to.have.lengthOf(3);

    const decoded = jwt.decode(token) as Record<string, any>;
    expect(decoded.userId).to.equal('user123');
    expect(decoded.orgId).to.equal('org123');
  });

  it('should include fetch_config scope in the token', async () => {
    const user = {
      _id: 'user123',
      orgId: 'org123',
    };

    const token = await generateFetchConfigAuthToken(user, scopedJwtSecret);
    const decoded = jwt.decode(token) as Record<string, any>;
    expect(decoded.scopes).to.be.an('array');
    expect(decoded.scopes).to.include('fetch:config');
  });
});
