import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  refuseServiceAccountCaller,
  SERVICE_ACCOUNT_CANNOT_MINT_MESSAGE,
} from '../../../../src/modules/user_management/middlewares/refuseServiceAccountCaller';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';

function stubCaller(doc: Record<string, unknown> | null) {
  return sinon.stub(Users, 'findOne').returns({
    select: sinon.stub().returns({
      lean: sinon.stub().returns({ exec: sinon.stub().resolves(doc) }),
    }),
  } as any);
}

describe('refuseServiceAccountCaller', () => {
  const userId = new mongoose.Types.ObjectId().toString();
  const orgId = new mongoose.Types.ObjectId().toString();

  afterEach(() => sinon.restore());

  it('refuses a service account', async () => {
    // Otherwise a read-only, expiring service token could mint itself a
    // personal access token with every scope and no expiry.
    stubCaller({ kind: 'service' });
    const next = sinon.stub();

    await refuseServiceAccountCaller({ user: { userId, orgId } } as any, {} as any, next);

    expect(next.calledOnce).to.equal(true);
    expect(next.firstCall.args[0]).to.exist;
    expect(next.firstCall.args[0].message).to.equal(
      SERVICE_ACCOUNT_CANNOT_MINT_MESSAGE,
    );
  });

  it('lets a person through', async () => {
    stubCaller({ kind: 'human' });
    const next = sinon.stub();

    await refuseServiceAccountCaller({ user: { userId, orgId } } as any, {} as any, next);

    expect(next.calledOnce).to.equal(true);
    expect(next.firstCall.args).to.have.length(0);
  });

  it('lets a record predating the kind field through', async () => {
    stubCaller({});
    const next = sinon.stub();

    await refuseServiceAccountCaller({ user: { userId, orgId } } as any, {} as any, next);

    expect(next.firstCall.args).to.have.length(0);
  });

  it('decides from the database, not from a claim on the token', async () => {
    // A token saying "human" does not make it so.
    const findOne = stubCaller({ kind: 'service' });
    const next = sinon.stub();

    await refuseServiceAccountCaller(
      { user: { userId, orgId, kind: 'human' } } as any,
      {} as any,
      next,
    );

    expect(findOne.calledOnce).to.equal(true);
    expect(next.firstCall.args[0].message).to.equal(
      SERVICE_ACCOUNT_CANNOT_MINT_MESSAGE,
    );
  });

  it('refuses a caller whose record has gone', async () => {
    stubCaller(null);
    const next = sinon.stub();

    await refuseServiceAccountCaller({ user: { userId, orgId } } as any, {} as any, next);

    expect(next.firstCall.args[0]).to.exist;
  });
});
