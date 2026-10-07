import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import {
  generateAuthToken,
  generateFetchConfigAuthToken,
  SERVICE_ACCOUNT_SIGN_IN_MESSAGE,
  DISABLED_ACCOUNT_SIGN_IN_MESSAGE,
} from '../../../../src/modules/auth/utils/generateAuthToken';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { NotFoundError } from '../../../../src/libs/errors/http.errors';

/**
 * Before issuing a session, generateAuthToken reads kind and isDisabled
 * straight from the database rather than trusting the user object it was
 * handed. These tests drive that lookup through this stub.
 */
function stubAccount(doc: Record<string, unknown> | null) {
  return sinon.stub(Users, 'findOne').returns({
    select: sinon.stub().returns({
      lean: sinon.stub().returns({
        exec: sinon.stub().resolves(doc),
      }),
    }),
  } as any);
}

describe('generateAuthToken', () => {
  const jwtSecret = 'test-jwt-secret';
  const validOrgId = '507f1f77bcf86cd799439011';
  const validUserId = '507f1f77bcf86cd799439012';

  afterEach(() => {
    sinon.restore();
  });

  it('should generate a valid JWT token when org is found', async () => {
    const user = {
      orgId: validOrgId,
      email: 'test@example.com',
      _id: validUserId,
      fullName: 'Test User',
    };

    stubAccount({ kind: 'human', isDisabled: false });
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

    // Re-stub to return a thenable. This clears every stub, so the account
    // lookup has to be put back as well.
    sinon.restore();
    stubAccount({ kind: 'human', isDisabled: false });
    const findOneStub = sinon.stub(Org, 'findOne').resolves(mockOrg as any);

    const token = await generateAuthToken(user, jwtSecret);
    expect(token).to.be.a('string');
    expect(token.split('.')).to.have.lengthOf(3);

    const decoded = jwt.decode(token) as Record<string, any>;
    expect(decoded.email).to.equal('test@example.com');
    expect(decoded.userId).to.equal(validUserId);
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
      _id: validUserId,
      fullName: 'Test User',
    };

    stubAccount({ kind: 'human', isDisabled: false });
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

  describe('accounts that may not hold a session', () => {
    const user = {
      orgId: validOrgId,
      email: 'test@example.com',
      _id: validUserId,
      fullName: 'Test User',
    };

    it('refuses to issue a session for a service account', async () => {
      stubAccount({ kind: 'service', isDisabled: false });
      try {
        await generateAuthToken(user, jwtSecret);
        expect.fail('expected a service account to be refused a session');
      } catch (error) {
        expect((error as Error).message).to.equal(
          SERVICE_ACCOUNT_SIGN_IN_MESSAGE,
        );
      }
    });

    it('refuses a service account even when the caller claims it is human', async () => {
      // The argument says human; the database says service. The database
      // wins, which is the whole reason the lookup is there.
      stubAccount({ kind: 'service', isDisabled: false });
      try {
        await generateAuthToken({ ...user, kind: 'human' }, jwtSecret);
        expect.fail('expected the stored kind to decide, not the argument');
      } catch (error) {
        expect((error as Error).message).to.equal(
          SERVICE_ACCOUNT_SIGN_IN_MESSAGE,
        );
      }
    });

    it('refuses to issue a session for a disabled account', async () => {
      stubAccount({ kind: 'human', isDisabled: true });
      try {
        await generateAuthToken(user, jwtSecret);
        expect.fail('expected a disabled account to be refused a session');
      } catch (error) {
        expect((error as Error).message).to.equal(
          DISABLED_ACCOUNT_SIGN_IN_MESSAGE,
        );
      }
    });

    it('fails closed when the account no longer exists', async () => {
      stubAccount(null);
      const orgStub = sinon.stub(Org, 'findOne').resolves({} as any);
      try {
        await generateAuthToken(user, jwtSecret);
        expect.fail('expected a missing account to be refused a session');
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError);
        expect((error as Error).message).to.equal('User not found');
      }
      // Refused before the org is even looked up.
      expect(orgStub.called).to.equal(false);
    });

    it('refuses a malformed user id without querying the database', async () => {
      const findOneStub = sinon.stub(Users, 'findOne');
      try {
        await generateAuthToken({ ...user, _id: 'not-an-id' }, jwtSecret);
        expect.fail('expected a malformed user id to be refused');
      } catch (error) {
        expect(error).to.be.instanceOf(NotFoundError);
        expect((error as Error).message).to.equal('User not found');
      }
      expect(findOneStub.called).to.equal(false);
    });
  });

  it('should throw NotFoundError instead of an unhandled cast error when orgId is malformed', async () => {
    const user = {
      orgId: 'not-a-valid-object-id',
      email: 'test@example.com',
      _id: validUserId,
      fullName: 'Test User',
    };

    stubAccount({ kind: 'human', isDisabled: false });
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
