import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { getConversationById } from '../../../../src/modules/enterprise_search/controller/es_controller'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { ProjectService } from '../../../../src/modules/projects/services/project.service'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { resetVisibility } from '../../../../src/modules/enterprise_search/services/conversation/privacy/visibility'
import { behindGuard, realGuards } from '../helpers/guarded-chat'

/**
 * A durable chat (agent harness on) as each of its readers reads it in the default build, where no answer is judged by
 * a label: the owner's first answers searched and cited records some readers can't open; the next ones reuse what those
 * searches returned, without searching or citing again, since a durable run's model sees its asker's earlier runs as they
 * were, tool results included. Such an answer cites nothing, but it is hidden from every reader an earlier answer is
 * hidden from. The rows are as the harness stores them: nothing on the owner's names who asked, a collaborator's carry
 * its sender, and each answer keeps its run and its steps. The access route and the store are stubbed; the reads run
 * for real.
 */

const ORG = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const SESSION = 'dddddddddddddddddddddddd'
const OWNER = 'aaaaaaaaaaaaaaaaaaaaaaaa'
/** Can open the open collection only. */
const READER_B = 'b1b1b1b1b1b1b1b1b1b1b1b1'
/** Can open everything the owner can. */
const READER_C = 'c1c1c1c1c1c1c1c1c1c1c1c1'
/** A `write` recipient who sent a durable turn of their own; can open the open collection only. */
const COLLABORATOR = 'e1e1e1e1e1e1e1e1e1e1e1e1'

const REC_OPEN = 'rec-open'
const REC_CLOSED = 'rec-closed'
const CLOSED_WORD = 'CANARY_CLOSED_6120'
const REUSED_WORD = 'CANARY_REUSED_5521'
const AGAIN_WORD = 'CANARY_AGAIN_7734'
const STEP_TEXT = 'CANARY-STEP-RESULT'

const OPENS: Record<string, string[]> = {
  [OWNER]: [REC_OPEN, REC_CLOSED],
  [READER_B]: [REC_OPEN],
  [READER_C]: [REC_OPEN, REC_CLOSED],
  [COLLABORATOR]: [REC_OPEN],
}

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

/** `ChatSessionMessage.find` as the store answers it: ids and a `seq` bound, the order a query asks for, then its window. */
const storeReads = (rows: any[]): void => {
  ;(ChatSessionMessage.find as sinon.SinonStub).callsFake((filter: any = {}) => {
    let skip = 0
    let limit: number | undefined
    let desc = false
    const q: any = chain(undefined)
    q.sort = sinon.stub().callsFake((by: Record<string, number>) => ((desc = by?.seq === -1), q))
    q.skip = sinon.stub().callsFake((k: number) => ((skip = k), q))
    q.limit = sinon.stub().callsFake((k: number) => ((limit = k), q))
    const value = () => {
      const seq = filter?.seq ?? {}
      const ids: string[] | undefined = filter?._id?.$in?.map(String)
      const kept = rows.filter(
        (m) =>
          (ids === undefined || ids.includes(String(m._id))) &&
          (seq.$lte === undefined || m.seq <= seq.$lte) &&
          (seq.$lt === undefined || m.seq < seq.$lt) &&
          (seq.$gt === undefined || m.seq > seq.$gt),
      )
      const ordered = desc ? [...kept].reverse() : kept
      return ordered.slice(skip, limit === undefined ? undefined : skip + limit).map((m) => ({ ...m }))
    }
    q.exec = sinon.stub().callsFake(async () => value())
    q.then = (ok: (v: unknown) => unknown, bad?: (e: unknown) => unknown) => Promise.resolve(value()).then(ok, bad)
    return q
  })
}

let n = 0
const nextId = (): string => (++n).toString(16).padStart(24, '0')

const cite = (recordId: string, content: string) => ({
  citationId: { _id: nextId(), content, chunkIndex: 1, metadata: { recordId, recordName: `${recordId}.md`, orgId: ORG } },
})

/** A durable question as the harness stores it: a collaborator's names its sender, the owner's names nobody. */
const question = (content: string, senderId?: string) => ({ messageType: 'user_query', content, ...(senderId && { senderId }) })

/** A durable answer as the harness stores it: its run, its steps (a search's result among them) and its citations. */
const answer = (content: string, citations: unknown[] = [], senderId?: string) => {
  const runId = `run-${nextId()}`
  return {
    messageType: 'bot_response',
    content,
    citations,
    runId,
    labelVer: nextId(),
    parts: [
      { type: 'tool_call', toolCallId: `call-${runId}`, toolName: 'knowledgegraph__search', status: 'completed', runId, resultPreview: STEP_TEXT },
      { type: 'text', content, isFinal: true, runId },
    ],
    ...(senderId && { senderId }),
  }
}

/** Rows of the chat in order from its `after`-th place on, a minute apart. */
const placed = (rows: object[], after = 0): any[] =>
  rows.map((row, i) => ({ _id: nextId(), sessionId: SESSION, orgId: ORG, seq: after + i + 1, createdAt: new Date(Date.UTC(2026, 10, 27, 18, after + i)), ...row }))

/**
 * The owner's chat: an answer that cites the open record, one that searched the closed collection and cites it, then
 * two follow-ups that answer from that search's results without a tool call or a citation.
 */
const durableChat = (): any[] =>
  placed([
    question('What is the meeting room for the open project?'),
    answer('Room 4 [1].', [cite(REC_OPEN, 'Room 4')]),
    question('What is the launch code word for the closed project?'),
    answer(`It is ${CLOSED_WORD} [1].`, [cite(REC_CLOSED, CLOSED_WORD)]),
    question('And its vault phrase?'),
    answer(`The vault phrase is ${REUSED_WORD}.`),
    question('And its signal word?'),
    answer(`The signal word is ${AGAIN_WORD}.`),
  ])

const stubAccess = (): void => {
  sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
    if (String(this.uri).endsWith('/chat/records/access')) {
      // The command keeps its headers in lower case.
      const token = String(this.headers?.authorization ?? '').replace(/^Bearer /, '')
      const userId = (jwt.decode(token) as { userId?: string } | null)?.userId ?? ''
      const body = typeof this.body === 'string' ? JSON.parse(this.body) : (this.body ?? {})
      const asked: string[] = body.recordIds ?? []
      return { statusCode: 200, data: { accessible: asked.filter((id) => (OPENS[userId] ?? []).includes(id)), accessibleUrls: [], unmatchedUrls: [] }, msg: '' } as any
    }
    return { statusCode: 200, data: {}, msg: '' } as any
  })
}

const stubChat = (rows: any[]): void => {
  sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
  sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false)
  sinon.stub(Users, 'find').returns(chain([]))
  const sharedWith = [
    ...[READER_B, READER_C].map((userId) => ({ userId, accessLevel: 'read' })),
    { userId: COLLABORATOR, accessLevel: 'write' },
  ]
  sinon.stub(ChatSession, 'findOne').returns(
    chain({ _id: SESSION, orgId: ORG, userId: OWNER, initiator: OWNER, title: 't', isShared: true, sharedWith, status: 'complete' }),
  )
  sinon.stub(ChatSession, 'find').returns(chain([]))
  sinon.stub(ChatSessionMessage, 'countDocuments').resolves(rows.length)
  sinon.stub(ChatSessionMessage, 'find')
  storeReads(rows)
}

const getChat = () => behindGuard(realGuards(), 'read', getConversationById(appConfig) as never, 'chat') as any

/** The chat's rows as `userId` reads them, one page of `limit` rows (the newest first page when not given). */
const readAs = async (userId: string, query: Record<string, string> = {}): Promise<any[]> => {
  const r: any = { status: sinon.stub(), json: sinon.stub() }
  r.status.returns(r)
  const next = sinon.stub()
  await getChat()(
    { headers: { authorization: 'Bearer t' }, body: {}, params: { conversationId: SESSION }, query, user: { userId, orgId: ORG }, context: { requestId: 'r' }, on: sinon.stub() },
    r,
    next,
  )
  sinon.assert.notCalled(next)
  return r.json.firstCall.args[0].conversation.messages
}

const answersOf = (messages: any[]) => messages.filter((m) => m.messageType === 'bot_response')
const HIDDEN = { redacted: true, reason: 'no_access' }

describe('durable follow-ups that reuse an earlier search without citing it, in a build whose answers carry no label', () => {
  beforeEach(() => resetVisibility())
  afterEach(() => {
    sinon.restore()
    resetVisibility()
  })

  it('are hidden from a reader the earlier answer is hidden from, with no word of it or of any step in their read', async () => {
    stubAccess()
    stubChat(durableChat())

    const messages = await readAs(READER_B)
    expect(JSON.stringify(messages)).to.not.contain('CANARY')
    const [open, closed, reused, again] = answersOf(messages)
    expect(open.content, 'an answer before any hidden one is still shown').to.equal('Room 4 [1].')
    expect(closed.answerHidden).to.deep.equal(HIDDEN)
    for (const follow of [reused, again]) {
      expect(follow.answerHidden).to.deep.equal(HIDDEN)
      expect(follow.content).to.equal("You can't see this answer. It used sources you can't open.")
    }
  })

  it('stay hidden when the answer they reuse is on an earlier page', async () => {
    stubAccess()
    stubChat(durableChat())

    // The newest page of two rows: the last follow-up and its question, the closed answer two turns back.
    const messages = await readAs(READER_B, { page: '1', limit: '2' })
    expect(messages.map((m: any) => m.messageType)).to.deep.equal(['user_query', 'bot_response'])
    expect(JSON.stringify(messages)).to.not.contain('CANARY')
    expect(messages[1].answerHidden).to.deep.equal(HIDDEN)
  })

  it('are shown, text only, to a reader who can see every answer before them; their asker reads them whole', async () => {
    stubAccess()
    stubChat(durableChat())

    const forC = answersOf(await readAs(READER_C))
    expect(forC.map((a) => a.content)).to.deep.equal(['Room 4 [1].', `It is ${CLOSED_WORD} [1].`, `The vault phrase is ${REUSED_WORD}.`, `The signal word is ${AGAIN_WORD}.`])
    for (const a of forC) expect(JSON.stringify(a), 'another person\'s steps').to.not.contain(STEP_TEXT)
    const own = answersOf(await readAs(OWNER))
    expect(own.map((a) => a.answerHidden)).to.deep.equal([undefined, undefined, undefined, undefined])
    expect(JSON.stringify(own[2])).to.contain(STEP_TEXT)
  })

  it("a collaborator's own durable turn is theirs after an answer hidden from them, and hidden from a reader it follows one for", async () => {
    const chat = durableChat()
    const theirs = placed(
      [question('Anything else on the open project?', COLLABORATOR), answer('The mascot is a lighthouse [1].', [cite(REC_OPEN, 'lighthouse')], COLLABORATOR)],
      chat.length,
    )
    stubAccess()
    stubChat([...chat, ...theirs])

    // The harness names a collaborator's turn by its sender alone: theirs, though an answer before it is hidden from them.
    const own = answersOf(await readAs(COLLABORATOR)).at(-1)
    expect(own.answerHidden).to.equal(undefined)
    expect(own.content).to.equal('The mascot is a lighthouse [1].')
    expect(JSON.stringify(own)).to.contain(STEP_TEXT)
    // The owner reads it as a reader does, with nothing before it hidden from them, so without its steps.
    const forOwner = answersOf(await readAs(OWNER)).at(-1)
    expect(forOwner.content).to.equal('The mascot is a lighthouse [1].')
    expect(JSON.stringify(forOwner)).to.not.contain(STEP_TEXT)
    expect(answersOf(await readAs(READER_B)).at(-1).answerHidden).to.deep.equal(HIDDEN)
  })
})
