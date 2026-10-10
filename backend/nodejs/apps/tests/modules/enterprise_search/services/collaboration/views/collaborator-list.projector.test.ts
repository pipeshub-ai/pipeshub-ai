import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { CollaboratorListProjector } from '../../../../../../src/modules/enterprise_search/services/collaboration/views/collaborator-list.projector'
import { CollaboratorsView } from '../../../../../../src/modules/enterprise_search/services/collaboration/domain/collaboration-views'
import { CallerIdentity } from '../../../../../../src/libs/types/caller-identity'

const ORG = String(new Types.ObjectId())
const owner = new Types.ObjectId()
const bob = new Types.ObjectId()
const ghost = new Types.ObjectId()
const identity = { userId: String(owner), orgId: ORG, authHeaders: {}, requestKey: {} } as CallerIdentity

function build(opts: { callerTeams?: 'unresolved' | string[]; teamInfo?: Record<string, { status: 'ok'; name: string } | { status: 'missing' } | { status: 'unavailable' }> } = {}) {
  const users = {
    displayNames: sinon.stub().callsFake(async (_o: string, ids: string[]) => new Map(ids.map((id) => [id, id === String(bob) ? 'Bob' : id === String(owner) ? 'Olivia' : '']))),
    findByIds: sinon.stub(),
  }
  const teams = { callerTeamIds: sinon.stub().resolves({ status: 'ok', teamIds: ['sales'] }), teamsVersion: sinon.stub(), exists: sinon.stub() }
  const lookup = { describeMany: sinon.stub().callsFake(async (ids: string[]) => new Map(ids.map((id) => [id, opts.teamInfo?.[id] ?? { status: 'ok' as const, name: id.toUpperCase() }]))) }
  return { projector: new CollaboratorListProjector(users, teams, lookup), users, teams, lookup }
}

const session = (rows: unknown[], settings?: Record<string, boolean>) => ({ userId: owner, sharedWith: rows as never, settings })
const caller = (teamIds: 'unresolved' | string[]) => ({ userId: String(owner), orgId: ORG, teamIds })

describe('CollaboratorListProjector', () => {
  it('the full view batches one name lookup and one team lookup, and shows only member teams by name', async () => {
    const { projector, users, lookup } = build()
    const out = (await projector.project({
      session: session([
        { userId: bob, accessLevel: 'write', addedBy: owner, addedAt: new Date('2026-10-01T00:00:00Z') },
        { userId: ghost, accessLevel: 'read' },
        { teamId: 'sales', accessLevel: 'read' },
        { teamId: 'ops', accessLevel: 'write' },
        { teamId: `all_${ORG}`, accessLevel: 'read' },
      ], { editorsCanInvite: true }),
      caller: caller(['sales']),
      identity,
      role: 'owner',
      full: true,
    })) as CollaboratorsView
    expect(users.displayNames.calledOnce).to.equal(true)
    expect(lookup.describeMany.calledOnce).to.equal(true)
    expect(lookup.describeMany.firstCall.args[0]).to.deep.equal(['sales', 'ops'])
    expect(out.owner).to.deep.equal({ userId: String(owner), displayName: 'Olivia' })
    expect(out.collaboratorCount).to.equal(5)
    expect(out.settings).to.deep.equal({ editorsCanInvite: true, ownerContentShared: false })
    expect(out.collaborators.map((c) => [c.displayName, c.state])).to.deep.equal([
      ['Bob', 'active'],
      ['Former member', 'former_member'],
      ['SALES', 'active'],
      ['A team', 'active'],
      ['Everyone in your organization', 'active'],
    ])
    expect(out.collaborators[0]).to.deep.include({ addedBy: String(owner), addedAt: '2026-10-01T00:00:00.000Z' })
  })

  it('a deleted team is labelled and its name is never shown; an unreachable lookup leaves it active', async () => {
    const { projector } = build({ teamInfo: { gone: { status: 'missing' }, slow: { status: 'unavailable' } } })
    const out = (await projector.project({ session: session([{ teamId: 'gone', accessLevel: 'read' }, { teamId: 'slow', accessLevel: 'read' }]), caller: caller(['gone', 'slow']), identity, role: 'owner', full: true })) as CollaboratorsView
    expect(out.collaborators.map((c) => [c.displayName, c.state])).to.deep.equal([['Deleted team', 'deleted_team'], ['A team', 'active']])
  })

  it('resolves the caller\'s teams itself when the guard did not, and hides every name when it cannot', async () => {
    const resolving = build()
    const named = (await resolving.projector.project({ session: session([{ teamId: 'sales', accessLevel: 'read' }]), caller: caller('unresolved'), identity, role: 'owner', full: true })) as CollaboratorsView
    expect(named.collaborators[0]!.displayName).to.equal('SALES')
    const failing = build()
    failing.teams.callerTeamIds.resolves({ status: 'unresolved' })
    const hidden = (await failing.projector.project({ session: session([{ teamId: 'sales', accessLevel: 'read' }]), caller: caller('unresolved'), identity, role: 'owner', full: true })) as CollaboratorsView
    expect(hidden.collaborators[0]!.displayName).to.equal('A team')
  })

  it('the summary view has only the owner, a count and the caller\'s access, and reads no team data', async () => {
    const { projector, lookup, teams } = build()
    const out = await projector.project({ session: session([{ userId: bob, accessLevel: 'write' }, { teamId: 'ops', accessLevel: 'read' }, {}]), caller: caller([]), identity, role: 'read', full: false })
    expect(out).to.deep.equal({ owner: { userId: String(owner), displayName: 'Olivia' }, collaboratorCount: 2, myAccess: 'read' })
    expect(lookup.describeMany.called).to.equal(false)
    expect(teams.callerTeamIds.called).to.equal(false)
  })

  it('the summary carries respondMode once it is set, so the composer can tell a note from a question', async () => {
    const { projector } = build()
    const withMode = { ...session([{ userId: bob, accessLevel: 'write' }]), settings: { respondMode: 'mention_only' } }
    const out = await projector.project({ session: withMode as never, caller: caller([]), identity, role: 'write', full: false })
    expect(out).to.deep.include({ myAccess: 'write', respondMode: 'mention_only' })
  })

  it('an owner who is no longer in the directory is a former member', async () => {
    const { projector, users } = build()
    users.displayNames.resolves(new Map())
    const out = await projector.project({ session: session([]), caller: caller([]), identity, role: 'write', full: false })
    expect((out as { owner: { displayName: string } }).owner.displayName).to.equal('Former member')
  })
})
