import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { PrincipalResolver } from '../../../../../../src/modules/enterprise_search/services/collaboration/principals/principal-resolver'
import { CallerIdentity } from '../../../../../../src/libs/types/caller-identity'
import { Principal } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/types'

const ORG = 'a'.repeat(24)
const OWNER = '1'.repeat(24)
const BOB = '2'.repeat(24)
const BOT = '3'.repeat(24)
const OFF = '4'.repeat(24)
const identity = { userId: OWNER, orgId: ORG, authHeaders: {}, requestKey: {} } as CallerIdentity
const user = (userId: string): Principal => ({ type: 'user', userId })
const team = (teamId: string): Principal => ({ type: 'team', teamId })

function resolver(over: { teamIds?: string[] | 'unresolved'; existing?: string[] } = {}) {
  const users = {
    displayNames: sinon.stub(),
    findByIds: sinon.stub().callsFake(async (_org: string, ids: string[]) =>
      [
        { userId: BOB, displayName: 'Bob', kind: 'human', isDisabled: false },
        { userId: BOT, displayName: 'Bot', kind: 'service', isDisabled: false },
        { userId: OFF, displayName: 'Off', kind: 'human', isDisabled: true },
        { userId: OWNER, displayName: 'Owner', kind: 'human', isDisabled: false },
      ].filter((u) => ids.includes(u.userId)),
    ),
  }
  const teams = {
    callerTeamIds: sinon.stub().resolves(over.teamIds === 'unresolved' ? { status: 'unresolved' } : { status: 'ok', teamIds: over.teamIds ?? ['t1', 't2'] }),
    teamsVersion: sinon.stub().resolves(0),
    exists: sinon.stub().callsFake(async (id: string) => (over.existing ?? ['t1']).includes(id)),
  }
  return { r: new PrincipalResolver(users, teams), users, teams }
}

describe('PrincipalResolver', () => {
  it('accepts a same-org human and nothing else for users, with a reason for each refusal', async () => {
    const { r } = resolver()
    const out = await r.validate(identity, [user(BOB), user(BOT), user(OFF), user(OWNER), user('9'.repeat(24))], { ownerId: OWNER })
    expect(out.valid).to.deep.equal([user(BOB)])
    expect(out.invalid.map((i) => [i.key, i.reason])).to.deep.equal([
      [`user:${BOT}`, 'service_account'],
      [`user:${OFF}`, 'disabled'],
      [`user:${OWNER}`, 'owner'],
      [`user:${'9'.repeat(24)}`, 'not_found'],
    ])
  })

  it('looks users up in one batch and teams not at all when there are none', async () => {
    const { r, users, teams } = resolver()
    await r.validate(identity, [user(BOB)], { ownerId: OWNER })
    expect(users.findByIds.calledOnce).to.equal(true)
    expect(teams.callerTeamIds.called).to.equal(false)
    expect((await r.validate(identity, [], { ownerId: OWNER })).valid).to.deep.equal([])
  })

  it('a team must be one the caller belongs to and that still exists', async () => {
    const { r } = resolver()
    const out = await r.validate(identity, [team('t1'), team('t2'), team('t3')], { ownerId: OWNER })
    expect(out.valid).to.deep.equal([team('t1')])
    expect(out.invalid.map((i) => [i.key, i.reason])).to.deep.equal([
      ['team:t2', 'not_found'],
      ['team:t3', 'not_a_member'],
    ])
  })

  it('the org-wide team of the caller\'s org needs no lookup, another org\'s is invalid', async () => {
    const { r, teams } = resolver()
    const out = await r.validate(identity, [team(`all_${ORG}`), team(`all_${'b'.repeat(24)}`)], { ownerId: OWNER })
    expect(out.valid).to.deep.equal([team(`all_${ORG}`)])
    expect(out.invalid.map((i) => i.reason)).to.deep.equal(['not_found'])
    expect(teams.callerTeamIds.called).to.equal(false)
  })

  it('unresolvable caller teams are a 503, not a pass or a refusal', async () => {
    const { r } = resolver({ teamIds: 'unresolved' })
    let error: { statusCode?: number; code?: string } | undefined
    try {
      await r.validate(identity, [team('t1')], { ownerId: OWNER })
    } catch (e) {
      error = e as never
    }
    expect(error).to.include({ statusCode: 503, code: 'TEAM_RESOLUTION_UNAVAILABLE' })
  })
})
