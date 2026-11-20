import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { DefaultConversationGuards } from '../../../../../src/modules/enterprise_search/services/conversation/http/default-conversation-guards'
import { conversationContextOf } from '../../../../../src/modules/enterprise_search/services/conversation/http/conversation-context'
import { ChatSession } from '../../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { Project } from '../../../../../src/modules/projects/schema/project.schema'

/**
 * A team member of a project reaches the chats shared to that project, as a direct member does (#4785).
 * A team share row on the chat itself still grants nothing under the default policy.
 */

const ORG = new Types.ObjectId()
const OWNER = new Types.ObjectId()
const MEMBER = new Types.ObjectId()
const PROJECT = new Types.ObjectId()
const TEAM = 'team-graph-id-1'

const leanOf = <T>(value: T) => ({ select: () => ({ lean: () => Promise.resolve(value) }) })

function guards(teamIds: string[]) {
  const accessibleProjectIds = sinon.stub().resolves([String(PROJECT)])
  const teamIdsOf = sinon.stub().resolves(teamIds)
  const g = new DefaultConversationGuards({
    projects: {
      accessibleProjectIds,
      roleOf: sinon.stub(),
      assertAtLeast: sinon.stub(),
      orgHasTeamMembers: sinon.stub().resolves(true),
    } as never,
    teamIdsOf,
  })
  return { g, accessibleProjectIds, teamIdsOf }
}

const request = (params: Record<string, string> = {}): Record<string, unknown> => ({
  params,
  query: {},
  headers: {},
  user: { userId: String(MEMBER), orgId: String(ORG) },
})

async function run(handler: (req: never, res: never, next: never) => unknown, req: Record<string, unknown>): Promise<unknown> {
  let error: unknown
  await handler(req as never, {} as never, ((err?: unknown) => {
    error = err
  }) as never)
  return error
}

describe('default guards: project access through a team', () => {
  afterEach(() => sinon.restore())

  it('a list asks for the projects the caller reaches through their teams', async () => {
    const { g, accessibleProjectIds, teamIdsOf } = guards([TEAM])
    const req = request()
    expect(await run(g.listScope('chat') as never, req)).to.equal(undefined)
    expect(teamIdsOf.calledOnce).to.equal(true)
    expect(accessibleProjectIds.firstCall.args[0]).to.deep.include({ teamIds: [TEAM] })
    expect(conversationContextOf(req as never).accessibleProjectIds).to.deep.equal([String(PROJECT)])
  })

  const projectChat = {
    _id: new Types.ObjectId(),
    orgId: ORG,
    userId: OWNER,
    isDeleted: false,
    sharedWith: [],
    projectId: PROJECT,
    projectVisibility: 'project',
  }
  const teamProject = {
    _id: PROJECT,
    orgId: ORG,
    userId: OWNER,
    visibility: 'private',
    members: [{ principalType: 'team', principalId: TEAM, role: 'viewer' }],
  }

  it('a team member of the project opens a chat shared to it', async () => {
    sinon.stub(ChatSession, 'findOne').returns(leanOf(projectChat) as never)
    sinon.stub(Project, 'findOne').returns({ lean: () => Promise.resolve(teamProject) } as never)
    const { g } = guards([TEAM])
    const req = request({ conversationId: String(projectChat._id) })
    expect(await run(g.authorize('read', 'chat') as never, req)).to.equal(undefined)
    expect(conversationContextOf(req as never).grant?.via.map((p) => p.type)).to.deep.equal(['project'])
  })

  it('without the team, the same caller gets a 404', async () => {
    sinon.stub(ChatSession, 'findOne').returns(leanOf(projectChat) as never)
    sinon.stub(Project, 'findOne').returns({ lean: () => Promise.resolve(teamProject) } as never)
    const { g } = guards([])
    const error = await run(g.authorize('read', 'chat') as never, request({ conversationId: String(projectChat._id) }))
    expect((error as { statusCode?: number }).statusCode).to.equal(404)
  })

  it('does not look up teams for a chat that is not shared to a project', async () => {
    sinon.stub(ChatSession, 'findOne').returns(leanOf({ ...projectChat, projectId: undefined, projectVisibility: undefined, userId: MEMBER }) as never)
    const { g, teamIdsOf } = guards([TEAM])
    expect(await run(g.authorize('read', 'chat') as never, request({ conversationId: String(projectChat._id) }))).to.equal(undefined)
    expect(teamIdsOf.called).to.equal(false)
  })
})
