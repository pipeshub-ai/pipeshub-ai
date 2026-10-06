import 'reflect-metadata'
import { expect } from 'chai'
import jwt from 'jsonwebtoken'
import { AuthTokenService } from '../../../src/libs/services/authtoken.service'
import { JwtServiceTokenIssuer } from '../../../src/libs/services/service-token.issuer'
import { Logger } from '../../../src/libs/services/logger.service'
import { TokenScopes } from '../../../src/libs/enums/token-scopes.enum'

const JWT_SECRET = 'issuer-test-jwt-secret'
const SCOPED_SECRET = 'issuer-test-scoped-secret'

describe('JwtServiceTokenIssuer (PH02-07)', () => {
  let auth: AuthTokenService
  let issuer: JwtServiceTokenIssuer

  before(() => {
    Logger.getInstance({ service: 'test', level: 'error' })
  })
  beforeEach(() => {
    auth = new AuthTokenService(JWT_SECRET, SCOPED_SECRET)
    issuer = new JwtServiceTokenIssuer(auth)
  })

  const base = { userId: 'u1', orgId: 'o1', scopes: [TokenScopes.CONVERSATION_PERMISSIONS] } as const

  it('issues a token that lives for the requested ttl', () => {
    const one = jwt.decode(issuer.issue(base, '1m')) as { iat: number; exp: number }
    const five = jwt.decode(issuer.issue(base, '5m')) as { iat: number; exp: number }
    expect(one.exp - one.iat).to.equal(60)
    expect(five.exp - five.iat).to.equal(300)
  })

  it('includes conversationId, runId and isServiceAccount only when given', () => {
    const plain = jwt.decode(issuer.issue(base, '5m')) as Record<string, unknown>
    expect(Object.keys(plain).sort()).to.deep.equal(['exp', 'iat', 'orgId', 'scopes', 'userId'])

    const full = jwt.decode(
      issuer.issue({ ...base, conversationId: 'c1', runId: 'r1', isServiceAccount: true }, '5m'),
    ) as Record<string, unknown>
    expect(full).to.include({ conversationId: 'c1', runId: 'r1', isServiceAccount: true })

    const falsy = jwt.decode(issuer.issue({ ...base, isServiceAccount: false }, '5m')) as Record<string, unknown>
    expect(falsy).to.not.have.property('isServiceAccount')
  })

  it('is verifiable with verifyScopedToken for its scope and refused for another', async () => {
    const token = issuer.issue(base, '1m')
    const claims = await auth.verifyScopedToken(token, TokenScopes.CONVERSATION_PERMISSIONS)
    expect(claims).to.include({ userId: 'u1', orgId: 'o1' })
    let failure: unknown
    try {
      await auth.verifyScopedToken(token, TokenScopes.FETCH_CONFIG)
    } catch (e) {
      failure = e
    }
    expect(failure).to.be.instanceOf(Error)
  })

  it('does not accept a user-action scope at compile time', () => {
    // @ts-expect-error user-action scopes are signed with a derived key
    issuer.issue({ ...base, scopes: [TokenScopes.PASSWORD_RESET] }, '1m')
  })
})
