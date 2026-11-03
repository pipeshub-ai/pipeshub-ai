import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { AgentRegistrationController } from '../../../../src/modules/oauth_provider/controller/agent-registration.controller';
import { OAuthProviderController } from '../../../../src/modules/oauth_provider/controller/oauth.provider.controller';
import { OIDCProviderController } from '../../../../src/modules/oauth_provider/controller/oid.provider.controller';
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service';
import {
  DeviceGrantError,
  InvalidGrantError,
} from '../../../../src/libs/errors/oauth.errors';
import { setOAuthTokenRepository } from '../../../../src/libs/repositories/oauth-token';
import {
  AGENT_CLAIM_GRANT,
  JWT_BEARER_GRANT,
} from '../../../../src/modules/oauth_provider/constants/agent-auth.constants';
import {
  tokenSchema,
  agentClaimCompleteSchema,
  agentClaimSchema,
} from '../../../../src/modules/oauth_provider/validators/oauth.validators';
import { createMockLogger } from '../../../helpers/mock-logger';

const res = () => {
  const r: any = { json: sinon.stub(), setHeader: sinon.stub() };
  r.status = sinon.stub().returns(r);
  return r;
};

describe('agent registration HTTP surface', () => {
  afterEach(() => {
    sinon.restore();
    setOAuthTokenRepository();
    delete process.env.PIPESHUB_ENABLE_DEVICE_GRANT;
  });

  describe('AgentRegistrationController', () => {
    it('answers with the OAuth error shape and the status each code owns', async () => {
      const cases: Array<[string, number]> = [
        ['service_auth_not_enabled', 400],
        ['invalid_claim_token', 400],
        ['claim_expired', 410],
        ['access_denied', 403],
      ];
      for (const [code, status] of cases) {
        const service: any = {
          register: sinon.stub().rejects(new DeviceGrantError(code, 'why')),
        };
        const r = res();
        await new AgentRegistrationController(service).identity(
          { body: {} } as any,
          r,
          sinon.stub(),
        );
        expect(r.status.calledWith(status), code).to.equal(true);
        expect(r.json.firstCall.args[0]).to.deep.equal({
          error: code,
          error_description: 'why',
        });
      }
    });

    it('passes unexpected errors on', async () => {
      const boom = new Error('boom');
      const next = sinon.stub();
      await new AgentRegistrationController({
        register: sinon.stub().rejects(boom),
      } as any).identity({ body: {} } as any, res(), next);
      expect(next.calledWith(boom)).to.equal(true);
    });

    it('hands the session user, not the body, to the claim completion', async () => {
      const completeClaim = sinon.stub().resolves('claimed');
      const user = { userId: 'u', orgId: 'o', email: 'a@b.c' };
      const r = res();
      await new AgentRegistrationController({
        completeClaim,
      } as any).claimComplete(
        {
          body: {
            claim_attempt_token: 't',
            user_code: '123456',
            action: 'approve',
            userId: 'evil',
            orgId: 'evil',
          },
          user,
        } as any,
        r,
        sinon.stub(),
      );
      expect(completeClaim.firstCall.args).to.deep.equal([
        't',
        '123456',
        'approve',
        user,
      ]);
      expect(r.json.firstCall.args[0]).to.deep.equal({
        ok: true,
        status: 'claimed',
      });
    });
  });

  describe('validators', () => {
    it('accepts both agent grants on the token endpoint', () => {
      for (const grant_type of [AGENT_CLAIM_GRANT, JWT_BEARER_GRANT]) {
        expect(
          tokenSchema.safeParse({
            body: { grant_type, claim_token: 'clm_x', assertion: 'a.b.c' },
          }).success,
        ).to.equal(true);
      }
    });
    it('requires an email on claim and an action on complete', () => {
      expect(
        agentClaimSchema.safeParse({
          body: { claim_token: 'x', email: 'nope' },
        }).success,
      ).to.equal(false);
      expect(
        agentClaimCompleteSchema.safeParse({
          body: { claim_attempt_token: 'x', action: 'maybe' },
        }).success,
      ).to.equal(false);
      expect(
        agentClaimCompleteSchema.safeParse({
          body: { claim_attempt_token: 'x', action: 'deny' },
        }).success,
      ).to.equal(true);
    });
  });

  describe('token endpoint', () => {
    const controller = (agent: any) =>
      new OAuthProviderController(
        createMockLogger() as any,
        {} as any,
        {} as any,
        {} as any,
        {} as any,
        {} as any,
        {} as any,
        {} as any,
        agent,
      );

    it('serves the claim grant with no client_id and a no-store response', async () => {
      const agent = {
        claimGrant: sinon
          .stub()
          .resolves({ access_token: 'at', identity_assertion: 'ia' }),
      };
      const r = res();
      await controller(agent).token(
        {
          body: { grant_type: AGENT_CLAIM_GRANT, claim_token: 'clm_x' },
          headers: {},
        } as any,
        r,
        sinon.stub(),
      );
      expect(agent.claimGrant.calledWith('clm_x')).to.equal(true);
      expect(r.json.firstCall.args[0]).to.include({ identity_assertion: 'ia' });
      expect(r.setHeader.calledWith('Cache-Control', 'no-store')).to.equal(
        true,
      );
    });

    it('serves the jwt-bearer grant', async () => {
      const agent = {
        jwtBearerGrant: sinon.stub().resolves({ access_token: 'at' }),
      };
      const r = res();
      await controller(agent).token(
        {
          body: { grant_type: JWT_BEARER_GRANT, assertion: 'a.b.c' },
          headers: {},
        } as any,
        r,
        sinon.stub(),
      );
      expect(agent.jwtBearerGrant.calledWith('a.b.c')).to.equal(true);
    });

    it('answers the protocol errors as 400 with the error code', async () => {
      for (const [err, code] of [
        [
          new DeviceGrantError('authorization_pending', 'p'),
          'authorization_pending',
        ],
        [new DeviceGrantError('slow_down', 'p'), 'slow_down'],
        [new DeviceGrantError('expired_token', 'p'), 'expired_token'],
        [new InvalidGrantError('p'), 'invalid_grant'],
      ] as const) {
        const r = res();
        await controller({ claimGrant: sinon.stub().rejects(err) }).token(
          {
            body: { grant_type: AGENT_CLAIM_GRANT, claim_token: 'x' },
            headers: {},
          } as any,
          r,
          sinon.stub(),
        );
        expect(r.status.calledWith(400)).to.equal(true);
        expect(r.json.firstCall.args[0].error).to.equal(code);
      }
    });
  });

  describe('authorization server metadata', () => {
    const oidc = (clientId: string | null) =>
      new OIDCProviderController(
        { getAlgorithm: () => 'HS256' } as any,
        { getAllScopes: () => [] } as any,
        { oauthIssuer: 'https://pipeshub.test', mcpScopes: [] } as any,
        { getOrCreate: sinon.stub().resolves(clientId) } as any,
        false,
      );
    const config = async (c: OIDCProviderController) => {
      const r = res();
      await c.openidConfiguration({} as any, r, sinon.stub());
      return r.json.firstCall.args[0];
    };

    it('advertises both grants and the agent_auth block when enabled', async () => {
      const c = await config(oidc('pipeshub-agent'));
      expect(c.grant_types_supported).to.include.members([
        AGENT_CLAIM_GRANT,
        JWT_BEARER_GRANT,
      ]);
      expect(c.agent_auth).to.deep.equal({
        skill: 'https://pipeshub.test/auth.md',
        identity_endpoint: 'https://pipeshub.test/api/v1/agent/identity',
        claim_endpoint: 'https://pipeshub.test/api/v1/agent/identity/claim',
        identity_types_supported: ['service_auth'],
      });
    });

    it('advertises none of it when device login is off, or the agent app cannot exist', async () => {
      process.env.PIPESHUB_ENABLE_DEVICE_GRANT = 'false';
      for (const c of [
        await config(oidc('pipeshub-agent')),
        await config(oidc(null)),
      ]) {
        expect(c).to.not.have.property('agent_auth');
        expect(c.grant_types_supported).to.not.include(AGENT_CLAIM_GRANT);
        expect(c.grant_types_supported).to.not.include(JWT_BEARER_GRANT);
      }
      delete process.env.PIPESHUB_ENABLE_DEVICE_GRANT;
      const noApp = await config(oidc(null));
      expect(noApp).to.not.have.property('agent_auth');
    });
  });

  describe('revocation', () => {
    const service = () =>
      new OAuthTokenService(
        createMockLogger() as any,
        {
          algorithm: 'HS256',
          secret: 'test-secret-key-for-signing-tokens-at-least-32-bytes',
        } as any,
        'https://pipeshub.test',
      );
    const repo = () => {
      const r = {
        revokeAllForClient: sinon.stub().resolves(),
        revokeAllForUser: sinon.stub().resolves(),
        revokeAllForClientUser: sinon.stub().resolves(),
        revokeAllAgentRegistrations: sinon.stub().resolves(),
        revokeAgentRegistrationsForUser: sinon.stub().resolves(),
      };
      setOAuthTokenRepository(r as any);
      return r;
    };

    it('revoking the agent app revokes every registration; another app does not', async () => {
      const r = repo();
      await service().revokeAllTokensForApp('someone-else');
      expect(r.revokeAllAgentRegistrations.called).to.equal(false);
      await service().revokeAllTokensForApp('pipeshub-agent');
      expect(r.revokeAllAgentRegistrations.calledOnce).to.equal(true);
    });

    it("revoking all of a user's tokens revokes their registrations", async () => {
      const r = repo();
      await service().revokeEveryTokenForUser('u1');
      expect(r.revokeAgentRegistrationsForUser.calledWith('u1')).to.equal(true);
      r.revokeAgentRegistrationsForUser.resetHistory();
      await service().revokeAllTokensForUser('other-app', 'u1');
      expect(r.revokeAgentRegistrationsForUser.called).to.equal(false);
      await service().revokeAllTokensForUser('pipeshub-agent', 'u1');
      expect(r.revokeAgentRegistrationsForUser.calledWith('u1')).to.equal(true);
    });
  });
});
