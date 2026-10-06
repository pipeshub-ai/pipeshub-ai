import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { ConversationReadinessService } from '../../../../../../src/modules/enterprise_search/services/collaboration/readiness/conversation-readiness.service'
import { ConversationAccessGrant } from '../../../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'
import { OwnerActivity } from '../../../../../../src/modules/enterprise_search/services/collaboration/http/owner-activity'
import { AgentReadinessResult } from '../../../../../../src/modules/enterprise_search/services/collaboration/readiness/agent-readiness.port'
import { CallerIdentity } from '../../../../../../src/libs/types/caller-identity'

const ORG = String(new Types.ObjectId())
const OWNER = String(new Types.ObjectId())
const ME = String(new Types.ObjectId())
const identity = { userId: ME, orgId: ORG, authHeaders: {}, requestKey: {} } as CallerIdentity

function grant(over: { canSend?: boolean; ownerId?: string; agentKey?: string; projectId?: string; teamIds?: 'unresolved' | string[] } = {}): ConversationAccessGrant {
  return {
    session: { _id: new Types.ObjectId(), userId: new Types.ObjectId(over.ownerId ?? OWNER), orgId: new Types.ObjectId(ORG), agentKey: over.agentKey, projectId: over.projectId ? new Types.ObjectId(over.projectId) : undefined },
    role: 'write',
    via: [],
    caller: { userId: ME, orgId: ORG, teamIds: over.teamIds ?? [] },
    view: { canSend: over.canSend ?? true },
  } as unknown as ConversationAccessGrant
}

function build(opts: { checks?: AgentReadinessResult[]; ownerActive?: boolean | Error; project?: boolean; teams?: 'unresolved' | string[] } = {}) {
  const checks = [...(opts.checks ?? [{ status: 'ready' } as AgentReadinessResult])]
  const agents = { check: sinon.stub().callsFake(async () => (checks.length > 1 ? checks.shift()! : checks[0]!)), invalidate: sinon.stub() }
  const projects = { roleOf: sinon.stub().resolves(opts.project === false ? null : { role: 'viewer' }), assertAtLeast: sinon.stub(), accessibleProjectIds: sinon.stub() }
  const teams = { callerTeamIds: sinon.stub().resolves(opts.teams === 'unresolved' ? { status: 'unresolved' } : { status: 'ok', teamIds: opts.teams ?? ['t1'] }), teamsVersion: sinon.stub(), exists: sinon.stub() }
  const users = {
    displayNames: sinon.stub(),
    findByIds: sinon.stub().callsFake(async () => {
      if (opts.ownerActive instanceof Error) throw opts.ownerActive
      return [{ userId: OWNER, displayName: 'o', kind: 'human', isDisabled: opts.ownerActive === false }]
    }),
  }
  const service = new ConversationReadinessService(agents, projects, teams, new OwnerActivity(users, { warn: sinon.stub() }), { warn: sinon.stub() })
  return { service, agents, projects, teams }
}

describe('ConversationReadinessService', () => {
  it('a caller who cannot write is told so and nothing else is asked', async () => {
    const { service, agents, projects } = build()
    expect(await service.evaluate(grant({ canSend: false, agentKey: 'a' }), identity)).to.deep.equal({ canSend: false, reasons: ['CONVERSATION_READ_ONLY'] })
    expect(agents.check.called).to.equal(false)
    expect(projects.roleOf.called).to.equal(false)
  })

  it('a ready chat can be sent to', async () => {
    const { service } = build()
    expect(await service.evaluate(grant(), identity)).to.deep.equal({ canSend: true, reasons: [] })
  })

  it('collects every reason: inactive owner, missing project access, missing toolsets', async () => {
    const { service } = build({ ownerActive: false, project: false, checks: [{ status: 'blocked', toolsets: ['jira', 'gmail'] }] })
    const out = await service.evaluate(grant({ agentKey: 'a', projectId: String(new Types.ObjectId()) }), identity)
    expect(out).to.deep.equal({ canSend: false, reasons: ['OWNER_INACTIVE', 'PROJECT_ACCESS_REQUIRED', 'CONNECTOR_SETUP_REQUIRED'], missingToolsets: ['jira', 'gmail'] })
  })

  it('an agent that is gone is AGENT_UNAVAILABLE', async () => {
    const { service } = build({ checks: [{ status: 'unavailable' }] })
    expect(await service.evaluate(grant({ agentKey: 'a' }), identity)).to.deep.equal({ canSend: false, reasons: ['AGENT_UNAVAILABLE'] })
  })

  it('an unknown answer from the agent service does not block, and a plain chat never asks it', async () => {
    const unknown = build({ checks: [{ status: 'unknown' }] })
    expect((await unknown.service.evaluate(grant({ agentKey: 'a' }), identity)).canSend).to.equal(true)
    const plain = build()
    await plain.service.evaluate(grant(), identity)
    expect(plain.agents.check.called).to.equal(false)
  })

  it('a cached blocked answer is dropped and asked again; Python\'s newer answer wins (PH-05 carry-over)', async () => {
    const { service, agents } = build({ checks: [{ status: 'blocked', toolsets: ['jira'] }, { status: 'ready' }] })
    const out = await service.evaluate(grant({ agentKey: 'a' }), identity)
    expect(out).to.deep.equal({ canSend: true, reasons: [] })
    expect(agents.invalidate.calledOnceWithExactly({ orgId: ORG, userId: ME }, 'a')).to.equal(true)
    expect(agents.check.callCount).to.equal(2)
  })

  it('a ready answer is not re-asked or invalidated', async () => {
    const { service, agents } = build()
    await service.evaluate(grant({ agentKey: 'a' }), identity)
    expect(agents.check.callCount).to.equal(1)
    expect(agents.invalidate.called).to.equal(false)
  })

  it('the owner is never checked for being inactive; an owner-status outage is skipped, not shown', async () => {
    const self = build({ ownerActive: false })
    expect((await self.service.evaluate(grant({ ownerId: ME }), identity)).canSend).to.equal(true)
    const down = build({ ownerActive: new Error('directory down') })
    expect((await down.service.evaluate(grant(), identity)).canSend).to.equal(true)
  })

  it('project access resolves the caller\'s teams when the guard did not, and gives up quietly when it cannot', async () => {
    const project = String(new Types.ObjectId())
    const resolving = build({ project: false, teams: ['t9'] })
    const out = await resolving.service.evaluate(grant({ projectId: project, teamIds: 'unresolved' }), identity)
    expect(out.reasons).to.deep.equal(['PROJECT_ACCESS_REQUIRED'])
    expect(resolving.projects.roleOf.firstCall.args[0]).to.deep.include({ teamIds: ['t9'] })
    const unresolved = build({ project: false, teams: 'unresolved' })
    expect((await unresolved.service.evaluate(grant({ projectId: project, teamIds: 'unresolved' }), identity)).canSend).to.equal(true)
  })
})
