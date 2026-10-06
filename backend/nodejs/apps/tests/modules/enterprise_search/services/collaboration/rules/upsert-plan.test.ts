import { expect } from 'chai'
import { Types } from 'mongoose'
import { assertOrgWideAllowed, planUpsert } from '../../../../../../src/modules/enterprise_search/services/collaboration/rules/upsert-plan'

const b = new Types.ObjectId()
const c = new Types.ObjectId()
const user = (id: Types.ObjectId, accessLevel: 'read' | 'write') => ({ principal: { type: 'user' as const, userId: String(id) }, accessLevel })

describe('planUpsert', () => {
  it('adds absent principals, changes differing levels and skips equal ones', () => {
    const ops = planUpsert({
      requested: [user(b, 'write'), user(c, 'read')],
      existing: [{ userId: b, accessLevel: 'read' }, { teamId: 't1', accessLevel: 'write' }],
      isOwner: true,
    })
    expect(ops).to.deep.equal([
      { kind: 'changeLevel', principal: { type: 'user', userId: String(b) }, from: 'read', to: 'write' },
      { kind: 'add', principal: { type: 'user', userId: String(c) }, accessLevel: 'read' },
    ])
    expect(planUpsert({ requested: [user(b, 'read')], existing: [{ userId: b, accessLevel: 'read' }], isOwner: false })).to.deep.equal([])
  })

  it('an editor cannot change an existing level', () => {
    expect(() => planUpsert({ requested: [user(b, 'read')], existing: [{ userId: b, accessLevel: 'write' }], isOwner: false })).to.throw().with.property('code', 'CONVERSATION_OWNER_ONLY')
  })

  it('refuses to go past 200 rows before writing anything', () => {
    const rows = Array.from({ length: 199 }, () => ({ userId: new Types.ObjectId(), accessLevel: 'read' }))
    expect(planUpsert({ requested: [user(b, 'read')], existing: rows, isOwner: true })).to.have.length(1)
    expect(() => planUpsert({ requested: [user(b, 'read'), user(c, 'read')], existing: rows, isOwner: true })).to.throw().with.property('code', 'COLLABORATOR_LIMIT')
  })

  it('ignores stored rows with neither id', () => {
    expect(planUpsert({ requested: [user(b, 'read')], existing: [{ accessLevel: 'read' }], isOwner: true })).to.have.length(1)
  })
})

describe('assertOrgWideAllowed', () => {
  const ORG = 'a'.repeat(24)
  const all = (accessLevel: 'read' | 'write') => ({ principal: { type: 'team' as const, teamId: `all_${ORG}` }, accessLevel })
  it('passes requests that do not target everyone', () => {
    expect(() => assertOrgWideAllowed([user(b, 'write')], { orgId: ORG, confirmOrgWide: false, writeAllowed: false })).to.not.throw()
  })
  it('needs the confirmation, then the platform switch for write', () => {
    expect(() => assertOrgWideAllowed([all('read')], { orgId: ORG, confirmOrgWide: false, writeAllowed: true })).to.throw().with.property('code', 'ORG_WIDE_CONFIRMATION_REQUIRED')
    expect(() => assertOrgWideAllowed([all('read')], { orgId: ORG, confirmOrgWide: true, writeAllowed: false })).to.not.throw()
    expect(() => assertOrgWideAllowed([all('write')], { orgId: ORG, confirmOrgWide: true, writeAllowed: false })).to.throw().with.property('code', 'INVALID_PRINCIPAL')
    expect(() => assertOrgWideAllowed([all('write')], { orgId: ORG, confirmOrgWide: true, writeAllowed: true })).to.not.throw()
  })
})
