import { expect } from 'chai'
import { collaborationSchemas } from '../../../../src/modules/enterprise_search/validators/collaboration.validators'
import { principalSchema } from '../../../../src/libs/validators/zod-primitives'

const OID = 'a'.repeat(24)
const TEAM = '11111111-1111-4111-8111-111111111111'
const params = { conversationId: OID }
const ok = (schema: { safeParse(v: unknown): { success: boolean } }, value: unknown) => schema.safeParse(value).success

describe('collaboration validators', () => {
  const chat = collaborationSchemas.chat

  it('principalSchema checks the id against the principal type', () => {
    expect(principalSchema.safeParse({ principalType: 'user', principalId: OID }).success).to.equal(true)
    expect(principalSchema.safeParse({ principalType: 'user', principalId: TEAM }).success).to.equal(false)
    expect(principalSchema.safeParse({ principalType: 'team', principalId: TEAM }).success).to.equal(true)
    expect(principalSchema.safeParse({ principalType: 'team', principalId: `all_${OID}` }).success).to.equal(true)
    expect(principalSchema.safeParse({ principalType: 'team', principalId: OID }).success).to.equal(false)
    expect(principalSchema.safeParse({ principalType: 'group', principalId: OID }).success).to.equal(false)
  })

  it('PUT takes 1 to 50 distinct principals, a note up to 500 characters and only a literal true confirmation', () => {
    const row = { principalType: 'user', principalId: OID, accessLevel: 'read' }
    expect(ok(chat.put, { params, body: { collaborators: [row] } })).to.equal(true)
    expect(ok(chat.put, { params, body: { collaborators: [] } })).to.equal(false)
    expect(ok(chat.put, { params, body: { collaborators: [row, row] } })).to.equal(false)
    expect(ok(chat.put, { params, body: { collaborators: [row], note: 'x'.repeat(501) } })).to.equal(false)
    expect(ok(chat.put, { params, body: { collaborators: [row], note: 'x'.repeat(500), confirmOrgWide: true } })).to.equal(true)
    expect(ok(chat.put, { params, body: { collaborators: [row], confirmOrgWide: false } })).to.equal(false)
    expect(ok(chat.put, { params, body: { collaborators: [{ ...row, accessLevel: 'admin' }] } })).to.equal(false)
    const fifty = Array.from({ length: 50 }, (_, i) => ({ ...row, principalId: i.toString(16).padStart(24, '0') }))
    expect(ok(chat.put, { params, body: { collaborators: fifty } })).to.equal(true)
    expect(ok(chat.put, { params, body: { collaborators: [...fifty, row] } })).to.equal(false)
  })

  it('the agent kind also needs the agent key', () => {
    const row = { principalType: 'user', principalId: OID, accessLevel: 'read' }
    expect(ok(collaborationSchemas.agent.put, { params, body: { collaborators: [row] } })).to.equal(false)
    expect(ok(collaborationSchemas.agent.put, { params: { ...params, agentKey: 'a1' }, body: { collaborators: [row] } })).to.equal(true)
  })

  it('settings need at least one boolean, transfer a user id, and the feed a numeric cursor', () => {
    expect(ok(chat.settings, { params, body: {} })).to.equal(false)
    expect(ok(chat.settings, { params, body: { editorsCanInvite: 'yes' } })).to.equal(false)
    expect(ok(chat.settings, { params, body: { ownerContentShared: true } })).to.equal(true)
    expect(ok(chat.transfer, { params, body: { newOwnerUserId: 'nope' } })).to.equal(false)
    expect(ok(chat.transfer, { params, body: { newOwnerUserId: OID } })).to.equal(true)
    expect(chat.feed.parse({ params, query: {} }).query).to.deep.equal({ afterSeq: -1 })
    expect(chat.feed.parse({ params, query: { afterSeq: '12', rev: '3' } }).query).to.deep.equal({ afterSeq: 12, rev: 3 })
    expect(ok(chat.feed, { params, query: { afterSeq: 'x' } })).to.equal(false)
  })

  it('DELETE defaults the principal type to user and refuses an unknown one', () => {
    expect(chat.remove.parse({ params: { ...params, principalId: OID }, query: {} }).query).to.deep.equal({ principalType: 'user' })
    expect(ok(chat.remove, { params: { ...params, principalId: OID }, query: { principalType: 'group' } })).to.equal(false)
  })

  it('every route refuses a malformed conversation id', () => {
    for (const schema of [chat.list, chat.leave, chat.readiness]) expect(ok(schema, { params: { conversationId: 'zzz' } })).to.equal(false)
  })
})
