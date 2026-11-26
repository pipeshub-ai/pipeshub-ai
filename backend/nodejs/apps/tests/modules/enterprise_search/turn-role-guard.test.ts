import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { SessionRole } from '../../../src/modules/agent-harness/domain/session-roles'
import { HarnessSessionRoles } from '../../../src/libs/repositories/agent-harness/session-roles/mongo'
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema'
import { refuseNonOwnerTurns, TurnRefusedError } from '../../../src/modules/enterprise_search/utils/turn-role-guard'
import { inRequestOrg } from '../../helpers/request-org-context'

const ORG = new Types.ObjectId()
const SESSION = new Types.ObjectId()
const OWNER = new Types.ObjectId()
const WRITER = new Types.ObjectId()
const READER = new Types.ObjectId()

const request = (userId: string | undefined, params: Record<string, string> = { conversationId: String(SESSION) }) =>
  ({ user: userId ? { userId, orgId: String(ORG) } : undefined, params, context: { requestId: 'req-1' } }) as never

async function guard(role: SessionRole | null, req: never): Promise<unknown[]> {
  const next = sinon.stub()
  await inRequestOrg(refuseNonOwnerTurns({ roleOf: () => Promise.resolve(role) }))(req, {} as never, next)
  expect(next.callCount).to.equal(1)
  return next.firstCall.args
}

describe('enterprise_search: turn routes refuse participants who may not send turns (SS0, RO-3)', () => {
  afterEach(() => sinon.restore())

  it('a collaborator gets 403 HTTP_COLLABORATOR_TURNS_DISABLED before the handler', async () => {
    const [error] = await guard('collaborator', request(String(WRITER)))
    expect(error).to.be.instanceOf(TurnRefusedError)
    expect(error).to.include({ statusCode: 403, code: 'HTTP_COLLABORATOR_TURNS_DISABLED' })
  })

  // R38AUTHZ-FU-2: the chat route's guard reads the org's collaborator-turns setting, as harness admission does.
  it('with the org letting collaborators send turns, a collaborator passes to the harness; a viewer still does not', async () => {
    const on = { enabled: sinon.stub().resolves(true) }
    const off = { enabled: sinon.stub().resolves(false) }
    const run = async (role: SessionRole, setting: { enabled: sinon.SinonStub }) => {
      const next = sinon.stub()
      await inRequestOrg(refuseNonOwnerTurns({ roleOf: () => Promise.resolve(role) }, setting))(request(String(WRITER)), {} as never, next)
      return next.firstCall.args as unknown[]
    }
    expect(await run('collaborator', on)).to.deep.equal([])
    expect(on.enabled.callCount).to.equal(1)
    expect(await run('viewer', on)).to.have.length(1)
    expect((await run('viewer', on))[0]).to.include({ code: 'HTTP_VIEWER_READ_ONLY' })
    expect((await run('collaborator', off))[0]).to.include({ code: 'HTTP_COLLABORATOR_TURNS_DISABLED' })
    // The owner's turn never waits on the setting.
    on.enabled.resetHistory()
    expect(await run('owner', on)).to.deep.equal([])
    expect(on.enabled.callCount).to.equal(0)
  })

  it('a setting that cannot be read is an error, never a pass', async () => {
    const outage = new Error('kv down')
    const next = sinon.stub()
    await inRequestOrg(
      refuseNonOwnerTurns({ roleOf: () => Promise.resolve('collaborator') }, { enabled: () => Promise.reject(outage) }),
    )(request(String(WRITER)), {} as never, next)
    expect(next.firstCall.args).to.deep.equal([outage])
  })

  it('a viewer gets 403 HTTP_VIEWER_READ_ONLY', async () => {
    const [error] = await guard('viewer', request(String(READER)))
    expect(error).to.include({ statusCode: 403, code: 'HTTP_VIEWER_READ_ONLY' })
  })

  it('the owner and a stranger pass to the handler (which answers a stranger 404)', async () => {
    expect(await guard('owner', request(String(OWNER)))).to.deep.equal([])
    expect(await guard(null, request(String(OWNER)))).to.deep.equal([])
  })

  it('a request without a user or a conversation is left to the routes\' own checks', async () => {
    expect(await guard('viewer', request(undefined))).to.deep.equal([])
    expect(await guard('viewer', request(String(READER), {}))).to.deep.equal([])
  })

  it('a lookup failure is an error, never a pass', async () => {
    const next = sinon.stub()
    const outage = new Error('mongo down')
    await inRequestOrg(refuseNonOwnerTurns({ roleOf: () => Promise.reject(outage) }))(request(String(WRITER)), {} as never, next)
    expect(next.firstCall.args).to.deep.equal([outage])
  })
})

describe('HarnessSessionRoles: roles read from the conversation document', () => {
  afterEach(() => sinon.restore())

  const stubDoc = (doc: Record<string, unknown> | null) => {
    const lean = sinon.stub().resolves(doc)
    const select = sinon.stub().returns({ lean })
    return { findOne: sinon.stub(ChatSession, 'findOne').returns({ select } as never), select }
  }
  const doc = { userId: OWNER, isShared: true, sharedWith: [{ userId: WRITER, accessLevel: 'write' }, { userId: READER, accessLevel: 'read' }] }

  it('maps the owner and each share, scoped to the org and the session kind', async () => {
    const { findOne, select } = stubDoc(doc)
    const roles = new HarnessSessionRoles()
    const scope = (actorId: Types.ObjectId, agentKey?: string) => ({
      orgId: String(ORG), sessionId: String(SESSION), actorId: String(actorId), ...(agentKey ? { agentKey } : {}),
    })
    expect(await roles.roleOf(scope(OWNER))).to.equal('owner')
    expect(await roles.roleOf(scope(WRITER))).to.equal('collaborator')
    expect(await roles.roleOf(scope(READER))).to.equal('viewer')
    expect(await roles.roleOf(scope(new Types.ObjectId(), 'agent-1'))).to.equal(null)
    expect(findOne.firstCall.args[0]).to.deep.include({ _id: String(SESSION), orgId: String(ORG), isDeleted: false, sessionType: 'chat' })
    expect(findOne.lastCall.args[0]).to.deep.include({ sessionType: 'agent', agentKey: 'agent-1' })
    expect(select.firstCall.args[0]).to.equal('userId isShared sharedWith')
  })

  it('no such session, or an invalid id, is no role', async () => {
    const { findOne } = stubDoc(null)
    const roles = new HarnessSessionRoles()
    expect(await roles.roleOf({ orgId: String(ORG), sessionId: String(SESSION), actorId: String(OWNER) })).to.equal(null)
    expect(await roles.roleOf({ orgId: String(ORG), sessionId: 'not-an-id', actorId: String(OWNER) })).to.equal(null)
    expect(findOne.callCount).to.equal(1)
  })
})
