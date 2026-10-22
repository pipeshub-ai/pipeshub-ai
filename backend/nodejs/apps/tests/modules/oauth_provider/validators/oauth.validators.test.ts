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
    ).to.throw(ZodError)
  })

  it('should reject consent values other than granted or denied', () => {
    expect(() =>
      deviceConsentSchema.parse({
        body: { user_code: 'ABCD-EFGH', consent: 'maybe' },
      }),
    ).to.throw(ZodError)
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
