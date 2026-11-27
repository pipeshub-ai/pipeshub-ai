import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Logger } from '../../../src/libs/services/logger.service';
import {
  makeOrgContext,
  runWithOrgContext,
} from '../../../src/libs/context/orgContext';
import {
  observeTokenRestriction,
  tokenRestrictionsEnforced,
} from '../../../src/libs/utils/token-restrictions.utils';

describe('token restrictions switch', () => {
  const saved = process.env.TOKEN_RESTRICTIONS_MODE;
  let warn: sinon.SinonStub;

  beforeEach(() => {
    warn = sinon.stub(Logger.getInstance(), 'warn');
  });

  afterEach(() => {
    sinon.restore();
    if (saved === undefined) delete process.env.TOKEN_RESTRICTIONS_MODE;
    else process.env.TOKEN_RESTRICTIONS_MODE = saved;
  });

  it('enforces only on the literal value enforce', () => {
    delete process.env.TOKEN_RESTRICTIONS_MODE;
    expect(tokenRestrictionsEnforced()).to.equal(false);
    for (const value of ['log', 'ENFORCE', 'true', '', ' enforce']) {
      process.env.TOKEN_RESTRICTIONS_MODE = value;
      expect(tokenRestrictionsEnforced(), value).to.equal(false);
    }
    process.env.TOKEN_RESTRICTIONS_MODE = 'enforce';
    expect(tokenRestrictionsEnforced()).to.equal(true);
  });

  const req = {
    method: 'GET',
    baseUrl: '/api/v1/x',
    route: { path: '/:id' },
  } as any;
  // The org comes from the bound org context, as the auth middleware sets it.
  const observeIn = (orgId: string, reason: string) =>
    runWithOrgContext(
      makeOrgContext({ orgId, userId: 'u1', kind: 'user', source: 'jwt' }),
      () => observeTokenRestriction(req, reason),
    );

  it('logs the event with route and reason', () => {
    observeIn('org-log', 'missing_scope');
    expect(warn.calledOnce).to.equal(true);
    expect(warn.firstCall.args[1]).to.deep.equal({
      securityEvent: 'token_restriction_observed',
      orgId: 'org-log',
      route: 'GET /api/v1/x/:id',
      reason: 'missing_scope',
    });
  });

  it('takes the org id when the request has no user yet', () => {
    observeTokenRestriction(
      { method: 'POST', baseUrl: '/b' } as any,
      'r',
      'org-arg',
    );
    expect(warn.firstCall.args[1]).to.include({
      orgId: 'org-arg',
      route: 'POST /b',
      reason: 'r',
    });
  });

  it('logs each org, route and reason once per window', () => {
    const clock = sinon.useFakeTimers({ now: Date.now() + 2 * 60 * 60 * 1000 });
    try {
      observeIn('org-rate', 'r');
      observeIn('org-rate', 'r');
      expect(warn.callCount).to.equal(1);
      observeIn('org-rate', 'other');
      observeIn('org-rate-2', 'r');
      expect(warn.callCount).to.equal(3);
      clock.tick(60 * 60 * 1000);
      observeIn('org-rate', 'r');
      expect(warn.callCount).to.equal(4);
    } finally {
      clock.restore();
    }
  });
});
