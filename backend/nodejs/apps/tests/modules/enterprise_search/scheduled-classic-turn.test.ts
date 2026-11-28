/**
 * ADR-SCH-02 D3: a schedule's classic firing is the chat route's own turn, run in this process against the real
 * controller, the in-memory conversation store and a fake query service.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Container } from 'inversify'
import type { NextFunction, Request, Response } from 'express'
import { createScheduledTurnPipelines } from '../../../src/modules/enterprise_search/routes/es.routes'
import { InProcessClassicTurns, TurnResponseSink, runHandlers } from '../../../src/modules/enterprise_search/utils/in-process-turn'
import { ClassicScheduleRunner } from '../../../src/modules/agent-harness/application/classic-schedule-runner'
import { RepositoryClassicConversations } from '../../../src/modules/agent-harness/adapters/classic-schedule-conversations'
import { getConversationRepository } from '../../../src/libs/repositories/conversation'
import { resolvedProxyHeaders } from '../../../src/modules/agent-harness/transport/resolved-turn-header'
import { registerUpstreams } from '../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../src/modules/tokens_manager/config/config'
import { UnauthorizedError } from '../../../src/libs/errors/http.errors'
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command'
import { HttpMethod } from '../../../src/libs/enums/http-methods.enum'
import { FakeAIBackend, InMemoryChatStore, settle } from './controller/chat-test-harness'
import { AGENT_KEY, ORG, OWNER, RUN, appConfig, finalAnswer } from './controller/streaming-flows'
import { markScheduledTurn, scheduledConversationOrigin, scheduledTurnDetachMs } from '../../../src/modules/agent-harness/transport/scheduled-turn-mark'

const MINTED = 'Bearer minted-for-alice'
// An hour on: the turn's stream is stopped at its deadline, so a deadline already past would stop it at once.
const DEADLINE = Date.now() + 3_600_000

/** The app's authentication as far as this turn needs it: the minted credential is the owner, anything else is not. */
const auth = {
  authenticate: (req: Request, _res: Response, next: NextFunction) => {
    if (req.headers.authorization !== MINTED) {
      next(new UnauthorizedError('Session expired'))
      return
    }
    ;(req as unknown as { user: Record<string, unknown> }).user = { userId: OWNER, orgId: ORG, role: 'member' }
    next()
  },
}

function container(): Container {
  const c = new Container()
  c.bind('AuthMiddleware').toConstantValue(auth)
  c.bind('AppConfig').toConstantValue(appConfig)
  return c
}

const mark = { scheduleId: 'sch-1', firingId: 'fir-1', conversationId: '0123456789abcdef01234567', title: 'Morning joke', deadlineAt: DEADLINE }

describe('ADR-SCH-02 D3: a scheduled classic turn', () => {
  let store: InMemoryChatStore
  let ai: FakeAIBackend
  let turns: InProcessClassicTurns

  beforeEach(() => {
    registerAppUpstreams(appConfig)
    store = new InMemoryChatStore()
    ai = new FakeAIBackend()
    store.install()
    ai.install()
    const pipelines = createScheduledTurnPipelines(container())
    turns = new InProcessClassicTurns(() => pipelines)
  })
  afterEach(() => {
    registerUpstreams({})
    sinon.restore()
  })

  it('a turn still going at its deadline is stopped there and its conversation saved, so the firing sees it end', async () => {
    let ended = 0
    const deadlineAt = Date.now() + 80
    const started = await turns.start({
      authorization: MINTED,
      body: { query: 'Tell me a joke', chatMode: 'agent', recordIds: [], filters: {} },
      mark: { ...mark, deadlineAt },
      ended: () => {
        ended += 1
      },
    })
    const id = (started as { conversationId: string }).conversationId
    await settle()
    ai.send('TEXT_MESSAGE_CONTENT', { delta: 'Why did the' })
    const conversations = new RepositoryClassicConversations(getConversationRepository)
    expect((await conversations.read(String(ORG), id))?.status).to.equal('in_progress')
    await new Promise((resolve) => setTimeout(resolve, 200))
    await settle(10)
    // Saved as stopped with what it wrote; the firing reads it as ended at its deadline, not cancelled by a person.
    expect((await conversations.read(String(ORG), id))?.status).to.equal('stopped')
    const runner = new ClassicScheduleRunner(turns, conversations, () => Date.now())
    expect(await runner.status(String(ORG), id, deadlineAt)).to.deep.equal({ status: 'failed', reason: 'deadline_passed' })
    expect(ended).to.equal(0)
  })

  it('opens a conversation named after the schedule, marked as its, and tells the query service it is a firing', async () => {
    let ended = 0
    const started = await turns.start({
      authorization: MINTED,
      body: { query: 'Tell me a joke', chatMode: 'agent', recordIds: [], filters: {} },
      mark,
      ended: () => {
        ended += 1
      },
    })
    expect(started).to.have.property('conversationId')
    const session = store.sessions[0]!
    expect(String(session._id)).to.equal((started as { conversationId: string }).conversationId)
    // Opened under the firing's own id, so a second start of the firing meets this conversation.
    expect(String(session._id)).to.equal(mark.conversationId)
    expect(session.get('title')).to.equal('Morning joke')
    const metadata = session.get('metadata') as Map<string, unknown>
    expect(metadata.get('origin')).to.deep.equal({
      kind: 'schedule', scheduleId: 'sch-1', firingId: 'fir-1', scheduleName: 'Morning joke',
    })
    await settle()
    const [call] = ai.streamCalls
    expect(call!.headers['x-pipeshub-schedule-firing']).to.equal('fir-1')
    expect(call!.headers['x-pipeshub-ask-deadline']).to.equal(new Date(DEADLINE).toISOString())
    expect(call!.headers['authorization']).to.equal(MINTED)
    expect(ended).to.equal(0)

    ai.send('RUN_FINISHED', finalAnswer('Why did the scheduler cross the road?'))
    ai.finish()
    await settle(10)
    expect(ended).to.equal(1)
    const conversations = new RepositoryClassicConversations(getConversationRepository)
    const runner = new ClassicScheduleRunner(turns, conversations, () => DEADLINE - 1)
    expect(await runner.status(String(ORG), String(session._id), DEADLINE)).to.deep.equal({ status: 'completed' })
    expect(await runner.status('0000000000000000000000ff', String(session._id), DEADLINE)).to.equal(null)
  })

  it('an agent schedule takes the agent chat route', async () => {
    const started = await turns.start({
      authorization: MINTED,
      agentKey: AGENT_KEY,
      body: { query: 'Summarise the roadmap', chatMode: 'auto', recordIds: [], filters: {} },
      mark,
    })
    expect(started).to.have.property('conversationId')
    await settle()
    expect(ai.streamCalls[0]!.url).to.contain(`/agent/${AGENT_KEY}/chat/stream`)
    expect(store.sessions[0]!.get('agentKey')).to.equal(AGENT_KEY)
    ai.finish()
    await settle(10)
  })

  it('a turn refused before it opened a conversation names why, and opens none', async () => {
    expect(
      await turns.start({ authorization: 'Bearer someone-else', body: { query: 'x', chatMode: 'agent' }, mark }),
    ).to.deep.equal({ refused: 'agent_forbidden' })
    expect(
      await turns.start({ authorization: MINTED, body: { query: 'x', chatMode: 'not-a-mode' }, mark }),
    ).to.deep.equal({ refused: 'turn_invalid' })
    expect(store.sessions).to.have.length(0)
  })

  it('waiting_for_you while the saved answer holds an ask; failed when the turn fails', async () => {
    const conversations = new RepositoryClassicConversations(getConversationRepository)
    let now = DEADLINE - 1
    const runner = new ClassicScheduleRunner(turns, conversations, () => now)
    const waiting = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Morning joke', sessionType: 'chat' })
    store.addMessage(waiting, { messageType: 'user_query', content: 'Make the ticket' })
    store.addMessage(waiting, {
      messageType: 'bot_response',
      content: 'Waiting for you.',
      parts: [{ type: 'tool_call', toolCallId: 'c1', toolName: 'jira_create_issue', status: 'awaiting_approval' }],
    } as never)
    expect(await runner.status(String(ORG), String(waiting._id), DEADLINE)).to.deep.equal({ status: 'waiting_for_you' })
    now = DEADLINE + 1
    expect(await runner.status(String(ORG), String(waiting._id), DEADLINE)).to.deep.equal({ status: 'failed', reason: 'ask_expired' })
    expect(await runner.status(String(ORG), String(waiting._id), DEADLINE)).to.deep.equal({ status: 'failed', reason: 'ask_expired' })

    const started = await turns.start({
      authorization: MINTED,
      body: { query: 'Tell me a joke', chatMode: 'agent', recordIds: [], filters: {} },
      mark,
    })
    await settle()
    ai.send('RUN_ERROR', { runId: RUN, message: 'model down', code: 'agent_error' })
    ai.finish()
    await settle(10)
    now = DEADLINE - 1
    const state = await runner.status(String(ORG), (started as { conversationId: string }).conversationId, DEADLINE)
    expect(state?.status).to.equal('failed')
  })

  it('a client cannot mark its own turn: scheduled-turn headers it sends are dropped', async () => {
    const req = { headers: { authorization: 'Bearer x', 'x-pipeshub-schedule-firing': 'forged', 'x-pipeshub-ask-deadline': '2099-01-01T00:00:00Z' } }
    const headers = await resolvedProxyHeaders(req as never, { conversationId: 'c1' })
    expect(headers).to.not.have.property('x-pipeshub-schedule-firing')
    expect(headers).to.not.have.property('x-pipeshub-ask-deadline')
    expect(headers['authorization']).to.equal('Bearer x')
  })

  it('the AI command sends the scheduled headers only from its own option, never from request headers', async () => {
    const forged = new AIServiceCommand({
      uri: 'http://ai.test/api/v1/chat/stream',
      method: HttpMethod.POST,
      headers: { authorization: 'Bearer x', 'x-pipeshub-schedule-firing': 'forged', 'x-pipeshub-ask-deadline': '2099-01-01T00:00:00Z' },
    })
    expect((forged as unknown as { headers: Record<string, string> }).headers).to.not.have.property('x-pipeshub-schedule-firing')
    const own = new AIServiceCommand({
      uri: 'http://ai.test/api/v1/chat/stream',
      method: HttpMethod.POST,
      headers: { authorization: 'Bearer x' },
      scheduledTurnHeaders: { 'x-pipeshub-schedule-firing': 'fir-1' },
    })
    expect((own as unknown as { headers: Record<string, string> }).headers).to.include({ 'x-pipeshub-schedule-firing': 'fir-1' })
  })

  it('runs handlers in order and stops at the first error, as the router does', async () => {
    const seen: string[] = []
    const ok = await runHandlers(
      [
        (_req, _res, next) => { seen.push('a'); next() },
        async (_req, _res, next) => { seen.push('b'); next() },
        (_req, res) => { seen.push('c'); res.end() },
      ],
      {} as Request,
      new TurnResponseSink() as unknown as Response,
    )
    expect([ok, seen]).to.deep.equal([{}, ['a', 'b', 'c']])
    const failed = await runHandlers(
      [(_req, _res, next) => next(new Error('no')), () => seen.push('never')],
      {} as Request,
      new TurnResponseSink() as unknown as Response,
    )
    expect((failed.error as Error).message).to.equal('no')
    expect(seen).to.not.include('never')
  })
})

describe('ADR-SCH-02: a scheduled turn is stopped at its firing\'s deadline', () => {
  it('its stream cap is the time left to the deadline; any other request keeps the usual cap', () => {
    const req = {} as never
    expect(scheduledTurnDetachMs(req)).to.equal(null)
    markScheduledTurn(req, { scheduleId: 's', firingId: 'f', conversationId: 'c', title: 't', deadlineAt: 10_000 })
    expect(scheduledTurnDetachMs(req, 4_000)).to.equal(6_000)
    // Already past it: stopped at once, never left running without a cap.
    expect(scheduledTurnDetachMs(req, 20_000)).to.equal(1)
  })
})

describe('ADR-SCH-02: a conversation\'s schedule origin, as either document store keeps its metadata', () => {
  const origin = { kind: 'schedule', scheduleId: 'sch-1', firingId: 'fir-1', scheduleName: 'Morning joke' }
  it('reads it from a Mongo Map and a PostgreSQL object alike', () => {
    expect(scheduledConversationOrigin(new Map([['origin', origin]]))).to.deep.equal(origin)
    expect(scheduledConversationOrigin({ origin })).to.deep.equal(origin)
    expect(scheduledConversationOrigin({ origin: { ...origin, scheduleName: 3 } })).to.deep.equal({ ...origin, scheduleName: '' })
  })
  it('is none for any other metadata: no origin, another kind, or ids that are not text', () => {
    for (const metadata of [undefined, {}, { origin: null }, { origin: 'schedule' }, { origin: { ...origin, kind: 'chat' } }, { origin: { ...origin, firingId: 7 } }])
      expect(scheduledConversationOrigin(metadata as never)).to.equal(null)
  })
})
