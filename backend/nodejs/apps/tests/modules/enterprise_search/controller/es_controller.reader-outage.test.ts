import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { getConversationById } from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { resetVisibility } from '../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { accessCheckBreaker } from '../../../../src/modules/enterprise_search/utils/access-check-breaker'
import { viewSyncBreaker } from '../../../../src/modules/enterprise_search/services/conversation/sharing/chat-content-access'
import { behindGuard, realGuards } from '../helpers/guarded-chat'

/**
 * A reader's read of a shared chat while the query service cannot answer: every answer that needs the access check is
 * hidden as unconfirmed, and once the checks keep failing the read stops asking, so many readers do not pile up requests
 * on a service that is down. Only the store and the AI service command are stubbed.
 */

const ORG = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const SESSION = 'dddddddddddddddddddddddd'
const OWNER = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const READER = 'b1b1b1b1b1b1b1b1b1b1b1b1'

const appConfig: any = {
  aiBackend: 'http://localhost:8000',
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
}

const chain = (value: unknown): any => {
  const q: any = {}
  for (const m of ['select', 'sort', 'skip', 'limit', 'populate', 'lean', 'session']) q[m] = sinon.stub().returns(q)
  q.exec = sinon.stub().resolves(value)
  q.then = (ok: (v: unknown) => unknown, bad?: (e: unknown) => unknown) => Promise.resolve(value).then(ok, bad)
  return q
}

let n = 0
const nextId = (): string => (++n).toString(16).padStart(24, '0')

/** Two turns, each answer citing a record, as the owner asked them. */
const rows = (): any[] =>
  [
    { messageType: 'user_query', content: 'What does the handbook say?', authorUserId: OWNER },
    { messageType: 'bot_response', content: 'Tuesdays [1].', requestedBy: OWNER, citations: [{ citationId: { _id: nextId(), content: 'Tuesdays', chunkIndex: 1, metadata: { recordId: 'rec-a', recordName: 'a.pdf', orgId: ORG } } }] },
    { messageType: 'user_query', content: 'And the other one?', authorUserId: OWNER },
    { messageType: 'bot_response', content: 'Fridays [1].', requestedBy: OWNER, citations: [{ citationId: { _id: nextId(), content: 'Fridays', chunkIndex: 1, metadata: { recordId: 'rec-b', recordName: 'b.pdf', orgId: ORG } } }] },
  ].map((row, i) => ({ _id: nextId(), sessionId: SESSION, orgId: ORG, seq: i + 1, createdAt: new Date(Date.UTC(2026, 10, 27, 12, i)), ...row }))

/** The chat in the store, shared read-only with the reader: every read of its rows answers by ids and place. */
const stubChat = (stored: any[]): void => {
  sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
  sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false)
  sinon.stub(Users, 'find').returns(chain([]))
  sinon.stub(ChatSession, 'findOne').returns(
    chain({ _id: SESSION, orgId: ORG, userId: OWNER, initiator: OWNER, title: 't', isShared: true, sharedWith: [{ userId: READER, accessLevel: 'read' }], status: 'complete' }),
  )
  sinon.stub(ChatSession, 'find').returns(chain([]))
  sinon.stub(ChatSessionMessage, 'countDocuments').resolves(stored.length)
  sinon.stub(ChatSessionMessage, 'find').callsFake((filter: any = {}) => {
    const ids: string[] | undefined = filter?._id?.$in?.map(String)
    const seq = filter?.seq ?? {}
    const kept = stored.filter((m) => (ids === undefined || ids.includes(String(m._id))) && (seq.$lte === undefined || m.seq <= seq.$lte))
    return chain(kept.map((m) => ({ ...m })))
  })
}

const getChat = () => behindGuard(realGuards(), 'read', getConversationById(appConfig) as never, 'chat') as any

const readAs = async (userId: string): Promise<any[]> => {
  const r: any = { status: sinon.stub(), json: sinon.stub() }
  r.status.returns(r)
  const next = sinon.stub()
  await getChat()(
    { headers: { authorization: 'Bearer t' }, body: {}, params: { conversationId: SESSION }, query: {}, user: { userId, orgId: ORG }, context: { requestId: 'r' }, on: sinon.stub() },
    r,
    next,
  )
  sinon.assert.notCalled(next)
  return r.json.firstCall.args[0].conversation.messages
}

describe("a reader's read while the access service cannot answer", () => {
  const reset = () => {
    resetVisibility()
    accessCheckBreaker.reset()
    viewSyncBreaker.reset()
  }
  beforeEach(reset)
  afterEach(() => {
    sinon.restore()
    reset()
  })

  it('hides what needs the check, and once the checks keep failing asks the service nothing', async () => {
    const asked: string[] = []
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
      asked.push(String(this.uri))
      throw Object.assign(new Error('The operation was aborted due to timeout'), { name: 'TimeoutError' })
    })
    stubChat(rows())

    const perRead: number[] = []
    for (let i = 0; i < 6; i += 1) {
      const before = asked.length
      const answers = (await readAs(READER)).filter((m) => m.messageType === 'bot_response')
      perRead.push(asked.length - before)
      expect(answers, `read ${i + 1}`).to.have.length(2)
      for (const a of answers) expect(a.answerHidden, `read ${i + 1}`).to.deep.equal({ redacted: true, reason: 'access_check_unavailable' })
    }
    // Each read asked the reader check and the file permission sync, until both kept failing.
    expect(perRead.slice(0, 5)).to.deep.equal([2, 2, 2, 2, 2])
    expect(perRead[5], 'the sixth read asked nothing').to.equal(0)
    expect(new Set(asked)).to.deep.equal(new Set(['http://localhost:8000/api/v1/chat/records/access', 'http://localhost:8000/api/v1/chat/artifacts/permissions']))
  })
})
