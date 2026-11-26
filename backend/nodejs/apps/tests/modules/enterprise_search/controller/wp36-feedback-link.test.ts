/**
 * WP-36 G-15 FB-2..FB-4: the conversation module's thumbs route stores the run
 * and agent version with the rating, and tells the rated-answer sink of every
 * rating: the validated body, the answer and the question on demand.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as controller from '../../../../src/modules/enterprise_search/controller/es_controller'
import type { RatedAnswer } from '../../../../src/modules/agent-harness/application/run-feedback'
import { FakeSSEResponse, InMemoryChatStore, oid, settle } from './chat-test-harness'

const ORG = oid()
const OWNER = oid()
const AGENT_KEY = 'agent-1'

function setup() {
  const store = new InMemoryChatStore()
  store.install()
  const chat = store.addSession({ orgId: ORG, userId: OWNER, initiator: OWNER, title: 'Q3', sessionType: 'chat' })
  store.addMessage(chat, { messageType: 'user_query', content: 'When does Q3 close?' })
  const bot = store.addMessage(chat, { messageType: 'bot_response', content: 'Q3 closes on 31 August.', runId: 'r-chat' })
  const legacy = store.addMessage(chat, { messageType: 'bot_response', content: 'An answer before runs.' })
  const agentChat = store.addSession({
    orgId: ORG,
    userId: OWNER,
    initiator: OWNER,
    title: 'Agent',
    sessionType: 'agent',
    agentKey: AGENT_KEY,
    conversationSource: 'agent_chat',
  })
  store.addMessage(agentChat, { messageType: 'user_query', content: 'Summarise the roadmap' })
  const agentBot = store.addMessage(agentChat, { messageType: 'bot_response', content: 'Three themes.', runId: 'r-agent' })
  return { store, chatId: String(chat._id), botId: String(bot._id), legacyId: String(legacy._id), agentChatId: String(agentChat._id), agentBotId: String(agentBot._id) }
}

function runs() {
  const recorded: RatedAnswer[] = []
  const links: Record<string, { runId: string; agentKey: string | null; agentVersion: number | null }> = {
    'r-chat': { runId: 'r-chat', agentKey: null, agentVersion: null },
    'r-agent': { runId: 'r-agent', agentKey: AGENT_KEY, agentVersion: 7 },
  }
  return {
    recorded,
    port: {
      link: async (_orgId: string, _sessionId: string, runId: string | undefined) => (runId === undefined ? null : (links[runId] ?? null)),
      record: async (rating: RatedAnswer) => {
        recorded.push(rating)
      },
    },
  }
}

const request = (params: Record<string, string>, body: Record<string, unknown>): never =>
  ({
    headers: {},
    params,
    body,
    query: {},
    user: { userId: OWNER, orgId: ORG, email: 'owner@example.com' },
    context: { requestId: 'req-wp36' },
  }) as never

async function call(handler: unknown, req: never) {
  const res = new FakeSSEResponse()
  const next = sinon.stub()
  await (handler as (req: never, res: never, next: never) => Promise<unknown>)(req, res as never, next as never)
  await settle()
  return { status: res.statusCode, body: res.jsonBody as { feedback: Record<string, unknown> }, error: next.firstCall?.args[0] as Error | undefined }
}

describe('WP-36 G-15 feedback is linked to its run and agent version (FB)', () => {
  afterEach(() => sinon.restore())

  it('FB-2: a rating of a durable answer stores the run with it; the sink is told of it with the body, the answer and the question', async () => {
    const s = setup()
    const { port, recorded } = runs()
    const out = await call(
      controller.updateFeedbackWith(port),
      request(
        { conversationId: s.chatId, messageId: s.botId },
        { isHelpful: false, categories: ['incorrect_information'], comments: { negative: 'It is 30 September.' } },
      ),
    )
    expect(out.error, String(out.error)).to.equal(undefined)
    expect(out.status).to.equal(200)
    const [stored] = s.store.messagesOf(s.chatId)[1]!.feedback as Array<Record<string, unknown>>
    expect(stored).to.include({ runId: 'r-chat', isHelpful: false })
    expect(stored).to.not.have.property('agentVersion')
    expect(recorded).to.have.length(1)
    expect(recorded[0]).to.deep.include({
      orgId: String(ORG),
      userId: String(OWNER),
      sessionId: s.chatId,
      messageId: s.botId,
      link: { runId: 'r-chat', agentKey: null, agentVersion: null },
      answer: 'Q3 closes on 31 August.',
    })
    expect(recorded[0]!.body).to.deep.include({ isHelpful: false, comments: { negative: 'It is 30 September.' } })
    expect(await recorded[0]!.question()).to.equal('When does Q3 close?')
  })

  it("FB-3: an agent's answer records the agent and the version it ran", async () => {
    const s = setup()
    const { port, recorded } = runs()
    const out = await call(
      controller.updateAgentFeedbackWith(port),
      request({ agentKey: AGENT_KEY, conversationId: s.agentChatId, messageId: s.agentBotId }, { isHelpful: false }),
    )
    expect(out.status, String(out.error)).to.equal(200)
    const [stored] = s.store.messagesOf(s.agentChatId)[1]!.feedback as Array<Record<string, unknown>>
    expect(stored).to.include({ runId: 'r-agent', agentKey: AGENT_KEY, agentVersion: 7 })
    expect(recorded[0]).to.deep.include({ link: { runId: 'r-agent', agentKey: AGENT_KEY, agentVersion: 7 }, answer: 'Three themes.' })
  })

  it('FB-4: an answer no run wrote is rated as before, and the sink is told with no link', async () => {
    const s = setup()
    const { port, recorded } = runs()
    const out = await call(
      controller.updateFeedbackWith(port),
      request({ conversationId: s.chatId, messageId: s.legacyId }, { isHelpful: false }),
    )
    expect(out.status, String(out.error)).to.equal(200)
    const [stored] = s.store.messagesOf(s.chatId)[2]!.feedback as Array<Record<string, unknown>>
    expect(stored).to.not.have.property('runId')
    expect(recorded[0]).to.include({ link: null })
    // The default handler, without the harness, still rates.
    const plain = await call(controller.updateFeedback, request({ conversationId: s.chatId, messageId: s.botId }, { isHelpful: true }))
    expect(plain.status).to.equal(200)
  })
})
