import 'reflect-metadata'
import { expect } from 'chai'
import { ZodError } from 'zod'
import {
  authorizeConsentSchema,
  authorizeQuerySchema,
  deviceAuthorizationSchema,
  deviceConsentSchema,
  deviceUserCodeSchema,
  tokenSchema,
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
