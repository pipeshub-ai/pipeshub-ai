import 'reflect-metadata'
import { expect } from 'chai'
import { createServiceTokenSchema } from '../../../../src/modules/oauth_provider/validators/service-token.validators'
import { SERVICE_TOKEN_MAX_EXPIRY_DAYS } from '../../../../src/modules/oauth_provider/services/service-token.service'

const body = (expiryDays?: unknown) => ({
  body: {
    serviceAccountId: '507f1f77bcf86cd799439012',
    name: 'nightly',
    scopes: ['kb:read'],
    ...(expiryDays === undefined ? {} : { expiryDays }),
  },
})

// The token screen lets anyone type a lifetime, so the API must hold the line
// on its own: the screen's check is a convenience, not the limit.
describe('oauth_provider/validators/service-token.validators', () => {
  it('accepts any whole number of days from 1 to the maximum', () => {
    for (const days of [1, 7, 45, SERVICE_TOKEN_MAX_EXPIRY_DAYS]) {
      expect(createServiceTokenSchema.safeParse(body(days)).success, String(days)).to.equal(true)
    }
  })

  it('accepts a request that leaves the expiry out, for the default', () => {
    expect(createServiceTokenSchema.safeParse(body()).success).to.equal(true)
  })

  it('refuses more than the maximum', () => {
    expect(SERVICE_TOKEN_MAX_EXPIRY_DAYS).to.equal(365)
    for (const days of [SERVICE_TOKEN_MAX_EXPIRY_DAYS + 1, 10000, Number.MAX_SAFE_INTEGER]) {
      expect(createServiceTokenSchema.safeParse(body(days)).success, String(days)).to.equal(false)
    }
  })

  it('refuses zero, negative and fractional days', () => {
    for (const days of [0, -1, 2.5, 0.1]) {
      expect(createServiceTokenSchema.safeParse(body(days)).success, String(days)).to.equal(false)
    }
  })

  it('refuses anything that is not a number, including "never"', () => {
    for (const days of ['30', 'never', null, true, Infinity, NaN]) {
      expect(createServiceTokenSchema.safeParse(body(days)).success, String(days)).to.equal(false)
    }
  })
})
