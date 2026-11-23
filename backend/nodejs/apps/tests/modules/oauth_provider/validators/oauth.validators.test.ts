import 'reflect-metadata'
import { expect } from 'chai'
import { ZodError } from 'zod'
import {
  authorizeConsentSchema,
  createAppSchema,
  authorizeQuerySchema,
  deviceAuthorizationSchema,
  deviceConsentSchema,
  deviceUserCodeSchema,
  knowledgeLimitSchema,
  MAX_KNOWLEDGE_LIMIT_IDS,
  introspectSchema,
  revokeSchema,
  tokenSchema,
  updateAppSchema,
} from '../../../../src/modules/oauth_provider/validators/oauth.validators'
import { OAuthGrantType } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'

const validChallenge = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'

const baseQuery = {
  response_type: 'code',
  client_id: 'cid',
  redirect_uri: 'https://example.com/cb',
  scope: 'org:read',
  state: 'st',
}

const baseConsent = {
  client_id: 'cid',
  redirect_uri: 'https://example.com/cb',
  scope: 'org:read',
  state: 'st',
  consent: 'granted',
}

describe('oauth_provider/validators/oauth.validators', () => {
  it('should accept a device authorization body', () => {
    const parsed = deviceAuthorizationSchema.parse({
      body: { client_id: 'pipeshub-agent', scope: 'user:read' },
    })
    expect(parsed.body.client_id).to.equal('pipeshub-agent')
  })

  it('should reject an empty user_code', () => {
    expect(() =>
      deviceUserCodeSchema.parse({ body: { user_code: '' } }),
    ).to.throw().that.satisfies((e: unknown) => e instanceof ZodError)
  })

  it('should reject consent values other than granted or denied', () => {
    expect(() =>
      deviceConsentSchema.parse({
        body: { user_code: 'ABCD-EFGH', consent: 'maybe' },
      }),
    ).to.throw().that.satisfies((e: unknown) => e instanceof ZodError)
  })

  it('should accept granted and denied consent', () => {
    expect(
      deviceConsentSchema.parse({
        body: { user_code: 'ABCD-EFGH', consent: 'granted' },
      }).body.consent,
    ).to.equal('granted')
    expect(
      deviceConsentSchema.parse({
        body: { user_code: 'ABCD-EFGH', consent: 'denied' },
      }).body.consent,
    ).to.equal('denied')
  })

  it('should accept the device_code grant on the token schema', () => {
    const parsed = tokenSchema.parse({
      body: {
        grant_type: OAuthGrantType.DEVICE_CODE,
        client_id: 'cid',
        device_code: 'abc',
      },
    })
    expect(parsed.body.device_code).to.equal('abc')
  })

  // A NUL cannot be stored by PostgreSQL: refused as the client's 400 here, never a store failure of ours later.
  describe('control characters in credentials', () => {
    const cases: Array<[string, () => unknown]> = [
      ['token client_id', () => tokenSchema.safeParse({ body: { grant_type: OAuthGrantType.CLIENT_CREDENTIALS, client_id: 'a\u0000b' } })],
      ['token client_secret', () => tokenSchema.safeParse({ body: { grant_type: OAuthGrantType.CLIENT_CREDENTIALS, client_id: 'cid', client_secret: 's\u0007' } })],
      ['token refresh_token', () => tokenSchema.safeParse({ body: { grant_type: OAuthGrantType.REFRESH_TOKEN, client_id: 'cid', refresh_token: 'r\u001f' } })],
      ['token code', () => tokenSchema.safeParse({ body: { grant_type: OAuthGrantType.AUTHORIZATION_CODE, client_id: 'cid', code: 'c\u0000' } })],
      ['token device_code', () => tokenSchema.safeParse({ body: { grant_type: OAuthGrantType.DEVICE_CODE, client_id: 'cid', device_code: 'd\u007f' } })],
      ['revoke token', () => revokeSchema.safeParse({ body: { token: 't\u0000', client_id: 'cid' } })],
      ['revoke client_id', () => revokeSchema.safeParse({ body: { token: 't', client_id: 'c\nid' } })],
      ['introspect token', () => introspectSchema.safeParse({ body: { token: 't\u0000', client_id: 'cid' } })],
      ['introspect client_secret', () => introspectSchema.safeParse({ body: { token: 't', client_id: 'cid', client_secret: '\u0000' } })],
    ]
    for (const [name, parse] of cases) {
      it(`refuses a control character in the ${name}`, () => {
        expect((parse() as { success: boolean }).success).to.equal(false)
      })
    }

    it('accepts ordinary credentials, punctuation and non-ASCII included', () => {
      expect(tokenSchema.safeParse({
        body: { grant_type: OAuthGrantType.REFRESH_TOKEN, client_id: 'pha_c-1.x', client_secret: 's3cr3t!~ é', refresh_token: 'eyJ.a-b_c' },
      }).success).to.equal(true)
      expect(revokeSchema.safeParse({ body: { token: 'phpat_abc.def', client_id: 'cid' } }).success).to.equal(true)
      expect(introspectSchema.safeParse({ body: { token: 'eyJ.x.y', client_id: 'cid', client_secret: 'x' } }).success).to.equal(true)
    })
  })

  // GHSA-cxgc-52jq-fcx9: only S256 is accepted on both the GET and the POST.
  describe('code_challenge_method (PKCE)', () => {
    it('authorizeQuerySchema accepts S256', () => {
      const result = authorizeQuerySchema.safeParse({
        query: { ...baseQuery, code_challenge: validChallenge, code_challenge_method: 'S256' },
      })
      expect(result.success).to.be.true
    })

    it('authorizeQuerySchema rejects plain', () => {
      const result = authorizeQuerySchema.safeParse({
        query: { ...baseQuery, code_challenge: validChallenge, code_challenge_method: 'plain' },
      })
      expect(result.success).to.be.false
    })

    it('authorizeQuerySchema still accepts an omitted method', () => {
      const result = authorizeQuerySchema.safeParse({
        query: { ...baseQuery, code_challenge: validChallenge },
      })
      expect(result.success).to.be.true
    })

    it('authorizeConsentSchema accepts S256', () => {
      const result = authorizeConsentSchema.safeParse({
        body: { ...baseConsent, code_challenge: validChallenge, code_challenge_method: 'S256' },
      })
      expect(result.success).to.be.true
    })

    it('authorizeConsentSchema rejects plain', () => {
      const result = authorizeConsentSchema.safeParse({
        body: { ...baseConsent, code_challenge: validChallenge, code_challenge_method: 'plain' },
      })
      expect(result.success).to.be.false
    })

    it('authorizeConsentSchema accepts the resource, one or several', () => {
      for (const resource of ['https://pipeshub.example.com/mcp', ['https://pipeshub.example.com/mcp']]) {
        const result = authorizeConsentSchema.safeParse({ body: { ...baseConsent, resource } })
        expect(result.success).to.be.true
        expect((result as any).data.body.resource).to.deep.equal(resource)
      }
    })

    it('authorizeConsentSchema rejects a malformed code_challenge', () => {
      const result = authorizeConsentSchema.safeParse({
        body: { ...baseConsent, code_challenge: 'too-short', code_challenge_method: 'S256' },
      })
      expect(result.success).to.be.false
    })
  })
})

describe('knowledgeLimitSchema', () => {
  const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`

  it('fills missing lists and drops repeated ids', () => {
    expect(knowledgeLimitSchema.parse({ apps: [id(1), id(1)] })).to.deep.equal({
      apps: [id(1)], recordGroups: [], records: [],
    })
  })

  it('lowercases ids, since an upper-case copy would match no graph id', () => {
    expect(knowledgeLimitSchema.parse({ apps: [id(1).toUpperCase()] }).apps).to.deep.equal([id(1)])
  })

  it('refuses a limit that names nothing, which would hide everything', () => {
    expect(knowledgeLimitSchema.safeParse({ apps: [], records: [] }).success).to.be.false
  })

  it(`refuses more than ${MAX_KNOWLEDGE_LIMIT_IDS} ids, since the limit rides in every token`, () => {
    const records = Array.from({ length: MAX_KNOWLEDGE_LIMIT_IDS + 1 }, (_, n) => id(n))
    expect(knowledgeLimitSchema.safeParse({ records }).success).to.be.false
  })

  it('refuses ids that are not UUIDs and keys it does not know', () => {
    expect(knowledgeLimitSchema.safeParse({ apps: ['not-an-id'] }).success).to.be.false
    expect(knowledgeLimitSchema.safeParse({ apps: [id(1)], recordsExact: [id(2)] }).success).to.be.false
  })

  it('rides on the app create and update bodies, and null lifts it on update', () => {
    const limit = { apps: [id(1)] }
    const app = { name: 'n', allowedScopes: ['kb:read'], redirectUris: ['https://example.com/cb'] }
    expect(createAppSchema.parse({ body: { ...app, knowledgeLimit: limit } }).body.knowledgeLimit)
      .to.deep.equal({ apps: [id(1)], recordGroups: [], records: [] })
    const params = { appId: '507f1f77bcf86cd799439011' }
    expect(updateAppSchema.parse({ params, body: { knowledgeLimit: null } }).body.knowledgeLimit).to.equal(null)
    expect(updateAppSchema.safeParse({ params, body: { knowledgeLimit: { apps: [] } } }).success).to.be.false
  })
})
