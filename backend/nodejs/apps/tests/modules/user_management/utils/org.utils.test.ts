import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { findActiveOrgById } from '../../../../src/modules/user_management/utils/org.utils';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';

describe('user_management/utils/org.utils', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('findActiveOrgById', () => {
    const validOrgId = '507f1f77bcf86cd799439011';

    it('should return the org when an active org matches the given id', async () => {
      const mockOrg = {
        _id: validOrgId,
        accountType: 'business',
        isDeleted: false,
      };
      const findOneStub = sinon.stub(Org, 'findOne').resolves(mockOrg as any);

      const result = await findActiveOrgById(validOrgId);

      expect(result).to.equal(mockOrg);
      expect(findOneStub.calledOnce).to.be.true;
      expect(findOneStub.firstCall.args[0]).to.deep.equal({
        _id: validOrgId,
        isDeleted: false,
      });
    });

    it('should return null when no active org matches the given id', async () => {
      const findOneStub = sinon.stub(Org, 'findOne').resolves(null);

      const result = await findActiveOrgById(validOrgId);

      expect(result).to.be.null;
      expect(findOneStub.calledOnce).to.be.true;
    });

    it('should accept an ObjectId — the shape a freshly JIT-provisioned user carries — and query by its string form', async () => {
      // jit-provisioning returns newUser.toObject(), whose orgId is an
      // ObjectId; generateAuthToken passes it straight through. With a
      // string-only guard the first SSO login of every provisioned user
      // failed with "Organization not found" and only the retry succeeded.
      const objectId = new mongoose.Types.ObjectId(validOrgId);
      const mockOrg = { _id: validOrgId, accountType: 'business', isDeleted: false };
      const findOneStub = sinon.stub(Org, 'findOne').resolves(mockOrg as any);

      const result = await findActiveOrgById(objectId);

      expect(result).to.equal(mockOrg);
      expect(findOneStub.calledOnce).to.be.true;
      expect(findOneStub.firstCall.args[0]).to.deep.equal({
        _id: validOrgId,
        isDeleted: false,
      });
    });

    it('should return null without querying the database when orgId is an object that is not an ObjectId', async () => {
      const findOneStub = sinon.stub(Org, 'findOne').resolves(null);

      const result = await findActiveOrgById({ not: 'an id' });

      expect(result).to.be.null;
      expect(findOneStub.called).to.be.false;
    });

    it('should return null without querying the database when orgId is a non-ObjectId object whose toString returns a valid id', async () => {
      // Only a real ObjectId is normalised, so an arbitrary object that
      // happens to stringify to a well-formed id must not reach the database.
      const findOneStub = sinon.stub(Org, 'findOne').resolves(null);

      const result = await findActiveOrgById({ toString: () => validOrgId });

      expect(result).to.be.null;
      expect(findOneStub.called).to.be.false;
    });

    it('should return null without querying the database when orgId is not a string', async () => {
      const findOneStub = sinon.stub(Org, 'findOne').resolves(null);

      const result = await findActiveOrgById(12345);

      expect(result).to.be.null;
      expect(findOneStub.called).to.be.false;
    });

    it('should return null without querying the database when orgId is undefined', async () => {
      const findOneStub = sinon.stub(Org, 'findOne').resolves(null);

      const result = await findActiveOrgById(undefined);

      expect(result).to.be.null;
      expect(findOneStub.called).to.be.false;
    });

    it('should return null without querying the database when orgId is null', async () => {
      const findOneStub = sinon.stub(Org, 'findOne').resolves(null);

      const result = await findActiveOrgById(null);

      expect(result).to.be.null;
      expect(findOneStub.called).to.be.false;
    });

    it('should return null without querying the database when orgId is a malformed string', async () => {
      const findOneStub = sinon.stub(Org, 'findOne').resolves(null);

      const result = await findActiveOrgById('not-a-valid-object-id');

      expect(result).to.be.null;
      expect(findOneStub.called).to.be.false;
    });

    it('should propagate errors raised by the database', async () => {
      const dbError = new Error('connection lost');
      sinon.stub(Org, 'findOne').rejects(dbError);

      try {
        await findActiveOrgById(validOrgId);
        expect.fail('should have thrown');
      } catch (error) {
        expect(error).to.equal(dbError);
      }
    });
  });
});
