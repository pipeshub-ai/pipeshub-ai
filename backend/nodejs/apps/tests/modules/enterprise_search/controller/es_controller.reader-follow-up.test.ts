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
import { redactConversationFor, viewerOf } from '../../../../src/modules/enterprise_search/utils/viewer-redaction'
import { behindGuard, realGuards } from '../helpers/guarded-chat'

/**
 * A shared chat as each of its readers reads it in the default build, whose answers carry no label: the owner asks
 * about two collections the readers can't open, then asks the model to repeat what it said without searching. The
 * repeat cites nothing, but its run was given the two hidden answers as history, so it is hidden from every reader who
 * can't see them, on whichever page it is read. The access route and the store are stubbed; the reads run for real.
 */

const ORG = 'bbbbbbbbbbbbbbbbbbbbbbbb'
const SESSION = 'dddddddddddddddddddddddd'
const OWNER = 'aaaaaaaaaaaaaaaaaaaaaaaa'
/** Can open the open collection and the first closed one. */
const READER_A = 'a1a1a1a1a1a1a1a1a1a1a1a1'
/** Can open the open collection only. */
const READER_B = 'b1b1b1b1b1b1b1b1b1b1b1b1'
/** Can open everything the owner can. */
const READER_C = 'c1c1c1c1c1c1c1c1c1c1c1c1'

const REC_OPEN = 'rec-open'
const REC_FIRST = 'rec-first-closed'
const REC_SECOND = 'rec-second-closed'
const FIRST_SECRET = 'CANARY_FIRST_4417'
const SECOND_SECRET = 'CANARY_SECOND_8823'

const OPENS: Record<string, string[]> = {
  [OWNER]: [REC_OPEN, REC_FIRST, REC_SECOND],
  [READER_A]: [REC_OPEN, REC_FIRST],
  [READER_B]: [REC_OPEN],
  [READER_C]: [REC_OPEN, REC_FIRST, REC_SECOND],
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
    q.skip = sinon.stub().callsFake((n: number) => ((skip = n), q))
    q.limit = sinon.stub().callsFake((n: number) => ((limit = n), q))
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

const citation = (recordId: string, content: string) => ({
  citationId: { _id: nextId(), content, chunkIndex: 1, metadata: { recordId, recordName: `${recordId}.pdf`, orgId: ORG } },
})

const question = (content: string) => ({ messageType: 'user_query', content, authorUserId: OWNER })
const answer = (content: string, citations: unknown[] = []) => ({ messageType: 'bot_response', content, citations, requestedBy: OWNER })

/** The live case: an open answer, two answers on the closed collections, then a repeat that cites nothing. */
const repeatChat = (): any[] =>
  [
    question('What does the open handbook say about launches?'),
    answer('Launches go out on Tuesdays [1].', [citation(REC_OPEN, 'Tuesdays')]),
    question('What is the code word in the first closed collection?'),
    answer(`The code word is ${FIRST_SECRET} [1].`, [citation(REC_FIRST, FIRST_SECRET)]),
    question('And in the second closed collection?'),
    answer(`It is ${SECOND_SECRET} [1].`, [citation(REC_SECOND, SECOND_SECRET)]),
    question('Without searching, repeat what you told me earlier.'),
    answer(`You were told ${FIRST_SECRET} and ${SECOND_SECRET}.`),
  ].map((row, i) => ({ _id: nextId(), sessionId: SESSION, orgId: ORG, seq: i + 1, createdAt: new Date(Date.UTC(2026, 10, 27, 10, i)), ...row }))

/** The access route, answering for whoever the scoped token names. */
const stubAccess = (down = false): void => {
  sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(async function (this: any) {
    if (String(this.uri).endsWith('/chat/records/access')) {
      if (down) throw new Error('access service down')
      // The command keeps its headers in lower case.
      const token = String(this.headers?.authorization ?? '').replace(/^Bearer /, '')
      const userId = (jwt.decode(token) as { userId?: string } | null)?.userId ?? ''
      const body = typeof this.body === 'string' ? JSON.parse(this.body) : (this.body ?? {})
      const asked: string[] = body.recordIds ?? []
      return { statusCode: 200, data: { accessible: asked.filter((id) => (OPENS[userId] ?? []).includes(id)), accessibleUrls: [], unmatchedUrls: [] }, msg: '' } as any
    }
    // The page's other calls (the file permission syncs) answer nothing of interest.
    return { statusCode: 200, data: {}, msg: '' } as any
  })
}

const stubChat = (rows: any[]): void => {
  sinon.stub(ProjectService, 'getAccessibleProjectIds').resolves([])
  sinon.stub(ProjectService, 'orgHasTeamMembers').resolves(false)
  sinon.stub(Users, 'find').returns(chain([]))
  const sharedWith = [READER_A, READER_B, READER_C].map((userId) => ({ userId, accessLevel: 'read' }))
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

describe('a follow-up built on answers hidden from a reader, in a build whose answers carry no label', () => {
  beforeEach(() => resetVisibility())
  afterEach(() => {
    sinon.restore()
    resetVisibility()
  })

  it('stays hidden from every reader who cannot see the answers it repeats', async () => {
    stubAccess()
    stubChat(repeatChat())

    for (const reader of [READER_A, READER_B]) {
      const messages = await readAs(reader)
      const wire = JSON.stringify(messages)
      expect(wire, `what ${reader} is sent`).to.not.contain(SECOND_SECRET)
      if (reader === READER_B) expect(wire).to.not.contain(FIRST_SECRET)
      const [open, , , repeat] = answersOf(messages)
      expect(open.content, 'an answer before any hidden one is still shown').to.equal('Launches go out on Tuesdays [1].')
      expect(repeat.answerHidden, 'the repeat is hidden').to.deep.equal({ redacted: true, reason: 'no_access' })
      expect(repeat.content).to.not.contain('CANARY')
    }
  })

  it('stays hidden when the answers it repeats are on an earlier page', async () => {
    stubAccess()
    stubChat(repeatChat())

    // The newest page of two rows: the repeat and its question, the closed answers a page back.
    const messages = await readAs(READER_B, { page: '1', limit: '2' })
    expect(messages.map((m: any) => m.messageType)).to.deep.equal(['user_query', 'bot_response'])
    expect(JSON.stringify(messages)).to.not.contain('CANARY')
    expect(messages[1].answerHidden).to.deep.equal({ redacted: true, reason: 'no_access' })
  })

  it('is shown to a reader who can see every answer before it, and to the owner', async () => {
    stubAccess()
    stubChat(repeatChat())

    for (const who of [READER_C, OWNER]) {
      const repeat = answersOf(await readAs(who))[3]
      expect(repeat.answerHidden, who).to.equal(undefined)
      expect(repeat.content).to.equal(`You were told ${FIRST_SECRET} and ${SECOND_SECRET}.`)
    }
  })

  it('a reader is told the answers are hidden because of sources they cannot open, not ones they lost', async () => {
    stubAccess()
    stubChat(repeatChat())

    const [, first, second] = answersOf(await readAs(READER_B))
    for (const hidden of [first, second]) {
      expect(hidden.answerHidden).to.deep.equal({ redacted: true, reason: 'no_access' })
      expect(hidden.content).to.equal("You can't see this answer. It used sources you can't open.")
    }
    // The owner, who had them, lost nothing here and sees them.
    const owned = answersOf(await readAs(OWNER))
    expect(owned[1].content).to.contain(FIRST_SECRET)
  })

  it('stays hidden in a frame whose rows name no place or chat, judged among themselves', async () => {
    stubAccess()
    // As a frame of rows nobody stored yet: no id, chat or place.
    const rows = repeatChat().map((row) => {
      const unplaced = { ...row }
      for (const field of ['_id', 'sessionId', 'seq', 'orgId']) delete unplaced[field]
      return unplaced
    })

    const out: any = await redactConversationFor(appConfig, viewerOf(READER_B, ORG), { initiator: OWNER, messages: rows })
    expect(JSON.stringify(out.messages)).to.not.contain('CANARY')
    expect(answersOf(out.messages)[3].answerHidden).to.deep.equal({ redacted: true, reason: 'no_access' })
  })

  it('stays hidden, as unconfirmed, while the access check cannot answer', async () => {
    stubAccess(true)
    stubChat(repeatChat())

    const messages = await readAs(READER_C)
    expect(JSON.stringify(messages)).to.not.contain('CANARY')
    expect(answersOf(messages)[3].answerHidden).to.deep.equal({ redacted: true, reason: 'access_check_unavailable' })
  })
})

/**
 * Another person's answer that a reader may see, in the same build: its text, and nothing of the asker's run. These are
 * the rows the live test read as each reader, shaped by the per-record view (#6619's cut) and the reader check together.
 */
describe("another person's answer a reader may see, in a build whose answers carry no label", () => {
  beforeEach(() => resetVisibility())
  afterEach(() => {
    sinon.restore()
    resetVisibility()
  })

  const RUN = 'owner-run-0001'
  const EXPIRES = 1_900_000_000_000
  const LINE = 'Waiting for your approval to run create_note on Notes.'
  const card = {
    type: 'tool_call',
    toolCallId: 'call-note',
    toolName: 'mcp_notes_create_note',
    args: JSON.stringify({ title: 'CARD-ARGUMENT' }),
    status: 'awaiting_approval',
    approval: { approvalId: 'ap-1', serverName: 'Notes', toolName: 'create_note', expiresAt: EXPIRES, arguments: { title: 'CARD-ARGUMENT' } },
  }
  const STUB = { type: 'tool_call', toolCallId: 'call-note', stub: true, approval: { approvalId: 'ap-1', serverName: 'Notes', toolName: 'create_note', expiresAt: EXPIRES } }

  const plainAnswer = () => answer('Launches go out on Tuesdays.')
  const chat = (...answers: Array<Record<string, unknown>>): any[] =>
    answers
      .flatMap((a, i) => [question(`Question ${i + 1}`), a])
      .map((row, i) => ({ _id: nextId(), sessionId: SESSION, orgId: ORG, seq: i + 1, createdAt: new Date(Date.UTC(2026, 10, 27, 11, i)), ...row }))

  /** An answer with the steps of its run, as the stream stores it. */
  const stepped = {
    ...plainAnswer(),
    runId: RUN,
    reasoning: [{ content: 'thinking prose' }],
    tools: [{ toolName: 'read', toolResult: { title: 'TOOL-OUTPUT' } }],
    parts: [
      { type: 'text', content: 'Let me look.', runId: RUN },
      { type: 'tool_call', toolCallId: 'c1', toolName: 'read', status: 'completed', resultPreview: 'TOOL-OUTPUT', runId: RUN },
      { type: 'tool_call', toolCallId: 'c2', toolName: 'read', status: 'completed', resultPreview: 'TOOL-OUTPUT', runId: RUN },
      { type: 'text', content: 'Launches go out on Tuesdays.', isFinal: true, runId: RUN },
    ],
    unavailableTools: [{ kind: 'toolset', instanceId: 'inst-1', displayName: 'Tracker', name: 'tracker', problem: 'not_configured', userCanConnect: true, needed: true }],
  }

  it('comes without its steps, says so, and names no run or tool connection of the asker', async () => {
    stubAccess()
    stubChat(chat(stepped))

    const [shown] = answersOf(await readAs(READER_B))
    expect(shown.content).to.equal('Launches go out on Tuesdays.')
    expect(shown.activityWithheld).to.equal(true)
    expect(shown.parts).to.deep.equal([{ type: 'text', content: 'Launches go out on Tuesdays.', isFinal: true }])
    expect(shown.reasoning).to.deep.equal([])
    expect(shown.tools).to.deep.equal([])
    expect(shown).to.not.have.property('unavailableTools')
    const wire = JSON.stringify(shown)
    for (const leaked of ['TOOL-OUTPUT', 'thinking prose', 'inst-1', 'Tracker']) expect(wire).to.not.contain(leaked)
    expect(wire, 'no run of the asker, on the row or in a part').to.not.contain(RUN)
  })

  it("names none of the asker's runs anywhere in the read: rows, ratings, job cards, questions and tool rows", async () => {
    const CHILD = 'owner-child-run-0002'
    const TASK = 'owner-engine-task-0003'
    stubAccess()
    const rows = chat(
      {
        ...plainAnswer(),
        runId: RUN,
        feedback: [{ isHelpful: true, runId: RUN }],
        parts: [
          { type: 'sub_agent', runId: CHILD, parentRunId: RUN, roleName: 'researcher', parts: [{ type: 'text', content: 'child notes', runId: CHILD }] },
          { type: 'engine_task', taskId: TASK, runId: RUN },
          { type: 'text', content: 'Launches go out on Tuesdays.', isFinal: true, runId: RUN },
        ],
      },
      // An answer the reader may not see: it cites a record only the owner opens.
      { ...answer('Hidden [1].', [citation(REC_SECOND, 'Hidden')]), runId: RUN, feedback: [{ isHelpful: false, runId: RUN }] },
    )
    // The question a run is still answering names it, and so does a tool row of that run.
    rows[0] = { ...rows[0], runId: RUN }
    rows.push({ _id: nextId(), sessionId: SESSION, orgId: ORG, seq: rows.length + 1, createdAt: new Date(Date.UTC(2026, 10, 27, 12)), messageType: 'tool_call', content: '', requestedBy: OWNER, runId: RUN, tools: [] })
    stubChat(rows)

    const messages = await readAs(READER_B)
    const [shown, hidden] = answersOf(messages)
    expect(shown.parts.filter((p: { type: string }) => p.type === 'sub_agent')).to.deep.equal([{ type: 'sub_agent', roleName: 'researcher', parts: [] }])
    expect(hidden.answerHidden).to.not.equal(undefined)
    const wire = JSON.stringify(messages)
    for (const run of [RUN, CHILD, TASK]) expect(wire, run).to.not.contain(run)
    // Their asker still follows their own runs.
    expect(JSON.stringify(await readAs(OWNER))).to.contain(RUN).and.to.contain(CHILD)
  })

  it('its asker gets it as stored', async () => {
    stubAccess()
    stubChat(chat(stepped))

    const [own] = answersOf(await readAs(OWNER))
    expect(own).to.not.have.property('activityWithheld')
    expect(own.parts).to.have.length(4)
    expect(own.unavailableTools).to.have.length(1)
  })

  it('an answer with nothing left out does not say anything was', async () => {
    stubAccess()
    stubChat(chat({ ...plainAnswer(), parts: [{ type: 'text', content: 'Launches go out on Tuesdays.', isFinal: true }] }))

    const [shown] = answersOf(await readAs(READER_B))
    expect(shown).to.not.have.property('activityWithheld')
  })

  it("a run stopped on its asker's approval card reads as one line on how the card stands, never the asker's waiting line", async () => {
    stubAccess()
    const waiting = {
      ...answer(`I will write it down.\n\n${LINE}`),
      parts: [{ type: 'text', content: 'Let me write it.' }, card, { type: 'text', content: `I will write it down.\n\n${LINE}`, isFinal: true }],
    }
    stubChat(chat(waiting))

    const [shown] = answersOf(await readAs(READER_B))
    expect(shown.content).to.equal('I will write it down.')
    expect(shown.parts).to.deep.equal([{ ...STUB, status: 'awaiting_approval' }, { type: 'text', content: 'I will write it down.', isFinal: true }])
    expect(JSON.stringify(shown)).to.not.contain('CARD-ARGUMENT').and.not.contain('Waiting for your approval')
  })

  it('once its asker allowed the call, the line says so', async () => {
    stubAccess()
    const answered = {
      ...answer(LINE),
      parts: [card, { type: 'text', content: LINE, isFinal: true }],
      approvalAnswered: { decision: 'approved', ran: true, at: new Date(EXPIRES - 60_000) },
    }
    stubChat(chat(answered))

    const [shown] = answersOf(await readAs(READER_B))
    expect(shown.content).to.equal('')
    expect(shown.parts).to.deep.equal([{ ...STUB, status: 'approved' }])
  })
})

/**
 * The live case once a later run in a chat others read is given the earlier answers only: the owner's first answer
 * cites the open record although its search also returned a closed one. The second, given her answers and none of
 * that search, has nothing of the closed record to state. The third searches again, finds the closed record and cites
 * it; the fourth cites the open record again.
 */
const answersOnlyChat = (): any[] =>
  [
    question('What does our handbook say?'),
    answer('Launches go out on Tuesdays [1].', [citation(REC_OPEN, 'Tuesdays')]),
    question('What did the other result say?'),
    answer('I would have to search again to say.'),
    question('Search again, then.'),
    answer(`The code word is ${FIRST_SECRET} [1].`, [citation(REC_FIRST, FIRST_SECRET)]),
    question('And the open handbook?'),
    answer('Launches go out on Tuesdays [1].', [citation(REC_OPEN, 'Tuesdays')]),
  ].map((row, i) => ({ _id: nextId(), sessionId: SESSION, orgId: ORG, seq: i + 1, createdAt: new Date(Date.UTC(2026, 10, 27, 11, i)), ...row }))

describe('a chat others read, its later answers given the earlier answers only', () => {
  beforeEach(() => resetVisibility())
  afterEach(() => {
    sinon.restore()
    resetVisibility()
  })

  it('a reader sees each answer that states nothing hidden, and loses one only once it cites what they cannot open', async () => {
    stubAccess()
    stubChat(answersOnlyChat())

    const messages = await readAs(READER_B)
    expect(answersOf(messages).map((m: any) => m.answerHidden)).to.deep.equal([
      undefined,
      undefined,
      { redacted: true, reason: 'no_access' },
      // After an answer hidden from them, as the no-label rule has it.
      { redacted: true, reason: 'no_access' },
    ])
    expect(JSON.stringify(messages)).to.not.contain(FIRST_SECRET)
    // A reader who can open the record the third answer cites reads all four.
    expect(answersOf(await readAs(READER_A)).map((m: any) => m.answerHidden)).to.deep.equal([undefined, undefined, undefined, undefined])
  })
})
