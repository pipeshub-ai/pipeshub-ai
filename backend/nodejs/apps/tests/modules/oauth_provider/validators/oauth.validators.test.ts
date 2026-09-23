import 'reflect-metadata'
import { expect } from 'chai'
import { ZodError } from 'zod'
import {
  deviceAuthorizationSchema,
  deviceConsentSchema,
  deviceUserCodeSchema,
  tokenSchema,
} from '../../../../src/modules/oauth_provider/validators/oauth.validators'
import { OAuthGrantType } from '../../../../src/modules/oauth_provider/schema/oauth.app.schema'

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
})
