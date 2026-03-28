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

  afterEach(() => {
    sinon.restore();
  });

  it('should generate a valid JWT token when org is found', async () => {
    const user = {
      orgId: 'org123',
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
    expect(decoded.orgId).to.equal('org123');

    expect(findOneStub.calledOnce).to.be.true;
    expect(findOneStub.firstCall.args[0]).to.deep.include({
      orgId: 'org123',
      isDeleted: false,
    });
  });

  it('should throw NotFoundError when org is not found', async () => {
    const user = {
      orgId: 'nonexistent',
      email: 'test@example.com',
      _id: 'user123',
      fullName: 'Test User',
    };

    sinon.stub(Org, 'findOne').resolves(null);

    try {
      await generateAuthToken(user, jwtSecret);
      expect.fail('Should have thrown NotFoundError');
    } catch (error) {
      expect(error).to.be.instanceOf(NotFoundError);
      expect((error as NotFoundError).message).to.equal(
        'Organization not found',
      );
    }
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
