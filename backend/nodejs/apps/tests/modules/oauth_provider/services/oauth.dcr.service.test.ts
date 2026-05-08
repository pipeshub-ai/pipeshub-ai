/**
 * Unit tests for OAuthDcrService — RFC 7591 Dynamic Client Registration +
 * RFC 7592 Client Configuration.
 *
 * Mirrors the structure of the existing oauth.app.service.test.ts: chai +
 * sinon + mocha, no DB, all collaborators stubbed.
 *
 * The test cases roughly track better-auth's `register.test.ts` checklist:
 *   - confidential vs public secret issuance (RFC 7591 §3.2.1)
 *   - grant_types ↔ response_types cross-validation
 *   - public-client semantics (token_endpoint_auth_method = "none")
 *   - client_credentials rejected for DCR (no authenticated identity)
 *   - RFC 7592 GET / PUT / DELETE management
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import {
  DcrMetadataError,
  OAuthDcrService,
} from '../../../../src/modules/oauth_provider/services/oauth.dcr.service'
import {
  IOAuthApp,
  OAuthAppRegisteredVia,
  OAuthAppStatus,
  OAuthGrantType,
} from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'
import { DefaultMcpScopes } from '../../../../src/modules/oauth_provider/config/scopes.config'

function buildFakeApp(overrides: Partial<IOAuthApp> = {}): IOAuthApp {
  const now = new Date()
  const _id = new Types.ObjectId()
  // Cast to IOAuthApp — we only touch the fields the service reads.
  return {
    _id,
    slug: 'fake-slug',
    clientId: 'fake-client-id',
    clientSecretEncrypted: '',
    name: 'Fake App',
    redirectUris: ['http://127.0.0.1:9999/cb'],
    allowedGrantTypes: [
      OAuthGrantType.AUTHORIZATION_CODE,
      OAuthGrantType.REFRESH_TOKEN,
    ],
    allowedScopes: ['openid', 'profile', 'email'],
    status: OAuthAppStatus.ACTIVE,
    isConfidential: false,
    accessTokenLifetime: 3600,
    refreshTokenLifetime: 2_592_000,
    isDeleted: false,
    registeredVia: OAuthAppRegisteredVia.DCR,
    clientProfile: 'mcp.dcr.public',
    registrationAccessTokenHash: 'hash',
    tokenEndpointAuthMethod: 'none',
    responseTypes: ['code'],
    contacts: undefined,
    softwareId: undefined,
    softwareVersion: undefined,
    applicationType: 'native',
    createdAt: now,
    updatedAt: now,
    save: sinon.stub().resolves() as unknown,
    ...overrides,
  } as unknown as IOAuthApp
}

describe('OAuthDcrService', () => {
  let service: OAuthDcrService
  let mockLogger: any
  let mockAppService: any
  let mockTokenService: any
  let mockScopeValidator: any
  let mockAppConfig: any

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
      debug: sinon.stub(),
    }
    mockAppService = {
      validateRedirectUrisPublic: sinon.stub(),
      validateGrantTypesPublic: sinon.stub(),
      // Default: createDcrApp returns a confidential app with secret.
      createDcrApp: sinon.stub().resolves({
        app: buildFakeApp({
          isConfidential: true,
          tokenEndpointAuthMethod: 'client_secret_basic',
        }),
        clientSecret: 'plaintext-secret',
      }),
    }
    mockTokenService = {
      generateRegistrationAccessToken: sinon
        .stub()
        .returns({ token: 'rat_test_plain', hash: 'rat_test_hash' }),
      revokeAllTokensForApp: sinon.stub().resolves(),
    }
    mockScopeValidator = {
      parseScopes: sinon.stub().callsFake((s: string) => s.split(/\s+/).filter(Boolean)),
      validateRequestedScopes: sinon.stub(),
    }
    mockAppConfig = { oauthIssuer: 'http://localhost:3000' }

    service = new OAuthDcrService(
      mockLogger,
      mockAppService,
      mockTokenService,
      mockScopeValidator,
      mockAppConfig,
    )
  })

  afterEach(() => {
    sinon.restore()
  })

  // ----- register() ---------------------------------------------------------

  describe('register', () => {
    it('issues a confidential client by default with client_secret', async () => {
      const result = await service.register({
        client_name: 'My App',
        redirect_uris: ['https://example.com/cb'],
      })
      expect(result.client_id).to.be.a('string')
      expect(result.client_secret).to.equal('plaintext-secret')
      // RFC 7591 §3.2.1 — 0 means never expires.
      expect(result.client_secret_expires_at).to.equal(0)
      expect(result.token_endpoint_auth_method).to.equal('client_secret_basic')
      expect(result.registration_access_token).to.equal('rat_test_plain')
      expect(result.registration_client_uri).to.equal(
        `http://localhost:3000/api/v1/oauth2/register/${result.client_id}`,
      )
      expect(result.client_profile).to.equal('mcp.dcr.public')
    })

    it('omits client_secret for public clients (token_endpoint_auth_method=none)', async () => {
      mockAppService.createDcrApp.resolves({
        app: buildFakeApp({ isConfidential: false, tokenEndpointAuthMethod: 'none' }),
        clientSecret: undefined,
      })
      const result = await service.register({
        client_name: 'Cursor',
        redirect_uris: ['http://127.0.0.1:9999/cb'],
        token_endpoint_auth_method: 'none',
      })
      expect(result.client_id).to.be.a('string')
      // Per RFC 7591 §3.2.1 — public clients MUST NOT receive a secret.
      expect(result).to.not.have.property('client_secret')
      expect(result).to.not.have.property('client_secret_expires_at')
      expect(result.token_endpoint_auth_method).to.equal('none')
      expect(result.registration_access_token).to.equal('rat_test_plain')
    })

    it('rejects authorization_code grant without redirect_uris', async () => {
      try {
        await service.register({ client_name: 'no-uris' })
        expect.fail('Should have thrown')
      } catch (e) {
        expect(e).to.be.instanceOf(DcrMetadataError)
        expect((e as DcrMetadataError).dcrCode).to.equal('invalid_redirect_uri')
      }
    })

    it('rejects mismatched grant_types/response_types', async () => {
      try {
        await service.register({
          client_name: 'mismatch',
          redirect_uris: ['https://example.com/cb'],
          grant_types: ['authorization_code'],
          // Missing 'code' in response_types: per RFC 7591 §2.1 this MUST fail.
          response_types: [],
        })
        expect.fail('Should have thrown')
      } catch (e) {
        expect(e).to.be.instanceOf(BadRequestError)
        expect((e as Error).message).to.include('response_types must include "code"')
      }
    })

    it('rejects client_credentials grant for DCR clients', async () => {
      // No authenticated identity behind a DCR registration; allowing
      // client_credentials would let any anon registrant mint app-only tokens.
      try {
        await service.register({
          client_name: 'evil',
          redirect_uris: ['http://127.0.0.1:9999/cb'],
          grant_types: ['client_credentials'],
        })
        expect.fail('Should have thrown')
      } catch (e) {
        expect(e).to.be.instanceOf(BadRequestError)
        expect((e as Error).message).to.include('client_credentials')
      }
    })

    it('caps requested scopes at the public scope set', async () => {
      // Simulate ScopeValidatorService rejecting a privileged scope.
      mockScopeValidator.validateRequestedScopes.callsFake(
        (requested: string[]) => {
          if (requested.includes('config:write')) {
            throw new Error('disallowed')
          }
        },
      )
      try {
        await service.register({
          client_name: 'overreach',
          redirect_uris: ['https://example.com/cb'],
          scope: 'openid config:write',
        })
        expect.fail('Should have thrown')
      } catch (e) {
        expect((e as Error).message).to.include('disallowed')
      }
    })

    it('defaults scope to DefaultMcpScopes when none requested', async () => {
      await service.register({
        client_name: 'defaults',
        redirect_uris: ['https://example.com/cb'],
      })
      // The service should call createDcrApp with the MCP default scope profile.
      const args = mockAppService.createDcrApp.firstCall.args[0]
      expect(args.allowedScopes).to.deep.equal(DefaultMcpScopes)
    })

    it('passes through optional metadata to createDcrApp verbatim', async () => {
      await service.register({
        client_name: 'meta',
        redirect_uris: ['https://example.com/cb'],
        client_uri: 'https://example.com',
        logo_uri: 'https://example.com/logo.png',
        policy_uri: 'https://example.com/privacy',
        tos_uri: 'https://example.com/terms',
        contacts: ['ops@example.com'],
        software_id: 'sw-1',
        software_version: 'v1',
        application_type: 'native',
      })
      const args = mockAppService.createDcrApp.firstCall.args[0]
      expect(args.homepageUrl).to.equal('https://example.com')
      expect(args.logoUrl).to.equal('https://example.com/logo.png')
      expect(args.privacyPolicyUrl).to.equal('https://example.com/privacy')
      expect(args.termsOfServiceUrl).to.equal('https://example.com/terms')
      expect(args.contacts).to.deep.equal(['ops@example.com'])
      expect(args.softwareId).to.equal('sw-1')
      expect(args.softwareVersion).to.equal('v1')
      expect(args.applicationType).to.equal('native')
      expect(args.clientProfile).to.equal('mcp.dcr.public')
    })
  })

  // ----- getRegistrationMetadata() ----------------------------------------

  describe('getRegistrationMetadata', () => {
    it('echoes the persisted client without re-issuing the registration_access_token', () => {
      const app = buildFakeApp({
        clientId: 'cid-abc',
        name: 'Stored App',
        redirectUris: ['http://127.0.0.1:9999/cb'],
        allowedScopes: ['openid'],
      })
      const out = service.getRegistrationMetadata(app)
      expect(out.client_id).to.equal('cid-abc')
      expect(out.client_name).to.equal('Stored App')
      expect(out.redirect_uris).to.deep.equal(['http://127.0.0.1:9999/cb'])
      expect(out.scope).to.equal('openid')
      // Critical: GET MUST NOT mint a fresh token.
      expect(out.registration_access_token).to.be.undefined
      expect(out.registration_client_uri).to.equal(
        'http://localhost:3000/api/v1/oauth2/register/cid-abc',
      )
    })
  })

  // ----- updateRegistration() ---------------------------------------------

  describe('updateRegistration', () => {
    it('updates mutable fields and persists', async () => {
      const app = buildFakeApp()
      const out = await service.updateRegistration(app, {
        client_name: 'Renamed',
        client_uri: 'https://renamed.example/',
        logo_uri: 'https://renamed.example/logo.png',
      })
      expect(out.client_name).to.equal('Renamed')
      expect(app.name).to.equal('Renamed')
      expect(app.homepageUrl).to.equal('https://renamed.example/')
      expect(app.logoUrl).to.equal('https://renamed.example/logo.png')
      expect((app.save as sinon.SinonStub).calledOnce).to.be.true
    })

    it('refuses to expand grant_types beyond the registration ceiling', async () => {
      const app = buildFakeApp({
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE],
      })
      try {
        await service.updateRegistration(app, {
          grant_types: ['authorization_code', 'refresh_token'],
        })
        expect.fail('Should have thrown')
      } catch (e) {
        expect(e).to.be.instanceOf(BadRequestError)
        expect((e as Error).message).to.include('grant_types not permitted')
      }
    })

    it('refuses client_credentials on update too (defence in depth)', async () => {
      const app = buildFakeApp({
        // Even if somehow the app's stored allowedGrantTypes contained client_credentials,
        // we still refuse it on the DCR update path.
        allowedGrantTypes: [
          OAuthGrantType.AUTHORIZATION_CODE,
          OAuthGrantType.CLIENT_CREDENTIALS,
        ],
      })
      try {
        await service.updateRegistration(app, {
          grant_types: ['client_credentials'],
        })
        expect.fail('Should have thrown')
      } catch (e) {
        expect(e).to.be.instanceOf(BadRequestError)
        expect((e as Error).message).to.include('client_credentials')
      }
    })

    it('refuses to expand scopes beyond the registration ceiling', async () => {
      const app = buildFakeApp({ allowedScopes: ['openid'] })
      try {
        await service.updateRegistration(app, {
          scope: 'openid org:read',
        })
        expect.fail('Should have thrown')
      } catch (e) {
        expect(e).to.be.instanceOf(BadRequestError)
        expect((e as Error).message).to.include('scope not permitted')
      }
    })

    it('flips isConfidential when token_endpoint_auth_method changes', async () => {
      const app = buildFakeApp({
        isConfidential: true,
        tokenEndpointAuthMethod: 'client_secret_basic',
      })
      await service.updateRegistration(app, {
        token_endpoint_auth_method: 'none',
      })
      expect(app.tokenEndpointAuthMethod).to.equal('none')
      expect(app.isConfidential).to.be.false
    })
  })

  // ----- deleteRegistration() ---------------------------------------------

  describe('deleteRegistration', () => {
    it('soft-deletes, clears registration token, revokes outstanding tokens', async () => {
      const app = buildFakeApp({
        isDeleted: false,
        status: OAuthAppStatus.ACTIVE,
        registrationAccessTokenHash: 'still-here',
      })
      await service.deleteRegistration(app)
      expect(app.isDeleted).to.be.true
      expect(app.status).to.equal(OAuthAppStatus.REVOKED)
      expect(app.registrationAccessTokenHash).to.be.undefined
      expect((app.save as sinon.SinonStub).calledOnce).to.be.true
      expect(mockTokenService.revokeAllTokensForApp.calledOnceWith(app.clientId))
        .to.be.true
    })

    it('revokes outstanding tokens before persisting soft-delete', async () => {
      const app = buildFakeApp()
      const callOrder: string[] = []
      mockTokenService.revokeAllTokensForApp.callsFake(async () => {
        callOrder.push('revoke')
      })
      ;(app.save as sinon.SinonStub).callsFake(async () => {
        callOrder.push('save')
      })

      await service.deleteRegistration(app)

      expect(callOrder).to.deep.equal(['revoke', 'save'])
    })
  })
})
