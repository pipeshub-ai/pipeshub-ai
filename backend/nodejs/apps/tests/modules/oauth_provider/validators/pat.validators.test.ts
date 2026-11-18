import 'reflect-metadata'
import { expect } from 'chai'
import { createPatTokenSchema } from '../../../../src/modules/oauth_provider/validators/pat.validators'

const body = (expiryDays?: unknown) => ({
  body: { name: 'ci', ...(expiryDays === undefined ? {} : { expiryDays }) },
})

describe('oauth_provider/validators/pat.validators', () => {
  it('accepts 7, 30, 90 and 365 days, and an omitted expiry', () => {
    for (const days of [7, 30, 90, 365, undefined]) {
      expect(createPatTokenSchema.safeParse(body(days)).success, String(days)).to.equal(true)
    }
  })

  it('refuses never and other values', () => {
    for (const days of ['never', 14, 1, 0, null, '30']) {
      expect(createPatTokenSchema.safeParse(body(days)).success, String(days)).to.equal(false)
    }
  })

  it('names the allowed values in the error message', async () => {
    const r = createPatTokenSchema.safeParse(body('never'))
    expect(r.success).to.equal(false)
    expect(JSON.stringify((r as any).error.issues)).to.contain('Expiry days must be 7, 30, 90 or 365.')
  })
})
