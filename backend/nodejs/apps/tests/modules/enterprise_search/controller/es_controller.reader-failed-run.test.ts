import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { getConversationById } from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { buildAIFailureResponseMessage } from '../../../../src/modules/enterprise_search/utils/utils'
import { FAILED_FOR_OTHERS, resetVisibility } from '../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { FAILED_FOR_OTHERS as STREAMED_FAILED_FOR_OTHERS } from '../../../../src/modules/agent-harness/projection/audiences'
import { behindGuard, realGuards } from '../helpers/guarded-chat'

/**
 * A chat whose owner's last run failed, read by each of its people in the default build: its readers learn only that it
 * failed (`FAILED_FOR_OTHERS`, `reasonWithheld`), never why, on the row or on the chat (`failReason`,
 * `conversationErrors`, who it failed for); its asker keeps all of it. As the live test read it, on the classic path and
 * on the durable one. The access route and the store are stubbed; the reads run for real.
 */

const ORG = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const SESSION = 'dddddddddddddddddddddddd'
const OWNER = 'aaaaaaaaaaaaaaaaaaaaaaaa'
const READER = 'b1b1b1b1b1b1b1b1b1b1b1b1'
/** Why the run failed, as the asker is told: it may name an account, a budget or a limit. */
const REASON = 'CANARY-REASON: the model account for this team is over its budget.'

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

/** The owner's question, then their run's failure as the classic path or the harness stores it. */
const failedChat = (durable: boolean): any[] => {
  const failure = buildAIFailureResponseMessage(
    REASON,
    [{ type: 'text', content: REASON } as never],
    durable ? { runId: 'run-failed-1', errorCode: 'MODEL_BUDGET_EXCEEDED', runOutcome: { kind: 'failed', reason: REASON } as never } : undefined,
    durable ? {} : { errorCode: 'MODEL_BUDGET_EXCEEDED' },
  )
  return [{ messageType: 'user_query', content: 'Reply with just OK.' }, failure].map((row, i) => ({
    _id: nextId(),
    sessionId: SESSION,
    orgId: ORG,
    seq: i + 1,
    ...row,
    createdAt: new Date(Date.UTC(2026, 10, 27, 18, 53, i)),
  }))
}

/** The access route: these rows name no record, so nobody is asked about one. */
const stubAccess = (): void => {
  sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
    if (String(this.uri).endsWith('/chat/records/access')) {
      return { statusCode: 200, data: { accessible: [], accessibleUrls: [], unmatchedUrls: [] }, msg: '' } as any
    }
    return { statusCode: 200, data: {}, msg: '' } as any
  })
}

const stubChat = (rows: any[]): void => {
  sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
  sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false)
  sinon.stub(Users, 'find').returns(chain([]))
  sinon.stub(ChatSession, 'findOne').returns(
    chain({
      _id: SESSION,
      orgId: ORG,
      userId: OWNER,
      initiator: OWNER,
      title: 't',
      isShared: true,
      sharedWith: [{ userId: READER, accessLevel: 'read' }],
      status: 'Failed',
      failReason: REASON,
      failedRunBy: OWNER,
      conversationErrors: [{ message: REASON, errorType: 'MODEL_BUDGET_EXCEEDED', requestedBy: OWNER, timestamp: new Date() }],
    }),
  )
  sinon.stub(ChatSession, 'find').returns(chain([]))
  sinon.stub(ChatSessionMessage, 'countDocuments').resolves(rows.length)
  sinon.stub(ChatSessionMessage, 'find').callsFake((filter: any = {}) => {
    const ids: string[] | undefined = filter?._id?.$in?.map(String)
    const seq = filter?.seq ?? {}
    return chain(
      rows
        .filter((m) => (ids === undefined || ids.includes(String(m._id))) && (seq.$lte === undefined || m.seq <= seq.$lte))
        .map((m) => ({ ...m })),
    )
  })
}

const getChat = () => behindGuard(realGuards(), 'read', getConversationById(appConfig) as never, 'chat') as any

/** The whole response `userId` is sent for the chat, as it reaches them. */
const readAs = async (userId: string): Promise<any> => {
  const r: any = { status: sinon.stub(), json: sinon.stub() }
  r.status.returns(r)
  const next = sinon.stub()
  await getChat()(
    { headers: { authorization: `Bearer user-${userId}` }, body: {}, params: { conversationId: SESSION }, query: {}, user: { userId, orgId: ORG }, context: { requestId: 'r' }, on: sinon.stub() },
    r,
    next,
  )
  sinon.assert.notCalled(next)
  return JSON.parse(JSON.stringify(r.json.firstCall.args[0]))
}

const errorRowOf = (response: any) => response.conversation.messages.find((m: any) => m.messageType === 'error')

describe("another person's failed run, in a chat's reads", () => {
  beforeEach(() => resetVisibility())
  afterEach(() => {
    sinon.restore()
    resetVisibility()
  })

  for (const durable of [false, true]) {
    const path = durable ? 'the durable path' : 'the classic path'

    it(`${path}: a reader learns that it failed and never why, on the row or the chat`, async () => {
      stubChat(failedChat(durable))
      stubAccess()

      const response = await readAs(READER)
      expect(JSON.stringify(response)).to.not.contain('CANARY')
      const row = errorRowOf(response)
      expect(row.content).to.equal(FAILED_FOR_OTHERS)
      expect(row.reasonWithheld).to.equal(true)
      for (const field of ['errorCode', 'runOutcome', 'parts', 'answerHidden']) expect(row[field], field).to.satisfy((v: unknown) => v === undefined || (Array.isArray(v) && v.length === 0))
      for (const field of ['failReason', 'conversationErrors', 'failedRunBy']) expect(response.conversation, field).to.not.have.property(field)
    })

    it(`${path}: its asker keeps the reason`, async () => {
      stubChat(failedChat(durable))
      stubAccess()

      const response = await readAs(OWNER)
      const row = errorRowOf(response)
      expect(row.content).to.equal(REASON)
      expect(row.reasonWithheld).to.equal(undefined)
      expect(response.conversation.failReason).to.equal(REASON)
    })
  }

  it('the live stream says it in the same words', () => {
    expect(STREAMED_FAILED_FOR_OTHERS).to.equal(FAILED_FOR_OTHERS)
  })
})
