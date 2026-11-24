import 'reflect-metadata'
import { expect } from 'chai'
import {
  createPatTokenSchema,
  updatePatKnowledgeLimitSchema,
} from '../../../../src/modules/oauth_provider/validators/pat.validators'

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

  describe('updatePatKnowledgeLimitSchema', () => {
    const tokenId = '65a1b2c3d4e5f60718293a4b'
    const source = '3f1c2a4e-9b7d-4c1a-8e2f-5a6b7c8d9e0f'
    const req = (body: unknown, id = tokenId) => ({ params: { tokenId: id }, body })

    it('takes a limit, filling the lists it leaves out and dropping repeats', () => {
      const r = updatePatKnowledgeLimitSchema.safeParse(req({ knowledgeLimit: { apps: [source, source] } }))
      expect(r.success).to.equal(true)
      expect((r as any).data.body.knowledgeLimit).to.deep.equal({ apps: [source], recordGroups: [], records: [] })
    })

    it('takes null for everything the owner can see', () => {
      const r = updatePatKnowledgeLimitSchema.safeParse(req({ knowledgeLimit: null }))
      expect(r.success).to.equal(true)
      expect((r as any).data.body.knowledgeLimit).to.equal(null)
    })

    it('refuses a body that says nothing about the limit, so a slip never widens a token', () => {
      expect(updatePatKnowledgeLimitSchema.safeParse(req({})).success).to.equal(false)
    })

    it('refuses an empty pick, a non-id and a bad token id', () => {
      expect(updatePatKnowledgeLimitSchema.safeParse(req({ knowledgeLimit: { apps: [] } })).success).to.equal(false)
      expect(updatePatKnowledgeLimitSchema.safeParse(req({ knowledgeLimit: { apps: ['x'] } })).success).to.equal(false)
      expect(updatePatKnowledgeLimitSchema.safeParse(req({ knowledgeLimit: null }, 'nope')).success).to.equal(false)
    })
  })
})
