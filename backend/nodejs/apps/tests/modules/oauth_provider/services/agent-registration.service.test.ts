import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { AgentRegistrationService } from '../../../../src/modules/oauth_provider/services/agent-registration.service';
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service';
import {
  AgentRegistrationStatus,
  setOAuthTokenRepository,
  type AgentRegistrationDto,
  type NewAgentRegistration,
} from '../../../../src/libs/repositories/oauth-token';
import { setUserRepository } from '../../../../src/libs/repositories/user';
import { setOrgRepository } from '../../../../src/libs/repositories/org';
import { hashOpaqueToken } from '../../../../src/libs/repositories/oauth-token/token-hash';
import { ASSERTION_TYP } from '../../../../src/modules/oauth_provider/constants/agent-auth.constants';
import { InvalidGrantError } from '../../../../src/libs/errors/oauth.errors';
import { createMockLogger } from '../../../helpers/mock-logger';

const ISSUER = 'https://pipeshub.test';
const SECRET = 'test-secret-key-for-signing-tokens-at-least-32-bytes';
const SCOPES = ['org:read', 'offline_access'];
const USER = {
  userId: 'a'.repeat(24),
  orgId: 'b'.repeat(24),
  email: 'Ada@Example.com',
};

/** Just enough of the repository, in memory, with the same conditional writes the real ones make. */
class FakeRepo {
  rows = new Map<string, AgentRegistrationDto>();
  private n = 0;
  async createAgentRegistration(i: NewAgentRegistration) {
    const _id = (++this.n).toString(16).padStart(24, '0');
    const row = {
      ...i,
      _id,
      status: AgentRegistrationStatus.PENDING,
      failedAttempts: 0,
      createdAt: new Date(),
      updatedAt: new Date(),
    } as AgentRegistrationDto;
    this.rows.set(_id, row);
    return { ...row };
  }
  private find(pick: (r: AgentRegistrationDto) => boolean) {
    const r = [...this.rows.values()].find(pick);
    return r ? { ...r } : null;
  }
  async findAgentRegistrationById(id: string) {
    return this.find((r) => r._id === id);
  }
  async findAgentRegistrationByClaimHash(h: string) {
    return this.find((r) => r.claimTokenHash === h);
  }
  async findAgentRegistrationByAttemptHash(h: string) {
    return this.find((r) => r.attemptTokenHash === h);
  }
  async startAgentClaimAttempt(id: string, a: any) {
    const r = this.rows.get(id)!;
    if (r.status !== AgentRegistrationStatus.PENDING) return false;
    Object.assign(r, a, { failedAttempts: 0 });
    return true;
  }
  async recordAgentFailedAttempt(id: string, max: number) {
    const r = this.rows.get(id)!;
    if (r.status !== AgentRegistrationStatus.PENDING) return;
    r.failedAttempts += 1;
    if (r.failedAttempts >= max) r.status = AgentRegistrationStatus.EXPIRED;
  }
  async recordAgentPoll(id: string, at: Date) {
    this.rows.get(id)!.lastPolledAt = at;
  }
  async decideAgentRegistration(id: string, d: any) {
    const r = this.rows.get(id)!;
    if (r.status !== AgentRegistrationStatus.PENDING) return false;
    r.status = d.status;
    if (d.userId) {
      r.claimedUserId = d.userId;
      r.claimedOrgId = d.orgId;
      r.approvedScopes = d.scopes;
    }
    return true;
  }
  async consumeAgentRegistration(id: string, purgeAt: Date) {
    const r = this.rows.get(id)!;
    if (r.status !== AgentRegistrationStatus.CLAIMED) return null;
    r.status = AgentRegistrationStatus.CONSUMED;
    r.purgeAt = purgeAt;
    return { ...r };
  }
  async listAgentRegistrationsForUser(userId: string) {
    return [...this.rows.values()].filter(
      (r) =>
        r.claimedUserId === userId &&
        [
          AgentRegistrationStatus.CLAIMED,
          AgentRegistrationStatus.CONSUMED,
        ].includes(r.status),
    );
  }
  async revokeAgentRegistration(id: string, userId?: string) {
    const r = this.rows.get(id);
    if (
      !r ||
      (userId !== undefined && r.claimedUserId !== userId) ||
      ![
        AgentRegistrationStatus.CLAIMED,
        AgentRegistrationStatus.CONSUMED,
      ].includes(r.status)
    ) {
      return false;
    }
    r.status = AgentRegistrationStatus.REVOKED;
    return true;
  }
  revoked: string[] = [];
  async revokeAccessByHash(hash: string, clientId: string) {
    this.revoked.push(`${clientId}:${hash}`);
    return true;
  }
}

describe('AgentRegistrationService', () => {
  let repo: FakeRepo;
  let service: AgentRegistrationService;
  let tokens: OAuthTokenService;
  let generate: sinon.SinonStub;
  let user: { fullName: string } | null;
  let app: { clientId: string; allowedScopes: string[] };

  beforeEach(() => {
    delete process.env.PIPESHUB_ENABLE_DEVICE_GRANT;
    app = { clientId: 'pipeshub-agent', allowedScopes: [...SCOPES] };
    repo = new FakeRepo();
    setOAuthTokenRepository(repo as any);
    user = { fullName: 'Ada' };
    setUserRepository({ getLiveFields: async () => user } as any);
    setOrgRepository({
      getAccountType: async () => ({ accountType: 'business' }),
    } as any);
    tokens = new OAuthTokenService(
      createMockLogger() as any,
      { algorithm: 'HS256', secret: SECRET } as any,
      ISSUER,
    );
    generate = sinon.stub(tokens, 'generateTokens').callsFake(
      async (_a, _u, _o, scopes) =>
        ({
          accessToken: 'at',
          jti: 'j',
          accessTokenId: 'i',
          tokenType: 'Bearer',
          expiresIn: 3600,
          scope: scopes.join(' '),
        }) as any,
    );
    service = new AgentRegistrationService(
      createMockLogger() as any,
      { getAppByClientId: sinon.stub().callsFake(async () => app) } as any,
      tokens,
      {
        resolveGrantedScopes: (r: string[]) => ({ granted: r, notGranted: [] }),
        getScopeDefinitions: (s: string[]) => s.map((name) => ({ name })),
      } as any,
      { getOrCreate: sinon.stub().resolves('pipeshub-agent') } as any,
      { frontendUrl: 'https://app.test', oauthIssuer: ISSUER } as any,
    );
  });

  afterEach(() => {
    sinon.restore();
    setOAuthTokenRepository();
    setUserRepository();
    setOrgRepository();
    delete process.env.PIPESHUB_ENABLE_DEVICE_GRANT;
  });

  const expectFail = async (p: Promise<unknown>, code: string) => {
    try {
      await p;
    } catch (e: any) {
      expect(
        e instanceof InvalidGrantError ? 'invalid_grant' : e.oauthError,
      ).to.equal(code);
      return;
    }
    throw new Error(`expected ${code}`);
  };
  const register = (email = 'ada@example.com') =>
    service.register('service_auth', email);
  const attemptToken = (uri: string) =>
    new URL(uri).searchParams.get('claim_attempt_token')!;
  /** Registers, then has the person approve it. */
  const claimed = async () => {
    const r = await register();
    await service.completeClaim(
      attemptToken(r.claim.verification_uri),
      r.claim.user_code,
      'approve',
      USER,
    );
    return r;
  };

  describe('register', () => {
    it('answers with the spec shape and stores only hashes', async () => {
      const r = await register();
      expect(r.registration_type).to.equal('service_auth');
      expect(r.registration_id).to.match(/^reg_[0-9a-f]{24}$/);
      expect(r.claim_token).to.match(/^clm_[A-Za-z0-9]{43}$/);
      expect(r.claim_url).to.equal(`${ISSUER}/api/v1/agent/identity/claim`);
      expect(r.post_claim_scopes).to.deep.equal(['org:read']); // offline_access is never granted here
      expect(r.claim.user_code).to.match(/^\d{6}$/);
      expect(r.claim).to.include({ expires_in: 600, interval: 5 });
      expect(r.claim.verification_uri).to.match(
        /^https:\/\/app\.test\/agent\/claim\?claim_attempt_token=/,
      );
      const stored = JSON.stringify([...repo.rows.values()]);
      for (const secret of [
        r.claim_token,
        r.claim.user_code,
        attemptToken(r.claim.verification_uri),
      ]) {
        expect(stored).to.not.include(secret);
      }
      expect(stored).to.include(hashOpaqueToken(r.claim_token));
      // A 6-digit code's bare digest is found by trying all 10^6 codes.
      expect(stored).to.not.include(hashOpaqueToken(r.claim.user_code));
    });

    it('answers an unknown and a known address alike (it never looks users up)', async () => {
      const lookup = sinon.spy(async () => ({ fullName: 'x' }));
      setUserRepository({ getLiveFields: lookup } as any);
      const a = await register('known@example.com');
      const b = await register('nobody@example.com');
      expect(Object.keys(a)).to.deep.equal(Object.keys(b));
      expect(lookup.called).to.equal(false);
    });

    it('refuses the types it does not serve', async () => {
      await expectFail(
        service.register('anonymous', undefined),
        'anonymous_not_enabled',
      );
      await expectFail(
        service.register('identity_assertion', undefined),
        'issuer_not_enabled',
      );
      await expectFail(service.register('other', undefined), 'invalid_request');
      await expectFail(
        service.register(undefined, undefined),
        'invalid_request',
      );
      await expectFail(
        service.register('service_auth', 'not-an-email'),
        'invalid_request',
      );
      await expectFail(
        service.register('service_auth', undefined),
        'invalid_request',
      );
    });

    it('is off when device login is off', async () => {
      process.env.PIPESHUB_ENABLE_DEVICE_GRANT = 'false';
      await expectFail(register(), 'service_auth_not_enabled');
    });
  });

  describe('startClaim', () => {
    it('re-mints the attempt, so the old link and code stop working', async () => {
      const r = await register();
      const again = await service.startClaim(r.claim_token, 'ADA@example.com');
      expect(again.status).to.equal('initiated');
      expect(again.claim_attempt_id).to.match(/^cla_/);
      expect(attemptToken(again.claim_attempt.verification_uri)).to.not.equal(
        attemptToken(r.claim.verification_uri),
      );
      await expectFail(
        service.completeClaim(
          attemptToken(r.claim.verification_uri),
          r.claim.user_code,
          'approve',
          USER,
        ),
        'invalid_request',
      );
      await service.completeClaim(
        attemptToken(again.claim_attempt.verification_uri),
        again.claim_attempt.user_code,
        'approve',
        USER,
      );
    });

    it('has an error for each way it can fail', async () => {
      const r = await register();
      await expectFail(
        service.startClaim('clm_nope', 'ada@example.com'),
        'invalid_claim_token',
      );
      await expectFail(
        service.startClaim(r.claim_token, 'eve@example.com'),
        'invalid_claim_token',
      );
      const [row] = [...repo.rows.values()];
      row.expiresAt = new Date(Date.now() - 1000);
      await expectFail(
        service.startClaim(r.claim_token, 'ada@example.com'),
        'claim_expired',
      );
      row.expiresAt = new Date(Date.now() + 60000);
      row.status = AgentRegistrationStatus.CLAIMED;
      await expectFail(
        service.startClaim(r.claim_token, 'ada@example.com'),
        'claimed_or_in_flight',
      );
    });
  });

  describe('completeClaim', () => {
    it('binds the registration to the session user and org, not to anything in the request', async () => {
      await claimed();
      const [row] = [...repo.rows.values()];
      expect(row).to.include({
        status: 'claimed',
        claimedUserId: USER.userId,
        claimedOrgId: USER.orgId,
      });
    });

    it('refuses another account, whatever the code', async () => {
      const r = await register();
      await expectFail(
        service.completeClaim(
          attemptToken(r.claim.verification_uri),
          r.claim.user_code,
          'approve',
          { ...USER, email: 'eve@example.com' },
        ),
        'access_denied',
      );
      expect([...repo.rows.values()][0].status).to.equal('pending');
    });

    it('refuses an expired attempt', async () => {
      const r = await register();
      [...repo.rows.values()][0].attemptExpiresAt = new Date(Date.now() - 1);
      await expectFail(
        service.completeClaim(
          attemptToken(r.claim.verification_uri),
          r.claim.user_code,
          'approve',
          USER,
        ),
        'claim_expired',
      );
    });

    it('expires the registration after 5 wrong codes, even for the right one after', async () => {
      const r = await register();
      const t = attemptToken(r.claim.verification_uri);
      const wrong = r.claim.user_code === '000000' ? '111111' : '000000';
      for (let i = 0; i < 5; i++) {
        await expectFail(
          service.completeClaim(t, wrong, 'approve', USER),
          'invalid_user_code',
        );
      }
      expect([...repo.rows.values()][0].status).to.equal('expired');
      await expectFail(
        service.completeClaim(t, r.claim.user_code, 'approve', USER),
        'invalid_request',
      );
    });

    it('marks the registration revoked on deny', async () => {
      const r = await register();
      expect(
        await service.completeClaim(
          attemptToken(r.claim.verification_uri),
          undefined,
          'deny',
          USER,
        ),
      ).to.equal('revoked');
      expect([...repo.rows.values()][0].status).to.equal('revoked');
    });

    it('refuses a deny that lost the race to an approval', async () => {
      const r = await register();
      // the approval lands between the deny's read and its write
      sinon.stub(repo, 'decideAgentRegistration').resolves(false);
      await expectFail(
        service.completeClaim(
          attemptToken(r.claim.verification_uri),
          undefined,
          'deny',
          USER,
        ),
        'invalid_request',
      );
    });

    it('keeps a registration that is never claimed only to the end of its window', async () => {
      await register();
      const [row] = [...repo.rows.values()];
      expect(row.purgeAt.getTime()).to.equal(row.expiresAt.getTime());
    });

    it('shows the signed-in person what is being granted', async () => {
      const r = await register();
      const ctx = await service.getClaimContext(
        attemptToken(r.claim.verification_uri),
        USER,
      );
      expect(ctx.scopes).to.deep.equal([{ name: 'org:read' }]);
      expect(ctx.user.email).to.equal(USER.email);
      await expectFail(
        service.getClaimContext(attemptToken(r.claim.verification_uri), {
          ...USER,
          email: 'eve@example.com',
        }),
        'access_denied',
      );
    });
  });

  describe('claim grant', () => {
    it('is pending, then slow_down when polled again too soon', async () => {
      const r = await register();
      await expectFail(
        service.claimGrant(r.claim_token),
        'authorization_pending',
      );
      await expectFail(service.claimGrant(r.claim_token), 'slow_down');
    });

    it('answers expired_token for an unknown token, an expired registration and a closed window', async () => {
      await expectFail(service.claimGrant('clm_nope'), 'expired_token');
      const r = await register();
      const [row] = [...repo.rows.values()];
      row.expiresAt = new Date(Date.now() - 1);
      await expectFail(service.claimGrant(r.claim_token), 'expired_token');
      row.expiresAt = new Date(Date.now() + 60000);
      row.status = AgentRegistrationStatus.EXPIRED;
      await expectFail(service.claimGrant(r.claim_token), 'expired_token');
    });

    it('answers access_denied once the person denied', async () => {
      const r = await register();
      await service.completeClaim(
        attemptToken(r.claim.verification_uri),
        undefined,
        'deny',
        USER,
      );
      await expectFail(service.claimGrant(r.claim_token), 'access_denied');
    });

    it('issues a token and an assertion, with no refresh token', async () => {
      const r = await claimed();
      const out = await service.claimGrant(r.claim_token);
      expect(out.access_token).to.equal('at');
      expect(out).to.not.have.property('refresh_token');
      expect(generate.firstCall.args[4]).to.equal(false); // includeRefreshToken
      expect(generate.firstCall.args[3]).to.deep.equal(['org:read']);
      const claims = jwt.verify(
        out.identity_assertion!,
        SECRET,
      ) as jwt.JwtPayload;
      expect(
        jwt.decode(out.identity_assertion!, { complete: true })!.header.typ,
      ).to.equal(ASSERTION_TYP);
      expect(claims).to.include({
        iss: ISSUER,
        aud: ISSUER,
        sub: r.registration_id,
      });
      expect(claims.jti).to.be.a('string');
      expect(claims.exp! - claims.iat!).to.equal(30 * 24 * 3600);
      expect(Date.parse(out.assertion_expires!)).to.equal(claims.exp! * 1000);
    });

    it('stamps the access token with the registration and keeps the row until the assertion is long over', async () => {
      const r = await claimed();
      const out = await service.claimGrant(r.claim_token);
      expect(generate.firstCall.args[7]).to.include({
        agentRegistrationId: r.registration_id,
      });
      const [row] = [...repo.rows.values()];
      const claims = jwt.decode(out.identity_assertion!) as jwt.JwtPayload;
      expect(row.purgeAt.getTime()).to.be.greaterThan(claims.exp! * 1000);
    });

    it('keeps to what the person approved when the app is widened afterwards', async () => {
      const r = await claimed();
      app.allowedScopes = ['org:read', 'kb:read', 'offline_access'];
      await service.claimGrant(r.claim_token);
      expect(generate.firstCall.args[3]).to.deep.equal(['org:read']);
    });

    it('drops a scope the app no longer allows', async () => {
      app.allowedScopes = ['org:read', 'kb:read', 'offline_access'];
      const r = await claimed();
      app.allowedScopes = ['org:read', 'offline_access'];
      const out = await service.claimGrant(r.claim_token);
      generate.resetHistory();
      await service.jwtBearerGrant(out.identity_assertion!);
      expect(generate.firstCall.args[3]).to.deep.equal(['org:read']);
      app.allowedScopes = ['offline_access'];
      await expectFail(
        service.jwtBearerGrant(out.identity_assertion!),
        'invalid_grant',
      );
    });

    it('works once: afterwards invalid_grant', async () => {
      const r = await claimed();
      await service.claimGrant(r.claim_token);
      await expectFail(service.claimGrant(r.claim_token), 'invalid_grant');
    });

    it('mints exactly one token for two concurrent polls', async () => {
      const r = await claimed();
      const results = await Promise.allSettled([
        service.claimGrant(r.claim_token),
        service.claimGrant(r.claim_token),
      ]);
      expect(results.filter((x) => x.status === 'fulfilled')).to.have.length(1);
      expect(generate.callCount).to.equal(1);
    });

    it('refuses once the person has left the org', async () => {
      const r = await claimed();
      user = null;
      await expectFail(service.claimGrant(r.claim_token), 'access_denied');
    });
  });

  describe('jwt-bearer grant', () => {
    const consumed = async () =>
      (await service.claimGrant((await claimed()).claim_token))
        .identity_assertion!;

    it('exchanges the assertion for a fresh token and never a refresh token', async () => {
      const assertion = await consumed();
      generate.resetHistory();
      const out = await service.jwtBearerGrant(assertion);
      expect(out.access_token).to.equal('at');
      expect(out).to.not.have.property('refresh_token');
      expect(generate.firstCall.args[4]).to.equal(false);
    });

    const forge = (
      claims: object,
      header: object = { typ: ASSERTION_TYP },
      key = SECRET,
    ) =>
      jwt.sign(
        {
          jti: 'j',
          sub: 'reg_' + '1'.padStart(24, '0'),
          iss: ISSUER,
          aud: ISSUER,
          ...claims,
        },
        key,
        {
          algorithm: 'HS256',
          header: { alg: 'HS256', ...header } as any,
          ...('exp' in claims ? {} : { expiresIn: 60 }),
        },
      );

    it('refuses a wrong typ, a wrong aud, a wrong iss, a bad signature and an expired one', async () => {
      await consumed();
      expect(tokens.verifyAgentAssertion(forge({}))).to.match(/^reg_/); // the forgery is good but for the one defect each case adds
      await expectFail(
        service.jwtBearerGrant(forge({}, { typ: 'JWT' })),
        'invalid_grant',
      );
      await expectFail(
        service.jwtBearerGrant(forge({ aud: 'https://elsewhere' })),
        'invalid_grant',
      );
      await expectFail(
        service.jwtBearerGrant(forge({ iss: 'https://elsewhere' })),
        'invalid_grant',
      );
      await expectFail(
        service.jwtBearerGrant(
          forge({}, { typ: ASSERTION_TYP }, 'x'.repeat(40)),
        ),
        'invalid_grant',
      );
      await expectFail(
        service.jwtBearerGrant(
          forge({ exp: Math.floor(Date.now() / 1000) - 10 }),
        ),
        'invalid_grant',
      );
      await expectFail(service.jwtBearerGrant('not.a.jwt'), 'invalid_grant');
    });

    it('refuses an access token presented as an assertion', async () => {
      const accessLike = jwt.sign(
        { sub: 'reg_x', iss: ISSUER, aud: ISSUER },
        SECRET,
        { algorithm: 'HS256', expiresIn: 60 },
      );
      await expectFail(service.jwtBearerGrant(accessLike), 'invalid_grant');
    });

    it('refuses a revoked registration', async () => {
      const assertion = await consumed();
      [...repo.rows.values()][0].status = AgentRegistrationStatus.REVOKED;
      await expectFail(service.jwtBearerGrant(assertion), 'invalid_grant');
    });

    it('refuses an unclaimed registration and an unknown one', async () => {
      const r = await register();
      const { assertion } = tokens.signAgentAssertion(r.registration_id);
      await expectFail(service.jwtBearerGrant(assertion), 'invalid_grant');
      await expectFail(
        service.jwtBearerGrant(
          tokens.signAgentAssertion('reg_' + 'f'.repeat(24)).assertion,
        ),
        'invalid_grant',
      );
    });

    it('refuses once the person is no longer in the org', async () => {
      const assertion = await consumed();
      user = null;
      await expectFail(service.jwtBearerGrant(assertion), 'invalid_grant');
    });
  });

  describe('revocation', () => {
    const OTHER = { ...USER, userId: 'c'.repeat(24) };
    const accessFor = (registrationId: string, extra: object = {}) =>
      jwt.sign(
        {
          iss: ISSUER,
          scope: 'org:read',
          client_id: 'pipeshub-agent',
          agent_registration_id: registrationId,
          ...extra,
        },
        SECRET,
        { algorithm: 'HS256', expiresIn: 60 },
      );

    it('revoking the assertion revokes the registration, so no new token is issued', async () => {
      const out = await service.claimGrant((await claimed()).claim_token);
      expect(await tokens.revokeAgentToken(out.identity_assertion!)).to.equal(
        true,
      );
      expect([...repo.rows.values()][0].status).to.equal(
        AgentRegistrationStatus.REVOKED,
      );
      await expectFail(
        service.jwtBearerGrant(out.identity_assertion!),
        'invalid_grant',
      );
    });

    it('revoking an access token issued under a registration revokes both', async () => {
      const r = await claimed();
      await service.claimGrant(r.claim_token);
      const access = accessFor(r.registration_id);
      expect(await tokens.revokeAgentToken(access)).to.equal(true);
      expect([...repo.rows.values()][0].status).to.equal(
        AgentRegistrationStatus.REVOKED,
      );
      expect(repo.revoked).to.have.length(1);
      expect(repo.revoked[0]).to.match(/^pipeshub-agent:/);
    });

    it('an expired access token issued under a registration still revokes it', async () => {
      const r = await claimed();
      await service.claimGrant(r.claim_token);
      const expired = jwt.sign(
        {
          iss: ISSUER,
          scope: 'org:read',
          client_id: 'pipeshub-agent',
          agent_registration_id: r.registration_id,
          exp: Math.floor(Date.now() / 1000) - 60,
        },
        SECRET,
        { algorithm: 'HS256' },
      );
      expect(await tokens.revokeAgentToken(expired)).to.equal(true);
      expect([...repo.rows.values()][0].status).to.equal(
        AgentRegistrationStatus.REVOKED,
      );
    });

    it('leaves any other token alone', async () => {
      const r = await claimed();
      await service.claimGrant(r.claim_token);
      const plain = jwt.sign({ iss: ISSUER, scope: 'org:read' }, SECRET, {
        algorithm: 'HS256',
        expiresIn: 60,
      });
      expect(await tokens.revokeAgentToken(plain)).to.equal(false);
      expect(
        await tokens.revokeAgentToken(
          accessFor(r.registration_id) + 'x',
        ),
      ).to.equal(false);
      expect(
        await tokens.revokeAgentToken(
          jwt.sign(
            { scope: 'x', agent_registration_id: r.registration_id },
            'y'.repeat(40),
            { algorithm: 'HS256' },
          ),
        ),
      ).to.equal(false);
      expect(await tokens.revokeAgentToken('not.a.jwt')).to.equal(false);
      expect([...repo.rows.values()][0].status).to.equal(
        AgentRegistrationStatus.CONSUMED,
      );
    });

    it('lists and revokes only the signed-in person’s own registrations', async () => {
      const r = await claimed();
      const id = r.registration_id;
      const { registrations } = await service.listForUser(USER);
      expect(registrations).to.have.length(1);
      expect(registrations[0]).to.include({ id, status: 'claimed' });
      expect(JSON.stringify(registrations)).to.not.match(/hash|@/i);
      expect((await service.listForUser(OTHER)).registrations).to.deep.equal(
        [],
      );
      try {
        await service.revokeForUser(id, OTHER);
        throw new Error('expected 404');
      } catch (e: any) {
        expect(e.statusCode ?? e.status).to.equal(404);
      }
      expect([...repo.rows.values()][0].status).to.equal('claimed');
      await service.revokeForUser(id, USER);
      expect([...repo.rows.values()][0].status).to.equal('revoked');
    });
  });
});
