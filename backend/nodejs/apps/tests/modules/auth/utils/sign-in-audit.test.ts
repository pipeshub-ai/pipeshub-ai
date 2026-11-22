import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import axios from 'axios';
import winston from 'winston';
import mongoose from 'mongoose';
import { generateKeyPairSync, KeyObject } from 'crypto';
import { LoginTicket } from 'google-auth-library';
import { stubGoogleIdTokenCheck } from '../../../helpers/google-id-token';
import { AUTH_AUDIT_REASONS } from '../../../../src/modules/auth/utils/sign-in-audit';
import { refusalReason } from '../../../../src/libs/services/audit';
import { Logger, SNAKE_TOKEN } from '../../../../src/libs/services/logger.service';
import { UserAccountController } from '../../../../src/modules/auth/controller/userAccount.controller';
import { UserController } from '../../../../src/modules/user_management/controller/users.controller';
import { validateAzureAdUser } from '../../../../src/modules/auth/utils/azureAdTokenValidation';
import * as credentialRepo from '../../../../src/libs/repositories/credential';
import * as userRepo from '../../../../src/libs/repositories/user';
import * as activityRepo from '../../../../src/libs/repositories/activity';
import { runWithRequestContext, setContextIdentity } from '../../../../src/libs/context/request-context';

const orgId = new mongoose.Types.ObjectId().toHexString();
const userId = new mongoose.Types.ObjectId().toHexString();
const adminId = new mongoose.Types.ObjectId().toHexString();
const EMAILS = ['ada@acme.test', 'ada.upn@acme.test', 'mallory@acme.test'];
/** The calls under test run in a request context with this id prefix; only those lines are captured. */
const OUR_REQUESTS = 'n4atest';
const inRequest = <T>(fn: () => Promise<T>): Promise<T> => runWithRequestContext({ rootId: `${OUR_REQUESTS}-unit` }, fn);

describe('sign-in audit: reason lists and the sites a request cannot reach', () => {
  let records: Record<string, any>[];
  const audits = () => records.filter((r) => r.service === 'audit').map((r) => [r.message, r.outcome, r.reason, r.actor, r.target]);
  const noEmails = () => {
    for (const r of records) for (const e of EMAILS) expect(JSON.stringify(r)).to.not.include(e);
  };
  const controller = () =>
    new UserAccountController(
      ...([
        { jwtSecret: 'j', scopedJwtSecret: 's', cmBackend: 'http://cm' },
        { updateUser: async () => ({ statusCode: 200, data: {} }) },
        {},
        {},
        { getConfig: async () => ({ data: { clientId: 'c', tenantId: '11111111-1111-1111-1111-111111111111' } }) },
        Logger.getInstance({ service: 'UserAccountController' }),
        {},
        {},
      ] as unknown as ConstructorParameters<typeof UserAccountController>),
    ) as any as UserAccountController & Record<string, any>;

  beforeEach(() => {
    records = [];
    // Every winston root, not just the current one: modules keep the logger they got at load, and other test files
    // replace the root, so a per-instance stub misses lines depending on test order. Only this test's requests
    // count: other work in the same mocha worker (timers, sweeps, reconnects) logs meanwhile.
    sinon.stub(winston.Logger.prototype, 'log').callsFake(function (this: unknown, info: Record<string, any>) {
      if (String(info.request_id).startsWith(OUR_REQUESTS)) records.push(info);
      return this;
    } as any);
  });
  afterEach(() => sinon.restore());

  it('[guard] every reason is a snake_case code, and every event that audits failures has some', () => {
    for (const [event, reasons] of Object.entries(AUTH_AUDIT_REASONS)) {
      expect(reasons, event).to.not.be.empty;
      for (const reason of reasons) expect(SNAKE_TOKEN.test(reason), `${event}: ${reason}`).to.equal(true);
    }
  });

  // The sign-in route picks the account by the provider's email, so these can only fire on a direct call.
  it('tags the Google method step: no payload, and an email that is not the account', async () => {
    const google = (payload: unknown) =>
      stubGoogleIdTokenCheck().resolves({ getPayload: () => payload } as unknown as LoginTicket);
    const user = { _id: userId, orgId, email: EMAILS[0] };
    google(undefined);
    const noPayload = await inRequest(() => controller().authenticateWithGoogle(user, 't', '1.1.1.1')).catch((e: unknown) => e);
    expect(refusalReason(noPayload)).to.equal('sso_token_invalid');
    sinon.restore();
    // Every winston root, not just the current one: modules keep the logger they got at load, and other test files
    // replace the root, so a per-instance stub misses lines depending on test order. Only this test's requests
    // count: other work in the same mocha worker (timers, sweeps, reconnects) logs meanwhile.
    sinon.stub(winston.Logger.prototype, 'log').callsFake(function (this: unknown, info: Record<string, any>) {
      if (String(info.request_id).startsWith(OUR_REQUESTS)) records.push(info);
      return this;
    } as any);
    google({ email: EMAILS[2] });
    const mismatch = await inRequest(() => controller().authenticateWithGoogle(user, 't', '1.1.1.1')).catch((e: unknown) => e);
    expect(refusalReason(mismatch)).to.equal('sso_email_mismatch');
    noEmails();
  });

  it('tags the Microsoft method step: an identity that is not the account', async () => {
    const azure = await import('../../../../src/modules/auth/utils/azureAdTokenValidation');
    sinon.stub(azure, 'validateAzureAdUser').resolves({ email: EMAILS[2], tid: 'x' } as any);
    const error = await controller()
      .authenticateWithMicrosoft({ _id: userId, orgId, email: EMAILS[0] }, { idToken: 't' }, '1.1.1.1')
      .catch((e: unknown) => e);
    expect(refusalReason(error)).to.equal('sso_email_mismatch');
  });

  it('tags a Microsoft token whose payload is a string', async () => {
    const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
    const jwk = { ...(publicKey as KeyObject).export({ format: 'jwk' }), kid: 'k' };
    sinon.stub(axios, 'get').callsFake(async (url: string) =>
      (url.endsWith('openid-configuration') ? { data: { issuer: 'i', jwks_uri: 'https://keys' } } : { data: { keys: [jwk] } }) as any);
    const token = jwt.sign('just a string', privateKey, { algorithm: 'RS256', keyid: 'k' });
    const error = await validateAzureAdUser({ idToken: token }, { clientId: 'c' }).catch((e: unknown) => e);
    expect(refusalReason(error)).to.equal('sso_token_invalid');
  });

  it('T12 a UPN corrected to the mail address is one auth.email.change line, without either address', async () => {
    const correctEmail = sinon.stub().resolves();
    sinon.stub(userRepo, 'getUserRepository').returns({ correctEmail } as any);
    const target = { _id: userId, orgId, email: EMAILS[1] };
    await inRequest(() => controller().correctEmailFromToken({ email: EMAILS[0], preferred_username: EMAILS[1] }, target, 'Microsoft auth', true));
    expect(correctEmail.calledOnce).to.equal(true);
    expect(audits()).to.deep.equal([['auth.email.change', 'success', 'sso_mail_claim', userId, null]]);
    noEmails();
  });

  it('T13 a correction that clashes with another account is one warning with the user id, without either address', async () => {
    sinon.stub(userRepo, 'getUserRepository').returns({ correctEmail: sinon.stub().rejects(new Error(`E11000 duplicate key: ${EMAILS[0]}`)) } as any);
    await inRequest(() => controller().correctEmailFromToken({ email: EMAILS[0], preferred_username: EMAILS[1] }, { _id: userId, orgId, email: EMAILS[1] }, 'Microsoft auth', true));
    const warnings = records.filter((r) => r.level === 'warning');
    expect(warnings).to.have.length(1);
    expect(warnings[0]).to.include({ message: 'auth.email.correct_failed', user_id: userId });
    expect(audits()).to.deep.equal([]);
    noEmails();
  });

  it('T21 account setup is a success line; setup on a locked account writes none', async () => {
    const creds: Record<string, any> = { userId, orgId, isBlocked: false, wrongCredentialCount: 0, blockExpiresAt: null };
    sinon.stub(credentialRepo, 'getCredentialRepository').returns({ find: async () => ({ ...creds }), savePassword: async () => undefined } as any);
    sinon.stub(activityRepo, 'getActivityRepository').returns({ record: async () => undefined } as any);
    const setup = async () => {
      const next = sinon.stub();
      const res = { status: () => res, json: () => res } as any;
      await inRequest(async () => {
        setContextIdentity({ orgId, userId });
        await controller().userAccountSetup(
          { body: { fullName: 'Ada', password: 'Correct-Horse-9!', email: EMAILS[0] }, user: { userId, orgId }, ip: '1.1.1.1' } as any, res, next,
        );
      });
      return next;
    };
    expect((await setup()).called).to.equal(false);
    Object.assign(creds, { isBlocked: true, blockExpiresAt: new Date(Date.now() + 60_000) });
    expect((await setup()).calledOnce).to.equal(true);
    expect(audits()).to.deep.equal([['auth.account.setup', 'success', null, userId, null]]);
    noEmails();
  });

  it('T24 an admin unblock is auth.account.unlock with the admin as actor and the account as target', async () => {
    sinon.stub(credentialRepo, 'getCredentialRepository').returns({ unblock: async () => true } as any);
    const users = new UserController(...([{}, {}, {}, Logger.getInstance(), {}, {}] as unknown as ConstructorParameters<typeof UserController>));
    const res = { status: () => res, json: () => res } as any;
    await inRequest(async () => {
      setContextIdentity({ orgId, userId: adminId });
      await users.unblockUser({ params: { id: userId }, user: { orgId, userId: adminId } } as any, res, sinon.stub());
    });
    expect(audits()).to.deep.equal([['auth.account.unlock', 'success', 'admin', adminId, userId]]);
  });
});
