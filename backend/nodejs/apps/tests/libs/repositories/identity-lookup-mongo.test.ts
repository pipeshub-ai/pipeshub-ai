import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { OAuthAccessToken } from '../../../src/modules/oauth_provider/schema/oauth.access_token.schema';
import { MongoIdentityLookup } from '../../../src/libs/repositories/identity-lookup/mongo';

// A stored limit that is not an object must reach the caller unchanged, so it can refuse the token.
// Loading the row as a Mongoose document drops such a value on hydration, and a missing limit reads
// as "everything the owner can see".
describe('MongoIdentityLookup.resolveToken: a malformed stored limit', () => {
  afterEach(() => sinon.restore());

  // Answers like a Mongoose query: awaited directly it hydrates a document; `.lean()` gives the stored row.
  const storedRow = (knowledgeLimit: unknown) => {
    const raw = {
      _id: new mongoose.Types.ObjectId(),
      orgId: new mongoose.Types.ObjectId(),
      userId: new mongoose.Types.ObjectId(),
      clientId: 'pat-system:abc',
      scopes: ['kb:read'],
      expiresAt: new Date(Date.now() + 3_600_000),
      isRevoked: false,
      tokenHash: 'h',
      knowledgeLimit,
    };
    const hydrated = () => OAuthAccessToken.hydrate(raw);
    return {
      lean: () => Promise.resolve(raw),
      then: (resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) =>
        Promise.resolve().then(hydrated).then(resolve, reject),
    };
  };

  for (const [name, value] of [
    ['false', false],
    ['a string', 'all'],
    ['a number', 7],
  ] as const) {
    it(`passes ${name} through instead of dropping it`, async () => {
      sinon.stub(OAuthAccessToken, 'findOne').returns(storedRow(value) as never);
      const token = await new MongoIdentityLookup().resolveToken('h');
      expect(token).to.not.equal(null);
      expect(token!.knowledgeLimit).to.deep.equal(value);
    });
  }

  it('passes a readable limit through as stored', async () => {
    const limit = { apps: ['a1'], recordGroups: [], records: [] };
    sinon.stub(OAuthAccessToken, 'findOne').returns(storedRow(limit) as never);
    const token = await new MongoIdentityLookup().resolveToken('h');
    expect(token!.knowledgeLimit).to.deep.equal(limit);
  });

  it('leaves the limit out when none is stored', async () => {
    sinon.stub(OAuthAccessToken, 'findOne').returns(storedRow(undefined) as never);
    const token = await new MongoIdentityLookup().resolveToken('h');
    expect(token).to.not.have.property('knowledgeLimit');
  });
});
