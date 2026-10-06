import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { RecipientResolver } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/recipient-resolver'
import { Principal } from '../../../../../src/modules/enterprise_search/services/collaboration/domain/types'
import { TeamMembersResult } from '../../../../../src/modules/user_management/services/team-directory.service'

const ORG = 'org1'
const identity = { userId: 'A', orgId: ORG, authHeaders: {}, requestKey: {} }
const user = (userId: string): Principal => ({ type: 'user', userId })
const team = (teamId: string): Principal => ({ type: 'team', teamId })
const ok = (...userIds: string[]): TeamMembersResult => ({ status: 'ok', userIds })

type Users = Record<string, { kind?: 'human' | 'service'; isDisabled?: boolean }>

function build(teams: Record<string, () => Promise<TeamMembersResult>>, users?: Users, timeoutMs = 25) {
  const logger = { warn: sinon.stub() }
  const memberUserIds = sinon.stub().callsFake((teamId: string) => (teams[teamId] ?? (() => Promise.resolve({ status: 'unresolved' as const })))())
  const findByIds = sinon.stub().callsFake(async (_org: string, ids: readonly string[]) =>
    ids.flatMap((id) => {
      const u = users ? users[id] : {}
      return u ? [{ userId: id, displayName: id, kind: u.kind ?? 'human', isDisabled: u.isDisabled === true }] : []
    }),
  )
  const resolver = new RecipientResolver({ findByIds, displayNames: sinon.stub() } as never, { memberUserIds } as never, logger as never, timeoutMs)
  return { resolver, memberUserIds, findByIds, logger }
}

describe('RecipientResolver', () => {
  it('NT-09: B direct and in team T with A: B once, the actor absent', async () => {
    const { resolver } = build({ T: async () => ok('B', 'A') })
    const out = await resolver.resolve({ orgId: ORG, identity, principals: [user('B'), team('T')], exclude: new Set(['A']) })
    expect(out).to.deep.equal(['B'])
  })

  it('expands a team as the actor, with the 50-member cap', async () => {
    const { resolver, memberUserIds } = build({ T: async () => ok('B') })
    await resolver.resolve({ orgId: ORG, identity, principals: [team('T')] })
    expect(memberUserIds.calledOnceWithExactly('T', identity, { limit: 50 })).to.equal(true)
  })

  it('never expands the org-wide team and does not notify it', async () => {
    const { resolver, memberUserIds } = build({})
    const out = await resolver.resolve({ orgId: ORG, identity, principals: [team(`all_${ORG}`)] })
    expect(out).to.deep.equal([])
    expect(memberUserIds.called).to.equal(false)
  })

  it('a team that is too large or unresolved is skipped and logged, the others still count', async () => {
    const { resolver, logger } = build({ BIG: async () => ({ status: 'unresolved' }), SMALL: async () => ok('C') })
    const out = await resolver.resolve({ orgId: ORG, identity, principals: [user('B'), team('BIG'), team('SMALL')] })
    expect(out).to.deep.equal(['B', 'C'])
    expect(logger.warn.calledOnce).to.equal(true)
    expect(logger.warn.firstCall.args[1]).to.deep.include({ teamId: 'BIG', reason: 'unresolved' })
  })

  it('a team that throws is skipped, never failing the resolution', async () => {
    const { resolver, logger } = build({ T: () => Promise.reject(new Error('connector down')), U: async () => ok('D') })
    const out = await resolver.resolve({ orgId: ORG, identity, principals: [team('T'), team('U')] })
    expect(out).to.deep.equal(['D'])
    expect(logger.warn.firstCall.args[1]).to.deep.include({ teamId: 'T', error: 'connector down' })
  })

  it('a team that does not answer in time is skipped; teams are expanded in parallel', async () => {
    const hang = () => new Promise<TeamMembersResult>(() => undefined)
    const slow = () => new Promise<TeamMembersResult>((resolve) => setTimeout(() => resolve(ok('E')), 10))
    const { resolver, logger } = build({ HANG: hang, SLOW: slow, SLOW2: slow }, undefined, 40)
    const started = Date.now()
    const out = await resolver.resolve({ orgId: ORG, identity, principals: [team('HANG'), team('SLOW'), team('SLOW2')] })
    expect(out).to.deep.equal(['E'])
    expect(Date.now() - started).to.be.lessThan(120)
    expect(logger.warn.firstCall.args[1]).to.deep.include({ teamId: 'HANG', reason: 'timeout' })
  })

  it('drops disabled users, service accounts and users the directory does not know', async () => {
    const { resolver } = build({}, { B: {}, C: { isDisabled: true }, D: { kind: 'service' } })
    const out = await resolver.resolve({ orgId: ORG, identity, principals: [user('B'), user('C'), user('D'), user('GONE')] })
    expect(out).to.deep.equal(['B'])
  })

  it('without an identity teams are skipped but direct users still resolve', async () => {
    const { resolver, memberUserIds, logger } = build({ T: async () => ok('X') })
    const out = await resolver.resolve({ orgId: ORG, principals: [user('B'), team('T')] })
    expect(out).to.deep.equal(['B'])
    expect(memberUserIds.called).to.equal(false)
    expect(logger.warn.calledOnce).to.equal(true)
  })

  it('looks nobody up when everyone is excluded', async () => {
    const { resolver, findByIds } = build({})
    expect(await resolver.resolve({ orgId: ORG, identity, principals: [user('A')], exclude: new Set(['A']) })).to.deep.equal([])
    expect(findByIds.called).to.equal(false)
  })
})
