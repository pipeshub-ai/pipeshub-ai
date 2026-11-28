import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { readFileSync } from 'fs'
import { join } from 'path'
import { Types } from 'mongoose'
import { oauthPrincipalKind } from '../../../../src/modules/oauth_provider/utils/principal-kind'
import { OAuthTokenService } from '../../../../src/modules/oauth_provider/services/oauth_token.service'
import { ServiceTokenService } from '../../../../src/modules/oauth_provider/services/service-token.service'
import { TokenExchangeService } from '../../../../src/modules/oauth_provider/services/token-exchange.service'
import { PatService } from '../../../../src/modules/oauth_provider/services/pat.service'
import { ScopeValidatorService } from '../../../../src/modules/oauth_provider/services/scope.validator.service'
import { stripTokenDisplayPrefix } from '../../../../src/modules/oauth_provider/constants/constants'
import { OAuthAccessToken } from '../../../../src/modules/oauth_provider/schema/oauth.access_token.schema'
import { NO_APP_CREATOR } from '../../../../src/libs/repositories/oauth-app'
import { OAuthApp } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { createMockLogger } from '../../../helpers/mock-logger'

/**
 * The Node API and the Python services read who is behind an OAuth token with
 * the same rules, from the shapes in `fixtures/minted-token-shapes.json`.
 * Python reads that file in `tests/unit/api/routes/test_service_account_tokens.py`.
 * This side also mints every listed shape through the real minting code and
 * checks the fixture still describes it, so neither side can drift from what
 * is actually issued.
 */

interface Case {
  name: string
  claims: Record<string, unknown>
  kind: string
}

const ids = {
  '<ORG>': new Types.ObjectId().toString(),
  '<SERVICE_ACCOUNT>': new Types.ObjectId().toString(),
  '<PERSON>': new Types.ObjectId().toString(),
  '<ADMIN>': new Types.ObjectId().toString(),
  '<APP_CLIENT>': 'b3b1a8a4-5d0c-4c43-9c39-6a3c44c7f1aa',
}

function fill(value: unknown): unknown {
  if (typeof value === 'string') {
    return Object.entries(ids).reduce((s, [k, v]) => s.split(k).join(v), value)
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, fill(v)]))
  }
  return value
}

const fixture = JSON.parse(
  readFileSync(join(__dirname, '..', 'fixtures', 'minted-token-shapes.json'), 'utf8'),
) as { minted: Case[]; notMinted: Case[] }
const minted = fixture.minted.map((c) => ({ ...c, claims: fill(c.claims) as Record<string, unknown> }))
const notMinted = fixture.notMinted.map((c) => ({ ...c, claims: fill(c.claims) as Record<string, unknown> }))
const byName = (name: string): Case => {
  const found = minted.find((c) => c.name === name)
  if (!found) throw new Error(`no fixture case ${name}`)
  return found
}

const IDENTITY_CLAIMS = ['userId', 'client_id', 'createdBy', 'tokenType', 'act'] as const

function identityOf(accessToken: string): Record<string, unknown> {
  const decoded = jwt.decode(stripTokenDisplayPrefix(accessToken)) as Record<string, unknown>
  return Object.fromEntries(
    IDENTITY_CLAIMS.filter((k) => decoded[k] !== undefined).map((k) => [k, decoded[k]]),
  )
}

describe('oauthPrincipalKind', () => {
  for (const c of [...minted, ...notMinted]) {
    it(`reads "${c.name}" as ${c.kind}`, () => {
      expect(oauthPrincipalKind(c.claims)).to.equal(c.kind)
    })
  }
})

describe('the token shapes the Node API mints', () => {
  const ORG = ids['<ORG>']
  const SA = ids['<SERVICE_ACCOUNT>']
  const PERSON = ids['<PERSON>']
  const ADMIN = ids['<ADMIN>']
  const APP = ids['<APP_CLIENT>']
  const logger = createMockLogger() as any
  const scopes = ['kb:read', 'semantic:write', 'conversation:chat']

  let tokens: OAuthTokenService
  const apps: Record<string, Record<string, unknown>> = {
    [`svc-system:${ORG}`]: { clientId: `svc-system:${ORG}`, createdBy: ADMIN, accessTokenLifetime: 3600 },
    [`pat-system:${ORG}`]: { clientId: `pat-system:${ORG}`, createdBy: ADMIN, accessTokenLifetime: 3600 },
  }

  beforeEach(() => {
    tokens = new OAuthTokenService(logger, { secret: 'test-secret-for-jwt-signing-minimum-length' } as any, 'http://issuer')
    sinon.stub(OAuthAccessToken, 'create').callsFake(async () => ({ _id: new Types.ObjectId() }) as any)
    sinon.stub(OAuthApp, 'findOne').callsFake(((q: { clientId: { $eq: string } }) =>
      Promise.resolve(apps[q.clientId.$eq] ?? null)) as any)
    sinon.stub(Users, 'findOne').returns({
      select: () => ({ lean: () => ({ exec: async () => ({ fullName: 'Nightly sync', isDisabled: false }) }) }),
    } as any)
    sinon.stub(tokens, 'countActiveAccessTokensForUser').resolves(1)
  })

  afterEach(() => sinon.restore())

  const config = { getMcpScopes: sinon.stub().resolves([...scopes, 'agent:execute']) }
  const mintServiceToken = async (): Promise<string> => {
    const service = new ServiceTokenService(
      logger,
      { encrypt: () => 'encrypted' } as any,
      config as any,
      tokens,
      { validateScopesForApp: () => undefined } as any,
      {} as any,
    )
    const created = await service.createToken(ORG, ADMIN, { serviceAccountId: SA, name: 'sync', scopes })
    return created.accessToken
  }

  it('a service account token', async () => {
    const c = byName('service account token')
    const claims = identityOf(await mintServiceToken())
    expect(claims).to.deep.equal(c.claims)
    expect(oauthPrincipalKind(claims)).to.equal('service_account')
  })

  it('a delegated token', async () => {
    const serviceToken = await mintServiceToken()
    sinon.stub(tokens, 'verifyAccessToken').resolves(jwt.decode(stripTokenDisplayPrefix(serviceToken)) as any)
    const exchange = new TokenExchangeService(
      logger,
      tokens,
      {
        resolveSubject: sinon.stub().resolves({ ok: true, userId: PERSON, email: 'person@example.com', fullName: 'A Person' }),
        isDelegationAllowed: sinon.stub().resolves(true),
        recordEvent: sinon.stub().resolves(),
      } as any,
      true,
    )
    const issued = await exchange.exchange({ subjectToken: serviceToken, requestedSubject: 'person@example.com' })

    const claims = identityOf(issued.access_token)
    expect(claims).to.deep.equal(byName('delegated token').claims)
    expect(oauthPrincipalKind(claims)).to.equal('human')
  })

  it('a personal access token', async () => {
    const pats = new PatService(
      logger,
      { encrypt: () => 'encrypted' } as any,
      config as any,
      tokens,
      new ScopeValidatorService(),
    )
    const created = await pats.createToken(ORG, PERSON, 'A Person', { name: 'laptop', scopes: ['kb:read'] } as any)

    const claims = identityOf(created.accessToken)
    expect(claims).to.deep.equal(byName('personal access token').claims)
    expect(oauthPrincipalKind(claims)).to.equal('human')
  })

  // The two grants below mint inside OAuthProviderController; these are the arguments it passes.
  it('a client credentials token, whichever identity its app acts as', async () => {
    for (const [name, identity] of [
      ['client credentials token', undefined],
      ['client credentials token of an app pointed at a service account', SA],
    ] as const) {
      const app = { clientId: APP, createdBy: ADMIN, tokenIdentityUserId: identity, accessTokenLifetime: 3600 }
      const result = await tokens.generateTokens(app as any, null, ORG, scopes, false)
      const claims = identityOf(result.accessToken)
      expect(claims, name).to.deep.equal(byName(name).claims)
      expect(oauthPrincipalKind(claims)).to.equal('app')
    }
  })

  it('an authorization code token', async () => {
    const app = { clientId: APP, createdBy: ADMIN, accessTokenLifetime: 3600 }
    const result = await tokens.generateTokens(app as any, PERSON, ORG, scopes, false)
    const claims = identityOf(result.accessToken)
    expect(claims).to.deep.equal(byName('authorization code token').claims)
    expect(oauthPrincipalKind(claims)).to.equal('human')
  })

  // AgentRegistrationService mints against the instance's own device app, which no person created.
  it('an agent registration token', async () => {
    const app = { clientId: APP, createdBy: NO_APP_CREATOR, accessTokenLifetime: 3600 }
    const result = await tokens.generateTokens(app as any, PERSON, ORG, scopes, false, 'A Person', undefined, {
      agentRegistrationId: 'reg_1',
    })
    const claims = identityOf(result.accessToken)
    expect(claims).to.deep.equal(byName('agent registration token').claims)
    expect(oauthPrincipalKind(claims)).to.equal('human')
  })

  it('lists a case for every kind', () => {
    expect(new Set(minted.map((c) => c.kind))).to.deep.equal(new Set(['human', 'app', 'service_account']))
  })
})
