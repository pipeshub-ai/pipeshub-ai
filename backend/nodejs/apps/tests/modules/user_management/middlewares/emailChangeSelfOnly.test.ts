import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { emailChangeSelfOnly } from '../../../../src/modules/user_management/middlewares/emailChangeSelfOnly';

describe('emailChangeSelfOnly Middleware', () => {
  let res: any;
  let next: sinon.SinonStub;
  const ownerId = new mongoose.Types.ObjectId().toString();
  const otherId = new mongoose.Types.ObjectId().toString();

  const reqFor = (actor: string, targetId: string, body: object) => ({
    user: { userId: actor, orgId: 'org-1' },
    params: { id: targetId },
    body,
  });

  beforeEach(() => {
    res = { status: sinon.stub().returnsThis(), json: sinon.stub() };
    next = sinon.stub();
  });

  it('lets a request through when it does not change the email', () => {
    emailChangeSelfOnly(reqFor(otherId, ownerId, { role: 'admin' }) as any, res, next);
    expect(next.calledOnceWithExactly()).to.be.true;
  });

  it('lets the owner change their own email', () => {
    emailChangeSelfOnly(reqFor(ownerId, ownerId, { email: 'new@x.com' }) as any, res, next);
    expect(next.calledOnceWithExactly()).to.be.true;
  });

  it('refuses a non-owner changing the email, before any user lookup', () => {
    // The middleware runs ahead of userExists precisely so this 403 is
    // returned whether or not the target id exists — a non-owner must not be
    // able to tell the two apart.
    emailChangeSelfOnly(reqFor(otherId, ownerId, { email: 'attacker@x.com' }) as any, res, next);
    expect(next.calledOnce).to.be.true;
    const err = next.firstCall.args[0];
    expect(err).to.be.an('error');
    expect(err.message).to.include('Only the account owner');
    expect(err.statusCode).to.equal(403);
  });

  it('refuses a non-owner even for an unknown target id', () => {
    const unknownId = new mongoose.Types.ObjectId().toString();
    emailChangeSelfOnly(reqFor(otherId, unknownId, { email: 'attacker@x.com' }) as any, res, next);
    expect(next.firstCall.args[0].statusCode).to.equal(403);
  });
});
