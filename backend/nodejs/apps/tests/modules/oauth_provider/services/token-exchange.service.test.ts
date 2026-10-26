import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  TokenExchangeService,
  DELEGATED_TOKEN_LIFETIME_SECONDS,
  ACCESS_TOKEN_TYPE,
} from '../../../../src/modules/oauth_provider/services/token-exchange.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema';
import { SERVICE_TOKEN_PREFIX } from '../../../../src/modules/oauth_provider/constants/constants';

const orgId = new mongoose.Types.ObjectId().toString();
const serviceAccountId = new mongoose.Types.ObjectId().toString();
const subjectId = new mongoose.Types.ObjectId().toString();

/** A verified service-token payload, as verifyAccessToken would return it. */
function servicePayload(overrides: Record<string, unknown> = {}) {
  return {
    userId: serviceAccountId,
    orgId,
    client_id: `svc-system:${orgId}`,
    scope: 'kb:read semantic:write',
    tokenType: 'oauth',
    jti: 'jti-1',
    iss: 'http://localhost:3000',
    exp: 0,
    iat: 0,
    ...overrides,
  };
}

function makeService(
  overrides: {
    payload?: Record<string, unknown> | null;
    account?: Record<string, unknown> | null;
    subjectOk?: boolean;
    refusedReason?: string;
    allowed?: boolean;
  } = {},
) {
  const logger = {
    info: sinon.stub(),
    debug: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
  };
  const tokens = {
    verifyAccessToken:
      overrides.payload === null
        ? sinon.stub().rejects(new Error('bad token'))
        : sinon.stub().resolves(overrides.payload ?? servicePayload()),
    generateTokens: sinon.stub().resolves({
      accessToken: 'header.payload.signature',
      accessTokenId: new mongoose.Types.ObjectId().toString(),
      jti: 'issued-jti',
      tokenType: 'Bearer',
      expiresIn: DELEGATED_TOKEN_LIFETIME_SECONDS,
      scope: 'kb:read semantic:write',
    }),
  };
  const delegations = {
    resolveSubject: sinon.stub().resolves(
      overrides.subjectOk === false
        ? { ok: false, refusedReason: overrides.refusedReason ?? 'subject_not_found' }
        : {
            ok: true,
            userId: subjectId,
            email: 'person@example.com',
            fullName: 'A Person',
          },
    ),
    isDelegationAllowed: sinon.stub().resolves(overrides.allowed !== false),
    recordEvent: sinon.stub().resolves(),
  };

  // The service account behind the token, looked up to confirm it is live.
  sinon.stub(Users, 'findOne').returns({
    select: () => ({
      lean: () => ({
        exec: async () =>
          overrides.account === undefined
            ? { isDisabled: false }
            : overrides.account,
      }),
    }),
  } as any);
  sinon
    .stub(OAuthApp, 'findOne')
    .returns({ exec: async () => ({ clientId: `svc-system:${orgId}` }) } as any);

  return {
    // Built as the Enterprise edition does. The exchange logic is shared
    // between editions; only whether it is offered differs, and that has its
    // own test.
    service: new TokenExchangeService(
      logger as any,
      tokens as any,
      delegations as any,
      true,
    ),
    tokens,
    delegations,
  };
}

async function expectRefusal(
  run: () => Promise<unknown>,
  expected: { oauthError?: string; contains?: string },
): Promise<Error> {
  try {
    await run();
  } catch (error) {
    const err = error as Error & { oauthError?: string };
    if (expected.oauthError !== undefined) {
      expect(err.oauthError).to.equal(expected.oauthError);
    }
    if (expected.contains !== undefined) {
      expect(err.message).to.contain(expected.contains);
    }
    return err;
  }
  throw new Error('expected the exchange to be refused');
}

describe('TokenExchangeService', () => {
  afterEach(() => sinon.restore());

  describe('what may be exchanged', () => {
    it('issues a token that speaks as the person, not the service account', async () => {
      const { service, tokens } = makeService();

      const response = await service.exchange({
        subjectToken: 'phsvc_abc',
        requestedSubject: 'person@example.com',
      });

      // The second argument to generateTokens is the userId on the token.
      // This is the whole design: downstream checks read that claim, so the
      // delegated token has to name the person for anything to work.
      expect(tokens.generateTokens.firstCall.args[1]).to.equal(subjectId);
      expect(response.access_token).to.contain(SERVICE_TOKEN_PREFIX);
      expect(response.issued_token_type).to.equal(ACCESS_TOKEN_TYPE);
    });

    it('records the service account as the actor on the issued token', async () => {
      const { service, tokens } = makeService();
      await service.exchange({
        subjectToken: 'phsvc_abc',
        requestedSubject: 'person@example.com',
      });

      const opts = tokens.generateTokens.firstCall.args[7];
      expect(opts.actorUserId).to.equal(serviceAccountId);
    });

    it('gives the token minutes, not days', async () => {
      const { service, tokens } = makeService();
      await service.exchange({
        subjectToken: 'phsvc_abc',
        requestedSubject: 'person@example.com',
      });

      const opts = tokens.generateTokens.firstCall.args[7];
      expect(opts.accessTokenLifetimeOverrideSeconds).to.equal(
        DELEGATED_TOKEN_LIFETIME_SECONDS,
      );
      expect(DELEGATED_TOKEN_LIFETIME_SECONDS).to.be.at.most(900);
    });

    it('never asks for a refresh token', async () => {
      const { service, tokens } = makeService();
      await service.exchange({
        subjectToken: 'phsvc_abc',
        requestedSubject: 'person@example.com',
      });
      expect(tokens.generateTokens.firstCall.args[4]).to.equal(false);
    });

    it('refuses a personal access token', async () => {
      // A PAT already speaks as its owner. Exchanging one would let any
      // employee act as a colleague, which is not delegation but impersonation.
      const { service } = makeService({
        payload: servicePayload({ client_id: `pat-system:${orgId}` }),
      });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phpat_abc',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_grant', contains: 'Only a service token' },
      );
    });

    it('refuses a token that is itself delegated, so delegation cannot chain', async () => {
      // A delegated token is minted against the same client as the service
      // token it came from, so the client check alone does not separate them.
      const { service } = makeService({
        payload: servicePayload({ act: { sub: serviceAccountId } }),
      });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phsvc_delegated',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_grant', contains: 'cannot be exchanged again' },
      );
    });

    it('refuses a token whose service account has been disabled', async () => {
      const { service } = makeService({ account: { isDisabled: true } });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phsvc_abc',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_grant', contains: 'disabled' },
      );
    });

    it('refuses a token whose service account is gone', async () => {
      const { service } = makeService({ account: null });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phsvc_abc',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_grant' },
      );
    });

    it('records the attempt when the service account is disabled', async () => {
      // Worth having in the log: a disabled account whose credential is still
      // being used is either automation nobody switched off or a token
      // somebody kept.
      const { service, delegations } = makeService({
        account: { isDisabled: true },
      });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phsvc_abc',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_grant' },
      );

      expect(delegations.recordEvent.calledOnce).to.equal(true);
      expect(delegations.recordEvent.firstCall.args[0].refusedReason).to.equal(
        'service_account_disabled',
      );
    });

    it('records the attempt when the service account is gone', async () => {
      const { service, delegations } = makeService({ account: null });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phsvc_abc',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_grant' },
      );

      expect(delegations.recordEvent.firstCall.args[0].refusedReason).to.equal(
        'service_account_not_found',
      );
    });

    it('writes nothing for a token that does not verify', async () => {
      // There is nobody to write it about. A row here would let an
      // unauthenticated caller put entries in an organisation's audit trail.
      const { service, delegations } = makeService({ payload: null });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'nonsense',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_grant' },
      );

      expect(delegations.recordEvent.called).to.equal(false);
    });

    it('writes nothing for a personal access token either', async () => {
      const { service, delegations } = makeService({
        payload: servicePayload({ client_id: `pat-system:${orgId}` }),
      });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phpat_abc',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_grant' },
      );

      expect(delegations.recordEvent.called).to.equal(false);
    });

    it('refuses an unverifiable token', async () => {
      const { service } = makeService({ payload: null });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'nonsense',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_grant', contains: 'not valid' },
      );
    });

    it('refuses a subject_token_type it does not issue', async () => {
      const { service } = makeService();
      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phsvc_abc',
            subjectTokenType: 'urn:ietf:params:oauth:token-type:saml2',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_request' },
      );
    });

    it('requires a subject to act as', async () => {
      const { service } = makeService();
      await expectRefusal(
        () => service.exchange({ subjectToken: 'phsvc_abc' }),
        { oauthError: 'invalid_request', contains: 'requested_subject' },
      );
    });
  });

  describe('who may be acted as', () => {
    it('refuses someone not on the allow-list', async () => {
      const { service, delegations } = makeService({ allowed: false });

      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phsvc_abc',
            requestedSubject: 'person@example.com',
          }),
        { oauthError: 'invalid_target' },
      );
      expect(delegations.recordEvent.firstCall.args[0].refusedReason).to.equal(
        'not_on_allow_list',
      );
    });

    it('tells every refusal apart in the log but not in the response', async () => {
      // A service token that could distinguish "no such person" from "not on
      // your list" would be a working directory of the organisation.
      const notFound = makeService({
        subjectOk: false,
        refusedReason: 'subject_not_found',
      });
      const refusedForMissing = await expectRefusal(
        () =>
          notFound.service.exchange({
            subjectToken: 'phsvc_abc',
            requestedSubject: 'nobody@example.com',
          }),
        { oauthError: 'invalid_target' },
      );
      sinon.restore();

      const isAdmin = makeService({
        subjectOk: false,
        refusedReason: 'subject_is_admin',
      });
      const refusedForAdmin = await expectRefusal(
        () =>
          isAdmin.service.exchange({
            subjectToken: 'phsvc_abc',
            requestedSubject: 'boss@example.com',
          }),
        { oauthError: 'invalid_target' },
      );

      expect(refusedForMissing.message).to.equal(refusedForAdmin.message);
      expect(isAdmin.delegations.recordEvent.firstCall.args[0].refusedReason).to.equal(
        'subject_is_admin',
      );
    });

    it('writes an audit record for a refusal, not only for a success', async () => {
      const { service, delegations } = makeService({ allowed: false });
      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phsvc_abc',
            requestedSubject: 'person@example.com',
          }),
        {},
      );

      expect(delegations.recordEvent.calledOnce).to.equal(true);
      expect(delegations.recordEvent.firstCall.args[0].outcome).to.equal(
        'refused',
      );
    });

    it('writes an audit record naming the issued token', async () => {
      const { service, delegations } = makeService();
      await service.exchange({
        subjectToken: 'phsvc_abc',
        requestedSubject: 'person@example.com',
      });

      const record = delegations.recordEvent.firstCall.args[0];
      expect(record.outcome).to.equal('issued');
      expect(record.jti).to.equal('issued-jti');
      expect(record.subjectUserId).to.equal(subjectId);
    });
  });

  describe('what the delegated token may do', () => {
    it('defaults to the service token’s own scopes', async () => {
      const { service, tokens } = makeService();
      await service.exchange({
        subjectToken: 'phsvc_abc',
        requestedSubject: 'person@example.com',
      });
      expect(tokens.generateTokens.firstCall.args[3]).to.deep.equal([
        'kb:read',
        'semantic:write',
      ]);
    });

    it('honours a narrower request', async () => {
      const { service, tokens } = makeService();
      await service.exchange({
        subjectToken: 'phsvc_abc',
        requestedSubject: 'person@example.com',
        scope: 'kb:read',
      });
      expect(tokens.generateTokens.firstCall.args[3]).to.deep.equal(['kb:read']);
    });

    it('refuses to exceed the service token', async () => {
      const { service } = makeService();
      await expectRefusal(
        () =>
          service.exchange({
            subjectToken: 'phsvc_abc',
            requestedSubject: 'person@example.com',
            scope: 'kb:read agent:execute',
          }),
        { oauthError: 'invalid_scope', contains: 'agent:execute' },
      );
    });

    it('drops agent:execute even if a service token somehow carries it', async () => {
      // Minting refuses it, so this is the backstop for a token issued before
      // that rule existed. The write surface stays shut either way.
      const { service, tokens } = makeService({
        payload: servicePayload({ scope: 'kb:read agent:execute' }),
      });
      await service.exchange({
        subjectToken: 'phsvc_abc',
        requestedSubject: 'person@example.com',
      });
      expect(tokens.generateTokens.firstCall.args[3]).to.deep.equal(['kb:read']);
    });
  });

  /**
   * The Community edition refuses the capability rather than merely lacking a
   * screen to reach it.
   *
   * This is the check that makes the edition boundary hold: the allow-list is
   * an ordinary database collection, so without this a row written straight
   * into Mongo would be enough to turn delegation on in a build that does not
   * offer it.
   */
  describe('an edition that does not offer delegation', () => {
    it('refuses before reading anything', async () => {
      const { service, delegations, tokens } = makeService()
      const community = new TokenExchangeService(
        (service as any).logger,
        (service as any).oauthTokenService,
        (service as any).delegationService,
        false,
      )

      let message = ''
      let code = ''
      try {
        await community.exchange({
          subjectToken: 'svc-token',
          requestedSubject: 'person@example.com',
        } as any)
      } catch (err: any) {
        code = err.code ?? err.error ?? ''
        message = err.message ?? ''
      }

      // The error class prefixes and upper-cases its code.
      expect(code.toLowerCase()).to.contain('unsupported_grant_type')
      expect(message).to.contain('Enterprise')

      // Nothing was looked up, so a hand-written allow-list row changes
      // nothing here.
      expect((delegations.isDelegationAllowed as any).called).to.equal(false)
      expect((delegations.resolveSubject as any).called).to.equal(false)
      void tokens
    })
  })
})
